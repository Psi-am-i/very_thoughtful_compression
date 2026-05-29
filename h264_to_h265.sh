#!/usr/bin/env bash
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
#   Floor  = 3200k  (avoids garbage output on low-bitrate sources)
#   Cap 4K = 15000k
#   Cap HD  = 8000k  (1080p and below)
BITRATE_FLOOR=3800
BITRATE_CAP_4K=16000
BITRATE_CAP_HD=8000
BPP_HIGH=0.08
BPP_LOW=0.03
RATIO_HIGH=0.55
RATIO_LOW=0.7

# ── Interactive prompts (skip by pre-setting env vars) ───────────────────────

# Parallel jobs (arg $3 wins; then env var; then prompt)
if [[ -n "${3:-}" ]]; then
  JOBS="$3"
elif [[ -n "${JOBS:-}" ]]; then
  : # already set
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

# Minimum size saving required before replacing the source
if [[ -z "${MIN_SAVING_SET:-}" ]]; then
  printf '\nMinimum size saving required to replace source?\n  1) 15%%  [default]\n  2) 10%%\n  3) 5%%\n  4) None — always replace if encode succeeds\n' >&2
  read -r -p "  Choice [1/2/3/4]: " _schoice </dev/tty 2>/dev/tty || _schoice=1
  case "${_schoice:-1}" in
    2) MIN_SAVING_RATIO="0.90" ;;   # output must be < 90% of source
    3) MIN_SAVING_RATIO="0.95" ;;   # output must be < 95% of source
    4) MIN_SAVING_RATIO="1.00" ;;   # no threshold
    *) MIN_SAVING_RATIO="0.85" ;;   # output must be < 85% of source (15% saving)
  esac
fi
export MIN_SAVING_RATIO

# Delete source after successful encode?
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
SIZE_THRESHOLD=$(( 4 * 1024 * 1024 * 1024 ))   # 4 GB in bytes

mkdir -p "$TMPROOT"

PROBLEM_LOG="$(mktemp /tmp/hevc_problems.XXXXXX)"
export PROBLEM_LOG
trap 'rm -f "$PROBLEM_LOG"' EXIT

log(){ printf '%s\n' "$*" >&2; }
ts(){ date +%H:%M:%S; }
problem(){ printf '%s\t%s\n' "$2" "$1" >> "$PROBLEM_LOG"; }

ff_run() {
  "$FFMPEG" -nostdin -hide_banner -loglevel error -stats "$@"
}

# Probe a single stream attribute from the first video stream
probe_video() {
  local file="$1" field="$2"
  "$FFPROBE" -v error -select_streams v:0 \
    -show_entries "stream=$field" -of csv=p=0 "$file" | head -1
}

# Probe container-level bitrate (bits/sec) as fallback
probe_container_bitrate() {
  "$FFPROBE" -v error \
    -show_entries format=bit_rate \
    -of csv=p=0 "$1" | head -1
}

# Portable file size (macOS stat vs GNU stat)
file_size() {
  stat -f%z "$1" 2>/dev/null || stat -c%s "$1"
}

# Check if hevc_videotoolbox is available on this machine
check_videotoolbox() {
  "$FFMPEG" -hide_banner -f lavfi -i color=c=black:s=64x64:d=0.1:r=1 \
    -c:v hevc_videotoolbox -f null /dev/null 2>/dev/null
}

# Calculate target bitrate in kbps given source bitrate (bps), width, height, fps
# Outputs two lines: line 1 = kbps integer, line 2 = debug info
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

# Quick pre-encode estimate using the same bitrate model as calc_bitrate().
# Returns 0 (proceed) if encoding is likely worth it, 1 (skip) if estimated
# savings fall below MIN_SAVING_RATIO. Falls through to encode if bitrate
# cannot be probed.
prescan_worth_encoding() {
  local file="$1"

  local src_bps width height fps_raw fps
  width="$(probe_video "$file" width)"
  height="$(probe_video "$file" height)"
  src_bps="$(probe_video "$file" bit_rate)"
  [[ -z "$src_bps" || "$src_bps" == "N/A" ]] && src_bps="$(probe_container_bitrate "$file")"
  [[ -z "$src_bps" || ! "$src_bps" =~ ^[0-9]+$ ]] && return 0  # can't estimate → proceed

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
    log "$(ts) PRESCAN: not worth encoding ($info) — skipping"
    return 1
  fi
  return 0
}

