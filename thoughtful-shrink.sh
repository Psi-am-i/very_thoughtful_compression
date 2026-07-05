#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════════════════
# thoughtful-shrink.sh — Batch re-encode fat H.264 video files to a sane size
#                        (H.265/HEVC or H.264/AVC, at a chosen quality tier)
#
# USAGE:
#   ./thoughtful-shrink.sh [SRC]
#
#   SRC   Directory to scan (default: current directory)
#
# The script prompts interactively for: output codec (H.265 or H.264), target
# quality tier (5/8/10 Mbps H.264-equivalent), output location, what to do with
# originals, parallel jobs, and minimum saving. Set FORCE_VT=1/0 to force or
# disable hardware (VideoToolbox) encoding.
#
# GRACEFUL STOP:
#   touch /tmp/hevc_stop     — finish current file(s), skip the rest
#   rm /tmp/hevc_stop        — clear the stop flag to resume/re-run
# ══════════════════════════════════════════════════════════════════════════════
set -euo pipefail

SRC="${1:-.}"
SRC="${SRC%/}"
TMPROOT="/tmp/hevcwork"

# ── Bitrate strategy ──────────────────────────────────────────────────────────
# The target bitrate comes from a quality TIER chosen per-run (see prompt below),
# expressed in familiar "H.264 @ 1080p" Mbps. That tier becomes a per-pixel
# ceiling that scales automatically with resolution and, for H.265 output, is
# multiplied by HEVC_EFFICIENCY (H.265 reaches the same quality at ~half the
# bitrate of H.264):
#
#   cap_kbps = tier_mbps*1000 * (pixels / 1920x1080) * codec_factor
#   codec_factor = HEVC_EFFICIENCY for H.265, 1.0 for H.264
#
# Below the cap, the adaptive ratio still trims by source bpp so moderately fat
# files shrink proportionally rather than all pinning to the ceiling:
#     bpp >= 0.08 (generous source) -> keep 55%
#     bpp <= 0.03 (lean source)     -> keep 70%   (linear between)
#
# "Already compressed" guard: if a source's bpp is at/below BPP_SKIP_FLOOR it is
# already tightly encoded for its resolution — re-encoding buys almost nothing
# and only adds a generation of loss, so it is skipped outright.
BITRATE_FLOOR=1500          # never target below this (avoids garbage output)
BPP_SKIP_FLOOR=0.050        # source at/below this bpp = already efficient, skip
HEVC_EFFICIENCY=0.55        # H.265 bitrate for parity with a given H.264 bitrate
BPP_HIGH=0.08
BPP_LOW=0.03
RATIO_HIGH=0.55
RATIO_LOW=0.7

# ── Interactive prompts ───────────────────────────────────────────────────────

printf '\nOutput codec?\n  1) H.265 / HEVC  — smallest files, needs a reasonably modern player  [default]\n  2) H.264 / AVC   — ~2x larger, direct-plays on virtually anything\n' >&2
read -r -p "  Choice [1/2]: " _cchoice </dev/tty 2>/dev/tty || _cchoice=1
case "${_cchoice:-1}" in
  2) OUT_CODEC=h264 ;;
  *) OUT_CODEC=h265 ;;
esac
export OUT_CODEC

printf '\nTarget quality tier? (shown as H.264 @ 1080p Mbps — for H.265 the script\nautomatically uses ~half that bitrate for the same quality)\n  1) 5   — older / SD or unremastered TV\n  2) 8   — standard; great for most film & TV (e.g. Columbo)  [default]\n  3) 10  — keep it crisp; remastered / detailed material\n' >&2
read -r -p "  Choice [1/2/3]: " _tchoice </dev/tty 2>/dev/tty || _tchoice=2
case "${_tchoice:-2}" in
  1) TARGET_MBPS=5 ;;
  3) TARGET_MBPS=10 ;;
  *) TARGET_MBPS=8 ;;
esac
export TARGET_MBPS

