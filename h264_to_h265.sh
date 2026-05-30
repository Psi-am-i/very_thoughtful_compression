#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════════════════
# h264_to_h265.sh — Batch re-encode H.264 video files to H.265/HEVC
#
# USAGE:
#   ./h264_to_h265.sh [SRC] [DST] [JOBS]
#
#   SRC   Source directory (default: current directory)
#   DST   Destination directory (default: same as SRC)
#   JOBS  Parallel jobs passed as 3rd argument (overrides interactive prompt)
#
# ENV VARS (skip the interactive prompts):
#   JOBS=1|2|4              Parallel encoding jobs
#   MIN_SAVING_RATIO=0.85   Minimum size ratio to replace source (default: 0.85 = 15% saving)
#   DELETE_SOURCE=0|1       Delete source after successful encode (default: 0)
#
# GRACEFUL STOP:
#   touch /tmp/hevc_stop     — finish current file(s), skip the rest
#   rm /tmp/hevc_stop        — clear the stop flag to resume/re-run
#
# EXAMPLES:
#   ./h264_to_h265.sh /Volumes/Media /Volumes/Media 2
#   JOBS=4 DELETE_SOURCE=1 ./h264_to_h265.sh /Volumes/Media
# ══════════════════════════════════════════════════════════════════════════════
set -euo pipefail

SRC="${1:-.}"
SRC="${SRC%/}"
DST="${2:-$SRC}"
DST="${DST%/}"
TMPROOT="/tmp/hevcwork"

# Bitrate logic:
#   Target ratio = adaptive, based on source bits-per-pixel-per-frame (bpp)
#     bpp >= 0.08 (generous source) -> 55%
#     bpp <= 0.03 (lean source)     -> 70%
#     linear interpolation between
#   Floor  = 3800k  (avoids garbage output on low-bitrate sources)
#   Cap 4K = 16000k
#   Cap HD  = 8000k  (1080p and below)
BITRATE_FLOOR=3800
BITRATE_CAP_4K=16000
BITRATE_CAP_HD=8000
BPP_HIGH=0.08
BPP_LOW=0.03
RATIO_HIGH=0.55
RATIO_LOW=0.7

# ── Interactive prompts (skip by pre-setting env vars) ───────────────────────

if [[ -n "${3:-}" ]]; then
  JOBS="$3"
elif [[ -n "${JOBS:-}" ]]; then
  :
else
  printf '\nParallel encoding jobs? (H.265 is CPU-heavy)\n  1) 1 job  [default]\n  2) 2 jobs\n  3) 4 jobs\n' >&2
  read -r -p "  Choice [1/2/3]: " _jchoice </dev/tty 2>/dev/tty || _jchoice=1
  case "${_jchoice:-1}" in
    2) JOBS=2 ;;
    3) JOBS=4 ;;
    *) JOBS=1 ;;
  esac
fi
export JOBS

if [[ -z "${MIN_SAVING_SET:-}" ]]; then
  printf '\nMinimum size saving required to replace source?\n  1) 15%%  [default]\n  2) 10%%\n  3) 5%%\n  4) None — always replace if encode succeeds\n' >&2
  read -r -p "  Choice [1/2/3/4]: " _schoice </dev/tty 2>/dev/tty || _schoice=1
  case "${_schoice:-1}" in
    2) MIN_SAVING_RATIO="0.90" ;;
    3) MIN_SAVING_RATIO="0.95" ;;
    4) MIN_SAVING_RATIO="1.00" ;;
    *) MIN_SAVING_RATIO="0.85" ;;
  esac
fi
export MIN_SAVING_RATIO

if [[ -z "${DELETE_SOURCE_SET:-}" ]]; then
  printf '\nDelete source file after successful encode?\n  1) Keep source  [default]\n  2) Delete source (backed up to _originals/)\n' >&2
  read -r -p "  Choice [1/2]: " _del </dev/tty 2>/dev/tty || _del=1
  case "${_del:-1}" in
    2) DELETE_SOURCE=1 ;;
    *) DELETE_SOURCE=0 ;;
  esac