# Returns 0 (true) if the file should be encoded
should_encode() {
  local file="$1"

  local codec width filesize
  codec="$(probe_video "$file" codec_name)"
  width="$(probe_video "$file" width)"
  filesize="$(file_size "$file")"

  # Always skip non-H.264
  case "$codec" in
    h264|avc) ;;
    *) log "$(ts) SKIP  : codec=$codec (not H.264): $(basename "$file")"; return 1 ;;
  esac

  # Encode if 4K+
  if [[ -n "$width" && "$width" -ge 3840 ]]; then
    log "$(ts) QUEUE : 4K H.264 (${width}px wide): $(basename "$file")"
    return 0
  fi

  # Encode if over size threshold
  if [[ "$filesize" -ge "$SIZE_THRESHOLD" ]]; then
    local gb=$(( filesize / 1024 / 1024 / 1024 ))
    log "$(ts) QUEUE : H.264 over 7GB (~${gb}GB): $(basename "$file")"
    return 0
  fi

  log "$(ts) SKIP  : H.264 under threshold (${width}px, $(( filesize / 1024 / 1024 ))MB): $(basename "$file")"
  return 1
}

process_one() {
  local src="$1" dst="$2" file="$3"

  # If src is a single file rather than a directory, derive paths from its parent
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

  mkdir -p "$(dirname "$out")"

  # Skip if output exists AND it is not the same file (in-place MP4 case)
  if [[ -f "$out" && "$out" != "$file" ]]; then
    log "$(ts) SKIP  : output exists: $out"
    return 0
  fi

  if ! should_encode "$file"; then
    return 0
  fi

  if ! prescan_worth_encoding "$file"; then
    return 0
  fi

  # Probe source: bitrate, dimensions, framerate
  local src_bps width height fps
  width="$(probe_video "$file" width)"
  height="$(probe_video "$file" height)"
  src_bps="$(probe_video "$file" bit_rate)"
  if [[ -z "$src_bps" || "$src_bps" == "N/A" ]]; then
    src_bps="$(probe_container_bitrate "$file")"
  fi

  # Parse framerate (comes as fraction e.g. 24000/1001 or 30/1)
  local fps_raw
  fps_raw="$(probe_video "$file" r_frame_rate)"
  fps="$(python3 -c "
from fractions import Fraction
try:
    print(float(Fraction('${fps_raw:-30/1}')))
except:
    print(30.0)
")"

  # Calculate target bitrate
  local bitrate
  if [[ -n "$src_bps" && "$src_bps" =~ ^[0-9]+$ ]]; then
    bitrate="$(calc_bitrate "$src_bps" "$width" "$height" "$fps")"
  else
    # Can't probe bitrate — use cap as safe default
    if [[ "$width" -ge 3840 ]]; then
      bitrate="${BITRATE_CAP_4K}k"
    else
      bitrate="${BITRATE_CAP_HD}k"
    fi
    log "$(ts) WARN  : could not probe source bitrate, using cap: $bitrate"
    problem "$file" "WARN: could not probe source bitrate; used cap (${bitrate}) — verify output quality"
  fi

  # Determine H.265 profile from pixel format (preserve 10-bit if present)
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

  log "$(ts) START : $file"
  log "$(ts) INFO  : src_size=$(( src_size / 1024 / 1024 ))MB | ${width}x${height}px | fps=$fps | pix_fmt=$pix_fmt"

  # Select encoder
  local use_vt=0
  if check_videotoolbox; then
    use_vt=1
    log "$(ts) ENCODE: hevc_videotoolbox | target=$bitrate | profile=$profile"
  else
    log "$(ts) ENCODE: libx265 crf=21 medium (VideoToolbox unavailable) | profile=$profile"
  fi

  local encode_ok=0

  # Try audio copy first, fall back to AAC
  for audio_mode in copy aac; do
    local audio_flags=()
    if [[ "$audio_mode" == "copy" ]]; then
      audio_flags=(-c:a copy)
    else
      log "$(ts) AUDIO : copy failed, falling back to AAC"
      audio_flags=(-c:a aac -b:a 384k)
    fi

    if [[ "$use_vt" -eq 1 ]]; then
      if ff_run -y -i "$file" \
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
      if ff_run -y -i "$file" \
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
    log "$(ts) ERROR : encode failed for $file, skipping"
    problem "$file" "ERROR: encode failed (both audio-copy and AAC fallback) — file skipped"
    return 1
  fi

  # Size check: only replace if output is meaningfully smaller
  local out_size
  out_size="$(file_size "$tmp")"
  local threshold
  threshold=$(python3 -c "print(int($src_size * $MIN_SAVING_RATIO))")

  log "$(ts) SIZE  : src=$(( src_size / 1024 / 1024 ))MB out=$(( out_size / 1024 / 1024 ))MB (need < $(( threshold / 1024 / 1024 ))MB to replace)"

  if [[ "$out_size" -ge "$threshold" ]]; then
    rm -f "$tmp"
    log "$(ts) SKIP  : output not small enough to be worth replacing (< 10% saving), keeping original"
    local end elapsed
    end="$(date +%s)"; elapsed=$(( end - start ))
    log "$(ts) TIME  : ${elapsed}s"
    return 0
  fi

  log "$(ts) MOVE  : -> $out"
  mv -f "$tmp" "$out"

  if [[ -s "$out" ]]; then
    if [[ "$DELETE_SOURCE" == "1" ]]; then
      # Back up to one central _originals/ at the search root, preserving relative structure
      local orig_dir rel_dir
      rel_dir="$(dirname "$rel")"
      if [[ "$rel_dir" == "." ]]; then
        orig_dir="$src_base/_originals"
      else
        orig_dir="$src_base/_originals/$rel_dir"
      fi
      mkdir -p "$orig_dir"
      cp -f "$file" "$orig_dir/$(basename "$file")"
      log "$(ts) BACKUP: original -> $orig_dir"
      if [[ "$file" != "$out" ]]; then
        rm -f "$file"
        log "$(ts) DONE  : source removed (backed up to _originals)"
      else
        log "$(ts) DONE  : replaced in place (backed up to _originals)"
      fi
    else
      log "$(ts) DONE  : source kept"
    fi
  else
    log "$(ts) WARN  : output missing/empty — encode may have failed"
    problem "$file" "WARN: output missing or empty after encode — source not deleted"
    rm -f "$out" 2>/dev/null || true
  fi

  local end elapsed
  end="$(date +%s)"
  elapsed=$(( end - start ))
  log "$(ts) TIME  : ${elapsed}s"
}

export -f process_one should_encode prescan_worth_encoding probe_video probe_container_bitrate file_size check_videotoolbox calc_bitrate log ts ff_run problem
export FFMPEG FFPROBE TMPROOT SIZE_THRESHOLD DELETE_SOURCE PROBLEM_LOG
export BITRATE_FLOOR BITRATE_CAP_4K BITRATE_CAP_HD MIN_SAVING_RATIO BPP_HIGH BPP_LOW RATIO_HIGH RATIO_LOW

log "SRC:          $SRC"
log "DST:          $DST"
log "JOBS:         $JOBS"
log "DELETE_SOURCE: $DELETE_SOURCE"
log "BITRATE:      adaptive ${RATIO_HIGH}-${RATIO_LOW} of source | floor=${BITRATE_FLOOR}k | cap_4k=${BITRATE_CAP_4K}k | cap_hd=${BITRATE_CAP_HD}k"
log "MIN_SAVING:   output must be <$(python3 -c "print(int((1-$MIN_SAVING_RATIO)*100))")% smaller than source to replace"
log "TMP:          $TMPROOT"

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