printf '\nWhere should the new %s file be written?\n' "$( [[ "$OUT_CODEC" == "h264" ]] && echo H.264 || echo H.265 )" >&2
printf '  1) Replace source in place  [default]\n  2) Write to a separate folder\n' >&2
read -r -p "  Choice [1/2]: " _ochoice </dev/tty 2>/dev/tty || _ochoice=1
case "${_ochoice:-1}" in
  2)
    OUTPUT_MODE=separate
    printf '\nDestination folder for new files?\n  (default: %s/new versions)\n' "$SRC" >&2
    read -r -p "  Path: " _opath </dev/tty 2>/dev/tty || _opath=""
    OUTPUT_DIR="${_opath:-$SRC/new versions}"
    printf '\nFolder structure in destination?\n  1) Mirror source folder structure  [default]\n  2) Flat — all files in destination root\n' >&2
    read -r -p "  Choice [1/2]: " _fchoice </dev/tty 2>/dev/tty || _fchoice=1
    case "${_fchoice:-1}" in
      2) OUTPUT_FLAT=1 ;;
      *) OUTPUT_FLAT=0 ;;
    esac
    ;;
  *)
    OUTPUT_MODE=inplace
    OUTPUT_DIR="$SRC"
    OUTPUT_FLAT=0
    ;;
esac
export OUTPUT_MODE OUTPUT_DIR OUTPUT_FLAT

printf '\nWhat should happen to the original file?\n  1) Archive (move to a folder)  [default]\n  2) Delete\n  3) Leave it where it is\n' >&2
read -r -p "  Choice [1/2/3]: " _schoice </dev/tty 2>/dev/tty || _schoice=1
case "${_schoice:-1}" in
  2) SOURCE_ACTION=delete ;;
  3) SOURCE_ACTION=keep ;;
  *) SOURCE_ACTION=archive ;;
esac

if [[ "$SOURCE_ACTION" == "archive" ]]; then
  printf '\nWhere should originals be archived?\n  (default: %s/originals)\n' "$SRC" >&2
  read -r -p "  Path: " _apath </dev/tty 2>/dev/tty || _apath=""
  ARCHIVE_DIR="${_apath:-$SRC/originals}"
else
  ARCHIVE_DIR=""
fi
export SOURCE_ACTION ARCHIVE_DIR

printf '\nParallel encoding jobs? (H.265 is CPU-heavy)\n  1) 1 job  [default]\n  2) 2 jobs\n  3) 4 jobs\n' >&2
read -r -p "  Choice [1/2/3]: " _jchoice </dev/tty 2>/dev/tty || _jchoice=1
case "${_jchoice:-1}" in
  2) JOBS=2 ;;
  3) JOBS=4 ;;
  *) JOBS=1 ;;
esac
export JOBS

printf '\nMinimum size saving required to keep the new file?\n  1) 15%%  [default]\n  2) 10%%\n  3) 5%%\n  4) None — always keep if encode succeeds\n' >&2
read -r -p "  Choice [1/2/3/4]: " _mschoice </dev/tty 2>/dev/tty || _mschoice=1
case "${_mschoice:-1}" in
  2) MIN_SAVING_RATIO="0.90" ;;
  3) MIN_SAVING_RATIO="0.95" ;;
  4) MIN_SAVING_RATIO="1.00" ;;
  *) MIN_SAVING_RATIO="0.85" ;;
esac
export MIN_SAVING_RATIO

# ── Setup ─────────────────────────────────────────────────────────────────────

FFMPEG="${FFMPEG:-ffmpeg}"
FFPROBE="${FFPROBE:-ffprobe}"
SIZE_THRESHOLD=$(( 4 * 1024 * 1024 * 1024 ))

mkdir -p "$TMPROOT"

PROBLEM_LOG="$(mktemp /tmp/hevc_problems.XXXXXX)"
STOP_FILE="/tmp/hevc_stop"
export PROBLEM_LOG STOP_FILE
trap 'rm -f "$PROBLEM_LOG"' EXIT

TTY=/dev/tty
log(){ printf '%s\n' "$*" > "$TTY"; }
ts(){ date +%H:%M:%S; }
problem(){ printf '%s\t%s\n' "$2" "$1" >> "$PROBLEM_LOG"; }
fsize(){ du -sh "$1" 2>/dev/null | awk '{print $1}'; }
sname(){ local b; b="$(basename "$1")"; printf '%s' "${b%.*}"; }