fi
export DELETE_SOURCE

FFMPEG="${FFMPEG:-ffmpeg}"
FFPROBE="${FFPROBE:-ffprobe}"
SIZE_THRESHOLD=$(( 4 * 1024 * 1024 * 1024 ))

mkdir -p "$TMPROOT"

PROBLEM_LOG="$(mktemp /tmp/hevc_problems.XXXXXX)"
STOP_FILE="/tmp/hevc_stop"
export PROBLEM_LOG STOP_FILE
trap 'rm -f "$PROBLEM_LOG"' EXIT

check_stop_requested() {
  if [[ -f "$STOP_FILE" ]]; then
    log "$(ts) STOP  : stop file found ($STOP_FILE) — skipping remaining files"
    return 0
  fi
  return 1
}

TTY=/dev/tty
log(){ printf '%s\n' "$*" > "$TTY"; }
ts(){ date +%H:%M:%S; }
problem(){ printf '%s\t%s\n' "$2" "$1" >> "$PROBLEM_LOG"; }
fsize(){ du -sh "$1" 2>/dev/null | awk '{print $1}'; }
sname(){ local b; b="$(basename "$1")"; printf '%s' "${b%.*}"; }

# Progress-aware ffmpeg wrapper.
#   ff_run_progress LABEL DURATION_SECS START_TS [ffmpeg args...]
# Writes progress to a temp file via -progress, spawns a monitor,
# cleans up on exit.  Returns ffmpeg's exit code.
ff_run_progress() {
  local label="$1" dur_secs="$2" start_ts="$3"
  shift 3

  local _e _prog _rc
  _e=$(mktemp /tmp/fferr.XXXXXX)
  _prog=$(mktemp /tmp/ffprog.XXXXXX)

  # Stagger reporting interval: 1 job → 10s; >1 job → 20s with per-file offset
  # so parallel encodes don't all log simultaneously.
  local interval offset monitor_pid
  if [[ "${JOBS:-1}" -le 1 ]]; then
    interval=10
    offset=0
  else
    interval=20
    # Hash the label to a 0–19 second offset
    offset=$(( $(printf '%s' "$label" | cksum | awk '{print $1}') % interval ))
  fi

  # Background monitor: sleeps for initial offset, then logs every interval
  ( sleep "$offset"
    while true; do
      sleep "$interval"
      [[ -f "$_prog" ]] || break

      # Parse the most recent ffmpeg progress snapshot
      local out_time_us speed bitrate pct eta_str elapsed
      out_time_us=$(grep '^out_time_us=' "$_prog" 2>/dev/null | tail -1 | cut -d= -f2)
      speed=$(grep       '^speed='       "$_prog" 2>/dev/null | tail -1 | cut -d= -f2 | tr -d ' ')
      bitrate=$(grep     '^bitrate='     "$_prog" 2>/dev/null | tail -1 | cut -d= -f2 | tr -d ' ')
      elapsed=$(( $(date +%s) - start_ts ))

      # Percent complete — requires a valid duration and timestamp
      if [[ -n "$out_time_us" && "$out_time_us" =~ ^[0-9]+$ && \
            -n "$dur_secs"    && "$dur_secs"    =~ ^[0-9]+$  && \
            "$dur_secs" -gt 0 ]]; then
        local done_secs=$(( out_time_us / 1000000 ))
        pct=$(python3 -c "print(f'{min($done_secs/$dur_secs*100,100):.1f}')")
        # ETA
        if [[ "$elapsed" -gt 2 && $(python3 -c "print(1 if $done_secs>0 else 0)") == "1" ]]; then
          local remain
          remain=$(python3 -c "
done=$done_secs; total=$dur_secs; el=$elapsed
if done>0:
    eta=int(el/done*(total-done))
    m,s=divmod(eta,60); h,m=divmod(m,60)
    print(f'{h}h{m:02d}m' if h else f'{m}m{s:02d}s')
else:
    print('?')
")
          eta_str="  ETA=${remain}"
        else
          eta_str=""
        fi
        log "$(ts)  ...  : ${pct}%  speed=${speed:-?}  bitrate=${bitrate:-?}  elapsed=${elapsed}s${eta_str}  — ${label}"
      else
        # Duration unknown — fall back to elapsed + whatever stats we have
        log "$(ts)  ...  : elapsed=${elapsed}s  speed=${speed:-?}  bitrate=${bitrate:-?}  — ${label}"
      fi
    done ) &
  monitor_pid=$!

  "$FFMPEG" -nostdin -hide_banner -loglevel error \
    -progress "$_prog" -stats_period 2 \
    "$@" 2>"$_e"
  _rc=$?

  kill "$monitor_pid" 2>/dev/null
  wait "$monitor_pid" 2>/dev/null || true

  if [[ -s "$_e" && $_rc -ne 0 ]]; then
    while IFS= read -r _l; do
      _l=$(printf '%s' "$_l" | sed $'s/\033\\[[0-9;]*m//g')
      [[ -z "$_l" ]] && continue
      printf '%s\n' "$(ts)   ! ${_l}" > "$TTY"
    done < "$_e"
  fi
  rm -f "$_e" "$_prog"
  return "$_rc"
}

# Legacy no-progress wrapper (used for audio-probe pass, VT check, etc.)
ff_run() {
  local _e _rc
  _e=$(mktemp /tmp/fferr.XXXXXX)
  "$FFMPEG" -nostdin -hide_banner -loglevel error "$@" 2>"$_e"
  _rc=$?
  if [[ -s "$_e" && $_rc -ne 0 ]]; then
    while IFS= read -r _l; do
      _l=$(printf '%s' "$_l" | sed $'s/\033\\[[0-9;]*m//g')
      [[ -z "$_l" ]] && continue
      printf '%s\n' "$(ts)   ! ${_l}" > "$TTY"
    done < "$_e"
  fi
  rm -f "$_e"
  return "$_rc"
}

probe_video() {
  local file="$1" field="$2"
  "$FFPROBE" -v error -select_streams v:0 \
    -show_entries "stream=$field" -of csv=p=0 "$file" | head -1
}

probe_container_bitrate() {
  "$FFPROBE" -v error \
    -show_entries format=bit_rate \
    -of csv=p=0 "$1" | head -1
}

file_size() {
  stat -f%z "$1" 2>/dev/null || stat -c%s "$1"
}

check_videotoolbox() {
  # Prefer an explicit env override: FORCE_VT=1 or FORCE_VT=0
  if [[ "${FORCE_VT:-}" == "1" ]]; then return 0; fi
  if [[ "${FORCE_VT:-}" == "0" ]]; then return 1; fi
  # Check encoder list rather than doing a test encode — the test encode
  # fails over SSH even when hardware is perfectly available
  "$FFMPEG" -hide_banner -encoders 2>/dev/null | grep -q 'hevc_videotoolbox'
}

calc_bitrate() {
  local src_bps="$1" width="$2" height="$3" fps="$4"
  local result
  result=$(python3 -c "
src_bps    = $src_bps
width      = $width
height     = $height
fps        = $fps
src_kbps   = src_bps / 1000
pixels     = width * height
bpp        = src_bps / (pixels * fps) if (pixels > 0 and fps > 0) else 0.05
bpp_high   = $BPP_HIGH
bpp_low    = $BPP_LOW
ratio_high = $RATIO_HIGH
ratio_low  = $RATIO_LOW
if bpp >= bpp_high:
    ratio = ratio_high
elif bpp <= bpp_low:
    ratio = ratio_low
else:
    t = (bpp - bpp_low) / (bpp_high - bpp_low)
    ratio = ratio_low + t * (ratio_high - ratio_low)
floor  = $BITRATE_FLOOR
cap    = $BITRATE_CAP_4K if width >= 3840 else $BITRATE_CAP_HD
kbps   = max(floor, min(src_kbps * ratio, cap))
print(int(kbps))
print(f'bpp={bpp:.4f} ratio={ratio:.0%} src={src_kbps:.0f}k -> target={int(kbps)}k')
")
  local kbps bpp_info
  kbps="$(echo "$result" | sed -n '1p')"
  bpp_info="$(echo "$result" | sed -n '2p')"
  log "$(ts) BPP   : $bpp_info"
  echo "${kbps}k"
}

prescan_worth_encoding() {
  local file="$1"
  local n; n="$(sname "$file")"

  local src_bps width height fps_raw fps
  width="$(probe_video "$file" width)"
  height="$(probe_video "$file" height)"
  src_bps="$(probe_video "$file" bit_rate)"
  [[ -z "$src_bps" || "$src_bps" == "N/A" ]] && src_bps="$(probe_container_bitrate "$file")"
  [[ -z "$src_bps" || ! "$src_bps" =~ ^[0-9]+$ ]] && return 0

  fps_raw="$(probe_video "$file" r_frame_rate)"
  fps="$(python3 -c "
from fractions import Fraction
try: print(float(Fraction('${fps_raw:-30/1}')))
except: print(30.0)
")"

  local result decision info
  result="$(python3 -c "
src_bps = $src_bps; width = $width; height = $height; fps = $fps
src_kbps = src_bps / 1000
pixels   = width * height
bpp      = src_bps / (pixels * fps) if pixels > 0 and fps > 0 else 0.05
bpp_high   = $BPP_HIGH;   bpp_low   = $BPP_LOW
ratio_high = $RATIO_HIGH; ratio_low = $RATIO_LOW
if bpp >= bpp_high:   ratio = ratio_high
elif bpp <= bpp_low:  ratio = ratio_low
else:
    t = (bpp - bpp_low) / (bpp_high - bpp_low)
    ratio = ratio_low + t * (ratio_high - ratio_low)
floor  = $BITRATE_FLOOR
cap    = $BITRATE_CAP_4K if width >= 3840 else $BITRATE_CAP_HD
target = max(floor, min(src_kbps * ratio, cap))
est    = target / src_kbps
saving = (1 - est) * 100
decision = 'skip' if est >= $MIN_SAVING_RATIO else 'encode'
print(decision)
print(f'est_saving={saving:.1f}% bpp={bpp:.4f} src={src_kbps:.0f}k -> target={target:.0f}k')
")"

  decision="$(echo "$result" | sed -n '1p')"
  info="$(echo "$result" | sed -n '2p')"

  if [[ "$decision" == "skip" ]]; then
    log "$(ts) SKIP  : not worth encoding ($info) — $n"
    return 1
  fi
  return 0
}

should_encode() {
  local file="$1"
  local n; n="$(sname "$file")"

  local codec width filesize
  codec="$(probe_video "$file" codec_name)"
  width="$(probe_video "$file" width)"
  filesize="$(file_size "$file")"

  case "$codec" in
    h264|avc) ;;
    *) log "$(ts) SKIP  : codec=$codec (not H.264) — $n"; return 1 ;;
  esac

  if [[ -n "$width" && "$width" -ge 3840 ]]; then
    log "$(ts) QUEUE : 4K H.264 (${width}px) — $n"
    return 0
  fi

  if [[ "$filesize" -ge "$SIZE_THRESHOLD" ]]; then
    local gb=$(( filesize / 1024 / 1024 / 1024 ))
    log "$(ts) QUEUE : H.264 over threshold (~${gb}GB) — $n"
    return 0
  fi

  log "$(ts) SKIP  : H.264 under threshold (${width}px, $(( filesize / 1024 / 1024 ))MB) — $n"
  return 1
}

process_one() {
  local src="$1" dst="$2" file="$3"

  local src_base dst_base
  if [[ -f "$src" ]]; then
    src_base="$(dirname "$src")"
    dst_base="$(dirname "$dst")"
  else
    src_base="$src"
    dst_base="$dst"
  fi

  local rel out
  rel="${file#"$src_base"/}"
  out="$dst_base/${rel%.*}.mp4"
  local n; n="$(sname "$file")"

  mkdir -p "$(dirname "$out")"

  if check_stop_requested; then return 0; fi

  if [[ -f "$out" && "$out" != "$file" ]]; then
    log "$(ts) SKIP  : already converted — $n"
    return 0
  fi

  if ! should_encode "$file"; then
    return 0
  fi

  if ! prescan_worth_encoding "$file"; then
    return 0
  fi

  # Probe source
  local src_bps width height fps fps_raw dur_secs
  width="$(probe_video "$file" width)"
  height="$(probe_video "$file" height)"
  src_bps="$(probe_video "$file" bit_rate)"
  [[ -z "$src_bps" || "$src_bps" == "N/A" ]] && src_bps="$(probe_container_bitrate "$file")"

  fps_raw="$(probe_video "$file" r_frame_rate)"
  fps="$(python3 -c "
from fractions import Fraction
try:    print(float(Fraction('${fps_raw:-30/1}')))
except: print(30.0)
")"

  # Duration in whole seconds - used by the progress monitor for % complete
  local dur_raw
  dur_raw="$("$FFPROBE" -v error -show_entries format=duration \
    -of csv=p=0 "$file" 2>/dev/null | head -1)"
  if [[ "$dur_raw" =~ ^[0-9]+([.][0-9]+)?$ ]]; then
    dur_secs="$(python3 -c "print(int(float('$dur_raw')))")"
  else
    dur_secs=0
  fi

  local bitrate
  if [[ -n "$src_bps" && "$src_bps" =~ ^[0-9]+$ ]]; then
    bitrate="$(calc_bitrate "$src_bps" "$width" "$height" "$fps")"
  else
    bitrate="$( [[ "$width" -ge 3840 ]] && echo "${BITRATE_CAP_4K}k" || echo "${BITRATE_CAP_HD}k" )"
    log "$(ts) WARN  : could not probe bitrate, using cap ($bitrate) — $n"
    problem "$file" "WARN: could not probe source bitrate; used cap (${bitrate}) — verify output quality"
  fi

  local pix_fmt profile
  pix_fmt="$(probe_video "$file" pix_fmt)"
  case "$pix_fmt" in
    *10le|*10be|*10*) profile="main10" ;;
    *)                profile="main"   ;;
  esac

  local start base tmp src_size
  start="$(date +%s)"
  base="$(basename "${out%.mp4}")"
  tmp="$TMPROOT/${base}.$$.$RANDOM.mp4"
  src_size="$(file_size "$file")"

  log "$(ts) START : [$(fsize "$file")]  $n"
  log "$(ts) INFO  : ${width}x${height}  fps=$fps  pix=$pix_fmt — $n"

  local use_vt=0
  if check_videotoolbox; then
    use_vt=1
    log "$(ts) ENCODE: VideoToolbox  target=$bitrate  profile=$profile — $n"
  else
    log "$(ts) ENCODE: libx265 crf=21 medium  profile=$profile — $n"
  fi

  local encode_ok=0

  for audio_mode in copy aac; do
    local audio_flags=()
    if [[ "$audio_mode" == "copy" ]]; then
      audio_flags=(-c:a copy)
    else
      log "$(ts) AUDIO : copy failed, falling back to AAC — $n"
      audio_flags=(-c:a aac -b:a 384k)
    fi

    if [[ "$use_vt" -eq 1 ]]; then
      if ff_run_progress "$n" "$dur_secs" "$start" \
          -y -i "$file" \
          -map 0:v:0 -map 0:a \
          -c:v hevc_videotoolbox -b:v "$bitrate" \
          -profile:v "$profile" \
          -tag:v hvc1 \
          -bf 0 \
          -fps_mode cfr \
          "${audio_flags[@]}" \
          -movflags +faststart \
          "$tmp"; then
        encode_ok=1; break
      fi
    else
      if ff_run_progress "$n" "$dur_secs" "$start" \
          -y -i "$file" \
          -map 0:v:0 -map 0:a \
          -c:v libx265 -crf 21 -preset medium \
          -profile:v "$profile" \
          "${audio_flags[@]}" \
          -movflags +faststart \
          "$tmp"; then
        encode_ok=1; break
      fi
    fi
    rm -f "$tmp"
  done

  if [[ "$encode_ok" -eq 0 ]]; then
    log "$(ts) ERROR : encode failed — $n"
    problem "$file" "ERROR: encode failed (both audio-copy and AAC fallback) — file skipped"
    return 1
  fi

  local out_size threshold
  out_size="$(file_size "$tmp")"
  threshold=$(python3 -c "print(int($src_size * $MIN_SAVING_RATIO))")

  local end elapsed
  end="$(date +%s)"
  elapsed=$(( end - start ))

  log "$(ts) SIZE  : $(( src_size / 1024 / 1024 ))MB → $(( out_size / 1024 / 1024 ))MB  (need < $(( threshold / 1024 / 1024 ))MB) — $n"

  if [[ "$out_size" -ge "$threshold" ]]; then
    rm -f "$tmp"
    log "$(ts) SKIP  : saving too small, keeping original — $n  [${elapsed}s]"
    return 0
  fi

  log "$(ts) MOVE  : $n"
  mv -f "$tmp" "$out"

  if [[ -s "$out" ]]; then
    if [[ "$DELETE_SOURCE" == "1" ]]; then
      local orig_dir rel_dir
      rel_dir="$(dirname "$rel")"
      orig_dir="$src_base/_originals${rel_dir:+/$rel_dir}"
      mkdir -p "$orig_dir"
      cp -f "$file" "$orig_dir/$(basename "$file")"
      [[ "$file" != "$out" ]] && rm -f "$file"
      log "$(ts) DONE  : ${elapsed}s  [$(fsize "$out")]  $n  (original backed up)"
    else
      log "$(ts) DONE  : ${elapsed}s  [$(fsize "$out")]  $n"
    fi
  else
    log "$(ts) WARN  : output empty after encode — $n"
    problem "$file" "WARN: output missing or empty after encode — source not deleted"
    rm -f "$out" 2>/dev/null || true
  fi
}