check_stop_requested() {
  if [[ -f "$STOP_FILE" ]]; then
    log "$(ts) STOP  : stop file found ($STOP_FILE) — skipping remaining files"
    return 0
  fi
  return 1
}

# Progress-aware ffmpeg wrapper.
#   ff_run_progress LABEL DURATION_SECS START_TS [ffmpeg args...]
ff_run_progress() {
  local label="$1" dur_secs="$2" start_ts="$3"
  shift 3

  local _e _prog _rc
  _e=$(mktemp /tmp/fferr.XXXXXX)
  _prog=$(mktemp /tmp/ffprog.XXXXXX)

  local interval offset monitor_pid
  if [[ "${JOBS:-1}" -le 1 ]]; then
    interval=10; offset=0
  else
    interval=20
    offset=$(( $(printf '%s' "$label" | cksum | awk '{print $1}') % interval ))
  fi

  ( sleep "$offset"
    while true; do
      sleep "$interval"
      [[ -f "$_prog" ]] || break
      local out_time_us speed bitrate elapsed
      out_time_us=$(grep '^out_time_us=' "$_prog" 2>/dev/null | tail -1 | cut -d= -f2)
      speed=$(grep       '^speed='       "$_prog" 2>/dev/null | tail -1 | cut -d= -f2 | tr -d ' ')
      bitrate=$(grep     '^bitrate='     "$_prog" 2>/dev/null | tail -1 | cut -d= -f2 | tr -d ' ')
      elapsed=$(( $(date +%s) - start_ts ))
      if [[ -n "$out_time_us" && "$out_time_us" =~ ^[0-9]+$ && \
            -n "$dur_secs"    && "$dur_secs"    =~ ^[0-9]+$  && \
            "$dur_secs" -gt 0 ]]; then
        local done_secs=$(( out_time_us / 1000000 ))
        local pct
        pct=$(python3 -c "print(f'{min($done_secs/$dur_secs*100,100):.1f}')")
        local eta_str=""
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
        fi
        log "$(ts)  ...  : ${pct}%  speed=${speed:-?}  bitrate=${bitrate:-?}  elapsed=${elapsed}s${eta_str}  — ${label}"
      else
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
  if [[ "${FORCE_VT:-}" == "1" ]]; then return 0; fi
  if [[ "${FORCE_VT:-}" == "0" ]]; then return 1; fi
  local enc
  enc="$( [[ "${OUT_CODEC:-h265}" == "h264" ]] && echo h264_videotoolbox || echo hevc_videotoolbox )"
  "$FFMPEG" -hide_banner -encoders 2>/dev/null | grep -q "$enc"
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
codec_factor = $HEVC_EFFICIENCY if '$OUT_CODEC' == 'h265' else 1.0
cap    = $TARGET_MBPS * 1000 * (pixels / (1920*1080)) * codec_factor
floor  = min($BITRATE_FLOOR, cap)
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
codec_factor = $HEVC_EFFICIENCY if '$OUT_CODEC' == 'h265' else 1.0
cap    = $TARGET_MBPS * 1000 * (pixels / (1920*1080)) * codec_factor
floor  = min($BITRATE_FLOOR, cap)
target = max(floor, min(src_kbps * ratio, cap))
est    = target / src_kbps
saving = (1 - est) * 100
if bpp <= $BPP_SKIP_FLOOR:
    decision = 'skip-efficient'
elif est >= $MIN_SAVING_RATIO:
    decision = 'skip'
else:
    decision = 'encode'
print(decision)
print(f'est_saving={saving:.1f}% bpp={bpp:.4f} src={src_kbps:.0f}k -> target={target:.0f}k')
")"

  decision="$(echo "$result" | sed -n '1p')"
  info="$(echo "$result" | sed -n '2p')"

  if [[ "$decision" == "skip-efficient" ]]; then
    log "$(ts) SKIP  : already efficiently compressed ($info) — $n"
    return 1
  fi
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
  local src="$1" file="$2"

  local rel out
  rel="${file#"$src"/}"

  # Determine output path
  if [[ "${OUTPUT_MODE:-inplace}" == "separate" ]]; then
    if [[ "${OUTPUT_FLAT:-0}" == "1" ]]; then
      out="${OUTPUT_DIR}/$(basename "${rel%.*}").mp4"
    else
      out="${OUTPUT_DIR}/${rel%.*}.mp4"
    fi
  else
    out="${src}/${rel%.*}.mp4"
  fi

  local n; n="$(sname "$file")"

  mkdir -p "$(dirname "$out")"

  if check_stop_requested; then return 0; fi

  if [[ -f "$out" && "$out" != "$file" ]]; then
    log "$(ts) SKIP  : already converted — $n"
    return 0
  fi

  if ! should_encode "$file"; then return 0; fi
  if ! prescan_worth_encoding "$file"; then return 0; fi

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
    bitrate="$(python3 -c "
w=$width; h=$height
cf=$HEVC_EFFICIENCY if '$OUT_CODEC'=='h265' else 1.0
cap=$TARGET_MBPS*1000*((w*h)/(1920*1080))*cf
print(f'{max($BITRATE_FLOOR,int(cap))}k')
")"
    log "$(ts) WARN  : could not probe bitrate, using tier cap ($bitrate) — $n"
    problem "$file" "WARN: could not probe source bitrate; used tier cap (${bitrate}) — verify output quality"
  fi

  local pix_fmt profile
  pix_fmt="$(probe_video "$file" pix_fmt)"
  if [[ "$OUT_CODEC" == "h264" ]]; then
    profile="high"      # 8-bit High for maximum player compatibility
  else
    case "$pix_fmt" in
      *10le|*10be|*10*) profile="main10" ;;
      *)                profile="main"   ;;
    esac
  fi

  local start base tmp src_size
  start="$(date +%s)"
  base="$(basename "${out%.mp4}")"
  tmp="$TMPROOT/${base}.$$.$RANDOM.mp4"
  src_size="$(file_size "$file")"

  log "$(ts) START : [$(fsize "$file")]  $n"
  log "$(ts) INFO  : ${width}x${height}  fps=$fps  pix=$pix_fmt — $n"

  # Build the video-encoder args. Hardware (VideoToolbox) uses -b:v as the
  # target; software (libx26x) uses constant-quality CRF but with -maxrate/
  # -bufsize so it never exceeds the chosen tier cap ("capped CRF").
  local use_vt=0
  check_videotoolbox && use_vt=1

  local br_k bufsize
  br_k="${bitrate%k}"
  bufsize=$(( br_k * 2 ))

  local -a vargs
  if [[ "$OUT_CODEC" == "h264" ]]; then
    if [[ "$use_vt" -eq 1 ]]; then
      vargs=(-c:v h264_videotoolbox -b:v "$bitrate" -profile:v high -pix_fmt yuv420p)
      log "$(ts) ENCODE: VideoToolbox H.264  target=$bitrate — $n"
    else
      vargs=(-c:v libx264 -crf 20 -preset medium -maxrate "$bitrate" -bufsize "${bufsize}k" \
             -profile:v high -pix_fmt yuv420p)
      log "$(ts) ENCODE: libx264 crf=20 medium  cap=$bitrate — $n"
    fi
  else
    if [[ "$use_vt" -eq 1 ]]; then
      vargs=(-c:v hevc_videotoolbox -b:v "$bitrate" -profile:v "$profile" -tag:v hvc1 -bf 0 -fps_mode cfr)
      log "$(ts) ENCODE: VideoToolbox H.265  target=$bitrate  profile=$profile — $n"
    else
      vargs=(-c:v libx265 -crf 21 -preset medium -maxrate "$bitrate" -bufsize "${bufsize}k" \
             -profile:v "$profile" -tag:v hvc1)
      log "$(ts) ENCODE: libx265 crf=21 medium  cap=$bitrate  profile=$profile — $n"
    fi
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

    if ff_run_progress "$n" "$dur_secs" "$start" \
        -y -i "$file" \
        -map 0:v:0 -map 0:a? \
        "${vargs[@]}" \
        "${audio_flags[@]}" \
        -movflags +faststart \
        "$tmp"; then
      encode_ok=1; break
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

  mv -f "$tmp" "$out"

  if [[ -s "$out" ]]; then
    case "${SOURCE_ACTION:-archive}" in
      delete)
        [[ "$file" != "$out" ]] && rm -f "$file"
        log "$(ts) DONE  : ${elapsed}s  [$(fsize "$out")]  $n  (original deleted)"
        ;;
      archive)
        local arc_rel arc_dest
        arc_rel="$(dirname "$rel")"
        arc_dest="${ARCHIVE_DIR}${arc_rel:+/$arc_rel}"
        mkdir -p "$arc_dest"
        [[ "$file" != "$out" ]] && mv -f "$file" "$arc_dest/$(basename "$file")"
        log "$(ts) DONE  : ${elapsed}s  [$(fsize "$out")]  $n  (original → $(basename "$ARCHIVE_DIR"))"
        ;;
      keep)
        log "$(ts) DONE  : ${elapsed}s  [$(fsize "$out")]  $n"
        ;;
    esac
  else
    log "$(ts) WARN  : output empty after encode — $n"
    problem "$file" "WARN: output missing or empty after encode — source not deleted"
    rm -f "$out" 2>/dev/null || true
  fi
}