export -f process_one should_encode prescan_worth_encoding probe_video probe_container_bitrate \
           file_size check_videotoolbox calc_bitrate log ts ff_run ff_run_progress problem fsize sname \
           check_stop_requested
export FFMPEG FFPROBE TMPROOT SIZE_THRESHOLD DELETE_SOURCE PROBLEM_LOG STOP_FILE TTY
export BITRATE_FLOOR BITRATE_CAP_4K BITRATE_CAP_HD MIN_SAVING_RATIO BPP_HIGH BPP_LOW RATIO_HIGH RATIO_LOW

log "SRC:         $SRC"
log "DST:         $DST"
log "JOBS:        $JOBS"
log "BITRATE:     adaptive ${RATIO_HIGH}-${RATIO_LOW} of source | floor=${BITRATE_FLOOR}k | cap_4k=${BITRATE_CAP_4K}k | cap_hd=${BITRATE_CAP_HD}k"
log "MIN_SAVING:  output must be <$(python3 -c "print(int((1-$MIN_SAVING_RATIO)*100))")% smaller than source to replace"
log "STOP:        touch $STOP_FILE   (completes in-progress encodes, skips the rest)"

find "$SRC" \
  \( -name '.Trashes' -o -name '.Spotlight-V100' -o -name '.fseventsd' -o -name '.TemporaryItems' \) -prune \
  -o \( -type f -not -name '._*' \( -iname "*.mkv" -o -iname "*.mp4" \) -print0 \) \
  | xargs -0 -n 1 -P "$JOBS" bash -c 'process_one "$@"' _ "$SRC" "$DST"

if [[ -s "$PROBLEM_LOG" ]]; then
  count="$(wc -l < "$PROBLEM_LOG" | tr -d ' ')"
  log ""
  log "══════════════════════════════════════════════════════════"
  log " PROBLEMS — ${count} file(s) need attention"
  log "══════════════════════════════════════════════════════════"
  while IFS=$'\t' read -r reason file; do
    log "  [${reason}]"
    log "    ${file}"
    log ""
  done < "$PROBLEM_LOG"
  log "══════════════════════════════════════════════════════════"
else
  log ""
  log "All files processed without errors."
fi
log "Done."