export -f process_one should_encode prescan_worth_encoding probe_video probe_container_bitrate \
           file_size check_videotoolbox calc_bitrate log ts ff_run ff_run_progress problem fsize sname \
           check_stop_requested
export FFMPEG FFPROBE TMPROOT SIZE_THRESHOLD SOURCE_ACTION ARCHIVE_DIR \
       OUTPUT_MODE OUTPUT_DIR OUTPUT_FLAT PROBLEM_LOG STOP_FILE TTY
export BITRATE_FLOOR BPP_SKIP_FLOOR HEVC_EFFICIENCY MIN_SAVING_RATIO BPP_HIGH BPP_LOW RATIO_HIGH RATIO_LOW
export OUT_CODEC TARGET_MBPS

log ""
log "SRC:         $SRC"
log "OUTPUT:      $( [[ "$OUTPUT_MODE" == "separate" ]] && echo "$OUTPUT_DIR ($( [[ $OUTPUT_FLAT == 1 ]] && echo flat || echo mirrored ))" || echo "in place" )"
log "ORIGINALS:   $( case "$SOURCE_ACTION" in archive) echo "archive → $ARCHIVE_DIR" ;; delete) echo "delete" ;; keep) echo "keep" ;; esac )"
log "JOBS:        $JOBS"
log "CODEC:       $( [[ "$OUT_CODEC" == "h264" ]] && echo "H.264 / AVC (universal)" || echo "H.265 / HEVC (smallest)" )"
log "TARGET:      tier ${TARGET_MBPS} Mbps (H.264 @ 1080p equiv)$( [[ "$OUT_CODEC" == "h265" ]] && echo " → ~$(python3 -c "print(f'{$TARGET_MBPS*$HEVC_EFFICIENCY:.1f}')") Mbps H.265" ), scales with resolution"
log "BITRATE:     ≤ tier cap | adaptive ${RATIO_HIGH}-${RATIO_LOW} of source below cap | floor=${BITRATE_FLOOR}k"
log "SKIP-EFFIC.: sources already ≤ ${BPP_SKIP_FLOOR} bpp are left untouched (already well compressed)"
log "MIN_SAVING:  output must be <$(python3 -c "print(int((1-$MIN_SAVING_RATIO)*100))")% smaller than source to replace"
log "STOP:        touch $STOP_FILE   (finish current file, skip the rest)"
log "             rm $STOP_FILE      (clear stop flag to re-run)"
log ""

find "$SRC" \
  \( -name '.Trashes' -o -name '.Spotlight-V100' -o -name '.fseventsd' -o -name '.TemporaryItems' \
     -o -name 'originals' -o -name 'new versions' -o -name 'Library' \) -prune \
  -o \( -type f -not -name '._*' \( -iname "*.mkv" -o -iname "*.mp4" \) -print0 \) \
  | xargs -0 -n 1 -P "$JOBS" bash -c 'process_one "$@"' _ "$SRC"

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
