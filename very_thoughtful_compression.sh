#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════════════════
# thoughtful-shrink.sh — Batch re-encode fat H.264 video files to a sane size
#                        (H.265/HEVC or H.264/AVC, at a chosen quality tier)
#
# USAGE:
#   ./thoughtful-shrink.sh [SRC]
#
#   SRC   Directory to scan (prompted for if not given; default: current dir)
#
# The script prompts interactively for: output codec (H.265 or H.264), quality
# tier (STANDARD/HIGH/EXCELLENT/STELLAR/INSANE — each a resolution-anchored
# quality ceiling), output location, what to do with originals, parallel jobs,
# and minimum saving. Set FORCE_VT=1/0 to force or disable hardware
# (VideoToolbox) encoding.
#
# Text subtitles are embedded into the MP4 (mov_text), or extracted to sidecar
# .srt files if embedding fails. Image subtitles (PGS/DVD) can't live in MP4;
# when any subtitle track would be lost the original is archived — even in
# delete mode — and the run report explains why.
#
# GRACEFUL STOP:
#   touch /tmp/hevc_stop     — finish current file(s), skip the rest
#   rm /tmp/hevc_stop        — clear the stop flag to resume/re-run
# ══════════════════════════════════════════════════════════════════════════════
set -euo pipefail

SRC="${1:-}"
if [[ -z "$SRC" ]]; then
  printf '\nDirectory to scan for videos (recursively)?\n  (default: current directory — %s)\n' "$PWD" >&2
  read -r -p "  Path: " SRC </dev/tty 2>/dev/tty || SRC=""
  SRC="${SRC:-.}"
fi
SRC="${SRC/#\~/$HOME}"        # expand a typed leading ~
SRC="${SRC%/}"
if [[ ! -d "$SRC" ]]; then
  printf 'ERROR: "%s" is not a directory\n' "$SRC" >&2
  exit 1
fi
TMPROOT="/tmp/hevcwork"

# ── Bitrate strategy ──────────────────────────────────────────────────────────
# The target bitrate comes from a quality TIER chosen per-run (see prompt below).
# Each tier is an ABSOLUTE quality ceiling anchored at a resolution, calibrated
# against streaming-service bitrates (Netflix/Amazon): sources at or below the
# anchor resolution stay imperceptibly different from the source; larger sources
# are deliberately squeezed down to that grade (pick a higher tier to keep full
# 4K/8K quality). Sources smaller than the anchor scale down per-pixel so an SD
# file never gets 1080p-sized bitrate:
#
#   cap_kbps = tier_mbps*1000 * min(src_pixels / tier_pixels, 1.0) * codec_factor
#
# codec_factor is 1.0 for H.264 output. For H.265 it reflects that HEVC reaches
# H.264 quality at roughly half the bitrate — and that H.264's efficiency falls
# off above 1080p (it was never designed for 4K+), so the HEVC advantage grows:
#     <= 1080p -> 0.55      <= 4K -> 0.50      above -> 0.45
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
HEVC_EFF_HD="${HEVC_EFFICIENCY_HD:-0.55}"   # H.265/H.264 bitrate parity <= 1080p
HEVC_EFF_4K="${HEVC_EFFICIENCY_4K:-0.50}"   # ... <= 4K
HEVC_EFF_8K="${HEVC_EFFICIENCY_8K:-0.45}"   # ... above 4K
BPP_HIGH=0.08
BPP_LOW=0.03
RATIO_HIGH=0.55
RATIO_LOW=0.7

# ── Interactive prompts ───────────────────────────────────────────────────────

printf '\nOutput codec?\n  1) H.265 / HEVC  — ~50%% smaller at the same quality; plays on anything reasonably modern  [default]\n  2) H.264 / AVC   — almost universally playable, but ~2x larger — and increasingly inefficient\n                     above 1080p (for 4K+ sources H.265 is strongly recommended)\n' >&2
read -r -p "  Choice [1/2]: " _cchoice </dev/tty 2>/dev/tty || _cchoice=1
case "${_cchoice:-1}" in
  2) OUT_CODEC=h264 ;;
  *) OUT_CODEC=h265 ;;
esac
export OUT_CODEC

printf '\nQuality tier? Each is a quality ceiling: sources at or below its resolution come out\nimperceptibly different from the source; bigger sources are deliberately squeezed down\nto that grade. (Mbps shown are H.264-equivalent; H.265 output automatically uses\nproportionally less bitrate for the same quality.)\n  1) STANDARD   — imperceptible at 480p/576p   (~2.5 Mbps ≈ DVD / Netflix SD)\n  2) HIGH       — imperceptible at 720p        (~5 Mbps   ≈ top Netflix/Amazon 720p)\n  3) EXCELLENT  — imperceptible at 1080p       (~10 Mbps  — above top Netflix/Amazon 1080p)  [default]\n  4) STELLAR    — imperceptible at 4K          (~32 Mbps  ≈ Netflix 4K UHD grade)\n  5) INSANE     — imperceptible at 8K          (~100 Mbps — beyond streaming; archival)\n' >&2
read -r -p "  Choice [1-5]: " _tchoice </dev/tty 2>/dev/tty || _tchoice=3
case "${_tchoice:-3}" in
  1) TIER_NAME=STANDARD;  TIER_MBPS=2.5; TIER_PIXELS=$((1024*576));   TIER_RES_LABEL="480p/576p" ;;
  2) TIER_NAME=HIGH;      TIER_MBPS=5;   TIER_PIXELS=$((1280*720));   TIER_RES_LABEL="720p" ;;
  4) TIER_NAME=STELLAR;   TIER_MBPS=32;  TIER_PIXELS=$((3840*2160));  TIER_RES_LABEL="4K" ;;
  5) TIER_NAME=INSANE;    TIER_MBPS=100; TIER_PIXELS=$((7680*4320));  TIER_RES_LABEL="8K" ;;
  *) TIER_NAME=EXCELLENT; TIER_MBPS=10;  TIER_PIXELS=$((1920*1080));  TIER_RES_LABEL="1080p" ;;
esac
export TIER_NAME TIER_MBPS TIER_PIXELS TIER_RES_LABEL

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

# Sensible minimum saving depends on the codec: a healthy H.265 re-encode saves
# 30-45%, so a small predicted saving means the source was already efficient and
# re-encoding would only cost a generation of quality. H.264->H.264 can only trim
# fat, so smaller savings are still worthwhile.
if [[ "$OUT_CODEC" == "h265" ]]; then
  printf '\nMinimum size saving required to keep the new file?\n  (H.265 typically saves 30-45%%; below ~25%% the source was already efficient)\n  1) 25%%  [default]\n  2) 15%%\n  3) 35%%\n  4) None — always keep if encode succeeds\n' >&2
  read -r -p "  Choice [1/2/3/4]: " _mschoice </dev/tty 2>/dev/tty || _mschoice=1
  case "${_mschoice:-1}" in
    2) MIN_SAVING_RATIO="0.85" ;;
    3) MIN_SAVING_RATIO="0.65" ;;
    4) MIN_SAVING_RATIO="1.00" ;;
    *) MIN_SAVING_RATIO="0.75" ;;
  esac
else
  printf '\nMinimum size saving required to keep the new file?\n  1) 15%%  [default]\n  2) 10%%\n  3) 5%%\n  4) None — always keep if encode succeeds\n' >&2
  read -r -p "  Choice [1/2/3/4]: " _mschoice </dev/tty 2>/dev/tty || _mschoice=1
  case "${_mschoice:-1}" in
    2) MIN_SAVING_RATIO="0.90" ;;
    3) MIN_SAVING_RATIO="0.95" ;;
    4) MIN_SAVING_RATIO="1.00" ;;
    *) MIN_SAVING_RATIO="0.85" ;;
  esac
fi
export MIN_SAVING_RATIO

# ── Setup ─────────────────────────────────────────────────────────────────────

FFMPEG="${FFMPEG:-ffmpeg}"
FFPROBE="${FFPROBE:-ffprobe}"

mkdir -p "$TMPROOT"

# Sweep orphaned scratch files left by previously killed/crashed runs (a hard
# SIGKILL or power loss bypasses the per-file cleanup trap below). Safe because
# this run hasn't created any temps yet — but skip if another instance of this
# script is already running, so we don't delete its in-progress work.
_others="$(pgrep -f 'thoughtful-shrink.sh' 2>/dev/null | grep -vw "$$" | wc -l | tr -d ' ')" || true
if [[ "${_others:-0}" -eq 0 ]]; then
  _swept="$(find "$TMPROOT" -maxdepth 1 -type f -name '*.mp4' 2>/dev/null | wc -l | tr -d ' ')"
  if [[ "${_swept:-0}" -gt 0 ]]; then
    find "$TMPROOT" -maxdepth 1 -type f -name '*.mp4' -delete 2>/dev/null || true
    printf '\nSwept %s orphaned scratch file(s) from %s\n' "$_swept" "$TMPROOT" >&2
  fi
fi

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
tier_px = $TIER_PIXELS
eff_px  = min(pixels, tier_px) if pixels > 0 else tier_px
if '$OUT_CODEC' == 'h265':
    if   eff_px <= 1920*1080: codec_factor = $HEVC_EFF_HD
    elif eff_px <= 3840*2160: codec_factor = $HEVC_EFF_4K
    else:                     codec_factor = $HEVC_EFF_8K
else:
    codec_factor = 1.0
cap    = $TIER_MBPS * 1000 * (eff_px / tier_px) * codec_factor
floor  = min($BITRATE_FLOOR, cap)
kbps   = max(floor, min(src_kbps * ratio, cap))
print(int(kbps))
print(f'bpp={bpp:.4f} ratio={ratio:.0%} cap={int(cap)}k src={src_kbps:.0f}k -> target={int(kbps)}k')
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
tier_px = $TIER_PIXELS
eff_px  = min(pixels, tier_px) if pixels > 0 else tier_px
if '$OUT_CODEC' == 'h265':
    if   eff_px <= 1920*1080: codec_factor = $HEVC_EFF_HD
    elif eff_px <= 3840*2160: codec_factor = $HEVC_EFF_4K
    else:                     codec_factor = $HEVC_EFF_8K
else:
    codec_factor = 1.0
cap    = $TIER_MBPS * 1000 * (eff_px / tier_px) * codec_factor
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

# Whether a file is worth encoding is judged by content, not size: any H.264
# source passes here, then prescan_worth_encoding decides using bpp and the
# predicted saving. Non-H.264 codecs (HEVC, VP9, AV1, ...) are never touched.
should_encode() {
  local file="$1"
  local n; n="$(sname "$file")"

  local codec
  codec="$(probe_video "$file" codec_name)"

  case "$codec" in
    h264|avc) return 0 ;;
    *) log "$(ts) SKIP  : codec=$codec (not H.264) — $n"; return 1 ;;
  esac
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

  # ── Subtitles ──────────────────────────────────────────────────────────────
  # Text-based tracks (SRT/ASS/...) can be embedded in MP4 as mov_text, or
  # extracted to sidecar .srt files. Image-based tracks (PGS/DVD/DVB) can do
  # neither without OCR — if any would be lost, the original must survive.
  local subs_probe text_sub_idx="" text_sub_count=0 image_sub_count=0 image_sub_codecs=""
  subs_probe="$("$FFPROBE" -v error -select_streams s \
    -show_entries stream=index,codec_name -of csv=p=0 "$file" 2>/dev/null || true)"
  local _sidx _scodec
  while IFS=, read -r _sidx _scodec; do
    [[ -z "$_sidx" ]] && continue
    case "$_scodec" in
      subrip|srt|ass|ssa|mov_text|webvtt|text)
        text_sub_idx="${text_sub_idx:+$text_sub_idx }$_sidx"
        text_sub_count=$(( text_sub_count + 1 )) ;;
      *)
        image_sub_count=$(( image_sub_count + 1 ))
        image_sub_codecs="${image_sub_codecs:+$image_sub_codecs, }${_scodec:-unknown}" ;;
    esac
  done <<< "$subs_probe"
  if [[ "$text_sub_count" -gt 0 || "$image_sub_count" -gt 0 ]]; then
    log "$(ts) SUBS  : found ${text_sub_count} text / ${image_sub_count} image subtitle track(s) — $n"
  fi

  local bitrate
  if [[ -n "$src_bps" && "$src_bps" =~ ^[0-9]+$ ]]; then
    bitrate="$(calc_bitrate "$src_bps" "$width" "$height" "$fps")"
  else
    bitrate="$(python3 -c "
w=$width; h=$height
tier_px=$TIER_PIXELS
eff_px=min(w*h, tier_px) if w*h > 0 else tier_px
if '$OUT_CODEC' == 'h265':
    if   eff_px <= 1920*1080: cf = $HEVC_EFF_HD
    elif eff_px <= 3840*2160: cf = $HEVC_EFF_4K
    else:                     cf = $HEVC_EFF_8K
else:
    cf = 1.0
cap=$TIER_MBPS*1000*(eff_px/tier_px)*cf
print(f'{int(cap)}k')
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

  # Remove this worker's scratch file if it is interrupted mid-encode (Ctrl-C,
  # graceful stop, TERM) so partial temps never accumulate. A successful encode
  # moves $tmp to $out first, making this a harmless no-op.
  trap 'rm -f "$tmp"' EXIT INT TERM

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

  # Attempt matrix: embed text subs first (as mov_text), retry without subs only
  # if that fails; within each, try audio stream-copy first, then AAC fallback.
  # A normal file still takes exactly one attempt.
  local encode_ok=0 subs_embedded=0 sub_mode audio_mode
  local sub_attempts="none"
  [[ "$text_sub_count" -gt 0 ]] && sub_attempts="embed none"

  for sub_mode in $sub_attempts; do
    local sub_maps=() sub_flags=()
    if [[ "$sub_mode" == "embed" ]]; then
      for _sidx in $text_sub_idx; do sub_maps+=(-map "0:${_sidx}"); done
      sub_flags=(-c:s mov_text)
    fi
    for audio_mode in copy aac; do
      local audio_flags=()
      if [[ "$audio_mode" == "copy" ]]; then
        audio_flags=(-c:a copy)
      else
        log "$(ts) RETRY : audio copy failed, falling back to AAC — $n"
        audio_flags=(-c:a aac -b:a 384k)
      fi

      if ff_run_progress "$n" "$dur_secs" "$start" \
          -y -i "$file" \
          -map 0:v:0 -map 0:a? ${sub_maps[@]+"${sub_maps[@]}"} \
          "${vargs[@]}" \
          "${audio_flags[@]}" ${sub_flags[@]+"${sub_flags[@]}"} \
          -movflags +faststart \
          "$tmp"; then
        encode_ok=1
        [[ "$sub_mode" == "embed" ]] && subs_embedded=1
        break 2
      fi
      rm -f "$tmp"
    done
    if [[ "$sub_mode" == "embed" ]]; then
      log "$(ts) RETRY : embedding subtitles failed, retrying without — $n"
    fi
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

  # Sidecar fallback: embedding failed but the encode succeeded — extract each
  # text track from the SOURCE (must run before it can be overwritten below) to
  # .srt files next to the output; Plex/VLC/Infuse pick these up automatically.
  local sidecars_made=0 subs_sidecar_fail=0
  if [[ "$text_sub_count" -gt 0 && "$subs_embedded" -eq 0 ]]; then
    local k=0 _lang sidecar
    for _sidx in $text_sub_idx; do
      k=$(( k + 1 ))
      _lang="$("$FFPROBE" -v error -select_streams "$_sidx" \
        -show_entries stream_tags=language -of csv=p=0 "$file" 2>/dev/null | head -1)"
      _lang="${_lang:-und}"
      if [[ "$text_sub_count" -eq 1 ]]; then
        sidecar="${out%.mp4}.${_lang}.srt"
      else
        sidecar="${out%.mp4}.${k}.${_lang}.srt"
      fi
      if ff_run -y -i "$file" -map "0:${_sidx}" -c:s srt "$sidecar"; then
        sidecars_made=$(( sidecars_made + 1 ))
      else
        rm -f "$sidecar"
        subs_sidecar_fail=$(( subs_sidecar_fail + 1 ))
      fi
    done
    if [[ "$sidecars_made" -gt 0 ]]; then
      log "$(ts) SUBS  : could not embed — extracted ${sidecars_made} sidecar .srt file(s) — $n"
      problem "$file" "NOTE: ${sidecars_made} text subtitle track(s) written as sidecar .srt files (could not be embedded in the MP4)"
    fi
  fi

  # Anything about to be irrecoverably lost? Image subs never make it into MP4;
  # text subs that failed both embed and sidecar extraction are lost too.
  local subs_dropped_reason=""
  if [[ "$image_sub_count" -gt 0 ]]; then
    subs_dropped_reason="${image_sub_count} image-based subtitle track(s) (${image_sub_codecs}) cannot be carried into MP4"
  fi
  if [[ "$subs_sidecar_fail" -gt 0 ]]; then
    subs_dropped_reason="${subs_dropped_reason:+$subs_dropped_reason; }${subs_sidecar_fail} text subtitle track(s) could not be embedded or extracted"
  fi

  # Rescue: when the output overwrites the source path directly, the original
  # (and its subtitle tracks) would vanish at the mv below — move it to the
  # archive first, whatever the chosen source action.
  local rescued_dir=""
  if [[ -n "$subs_dropped_reason" && "$file" == "$out" ]]; then
    local fb_root fb_rel fb_dest
    fb_root="${ARCHIVE_DIR:-$src/archived}"
    fb_rel="$(dirname "$rel")"; [[ "$fb_rel" == "." ]] && fb_rel=""
    fb_dest="${fb_root}${fb_rel:+/$fb_rel}"
    mkdir -p "$fb_dest"
    mv -f "$file" "$fb_dest/$(basename "$file")"
    rescued_dir="$fb_dest"
  fi

  mv -f "$tmp" "$out"

  if [[ -s "$out" ]]; then
    case "${SOURCE_ACTION:-archive}" in
      delete)
        if [[ -n "$subs_dropped_reason" ]]; then
          if [[ -z "$rescued_dir" ]]; then
            local fb_root fb_rel
            fb_root="${ARCHIVE_DIR:-$src/archived}"
            fb_rel="$(dirname "$rel")"; [[ "$fb_rel" == "." ]] && fb_rel=""
            rescued_dir="${fb_root}${fb_rel:+/$fb_rel}"
            mkdir -p "$rescued_dir"
            mv -f "$file" "$rescued_dir/$(basename "$file")"
          fi
          log "$(ts) DONE  : ${elapsed}s  [$(fsize "$out")]  $n  (original ARCHIVED, not deleted — subtitles would be lost)"
          problem "$file" "NOTE: original archived to ${rescued_dir} instead of deleted — ${subs_dropped_reason}"
        else
          [[ "$file" != "$out" ]] && rm -f "$file"
          log "$(ts) DONE  : ${elapsed}s  [$(fsize "$out")]  $n  (original deleted)"
        fi
        ;;
      archive)
        local arc_rel arc_dest
        arc_rel="$(dirname "$rel")"; [[ "$arc_rel" == "." ]] && arc_rel=""
        arc_dest="${ARCHIVE_DIR}${arc_rel:+/$arc_rel}"
        mkdir -p "$arc_dest"
        [[ "$file" != "$out" && -f "$file" ]] && mv -f "$file" "$arc_dest/$(basename "$file")"
        log "$(ts) DONE  : ${elapsed}s  [$(fsize "$out")]  $n  (original → $(basename "$ARCHIVE_DIR"))"
        if [[ -n "$subs_dropped_reason" ]]; then
          problem "$file" "NOTE: output MP4 is missing subtitle track(s) — ${subs_dropped_reason}; the archived original still has them"
        fi
        ;;
      keep)
        if [[ -n "$rescued_dir" ]]; then
          log "$(ts) DONE  : ${elapsed}s  [$(fsize "$out")]  $n  (original moved to archive — subtitles would be lost)"
          problem "$file" "NOTE: original moved to ${rescued_dir} (it was being overwritten in place) — ${subs_dropped_reason}"
        else
          log "$(ts) DONE  : ${elapsed}s  [$(fsize "$out")]  $n"
          if [[ -n "$subs_dropped_reason" ]]; then
            problem "$file" "NOTE: output MP4 is missing subtitle track(s) — ${subs_dropped_reason}; the original (kept in place) still has them"
          fi
        fi
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
export FFMPEG FFPROBE TMPROOT SOURCE_ACTION ARCHIVE_DIR \
       OUTPUT_MODE OUTPUT_DIR OUTPUT_FLAT PROBLEM_LOG STOP_FILE TTY
export BITRATE_FLOOR BPP_SKIP_FLOOR MIN_SAVING_RATIO BPP_HIGH BPP_LOW RATIO_HIGH RATIO_LOW
export HEVC_EFF_HD HEVC_EFF_4K HEVC_EFF_8K
export OUT_CODEC TIER_NAME TIER_MBPS TIER_PIXELS TIER_RES_LABEL

# (plain case block — a case inside $() breaks macOS bash 3.2's parser)
case "$SOURCE_ACTION" in
  delete) _orig_desc="delete" ;;
  keep)   _orig_desc="keep" ;;
  *)      _orig_desc="archive → $ARCHIVE_DIR" ;;
esac

log ""
log "SRC:         $SRC"
log "OUTPUT:      $( [[ "$OUTPUT_MODE" == "separate" ]] && echo "$OUTPUT_DIR ($( [[ $OUTPUT_FLAT == 1 ]] && echo flat || echo mirrored ))" || echo "in place" )"
log "ORIGINALS:   $_orig_desc"
log "JOBS:        $JOBS"
log "CODEC:       $( [[ "$OUT_CODEC" == "h264" ]] && echo "H.264 / AVC (universal playback, ~2x larger)" || echo "H.265 / HEVC (~50% smaller at the same quality)" )"
log "TIER:        ${TIER_NAME} — imperceptible at ≤ ${TIER_RES_LABEL}; ceiling ${TIER_MBPS} Mbps H.264-equiv$( [[ "$OUT_CODEC" == "h265" ]] && python3 -c "
px=$TIER_PIXELS
cf=$HEVC_EFF_HD if px<=1920*1080 else ($HEVC_EFF_4K if px<=3840*2160 else $HEVC_EFF_8K)
print(f' (→ ~{$TIER_MBPS*cf:.1f} Mbps H.265)', end='')" )"
log "BITRATE:     ≤ tier ceiling (scaled down for smaller sources) | adaptive ${RATIO_HIGH}-${RATIO_LOW} of source below it | floor=${BITRATE_FLOOR}k"
log "SKIP-EFFIC.: sources already ≤ ${BPP_SKIP_FLOOR} bpp are left untouched (already well compressed)"
log "MIN_SAVING:  output must be ≥$(python3 -c "print(int((1-$MIN_SAVING_RATIO)*100))")% smaller than source to replace it"
log "SUBTITLES:   text subs embedded (mov_text) or extracted to .srt; if image subs (PGS/DVD)"
log "             would be lost, the original is archived — even in delete mode"
log "STOP:        touch $STOP_FILE   (finish current file, skip the rest)"
log "             rm $STOP_FILE      (clear stop flag to re-run)"
log ""

find "$SRC" \
  \( -name '.Trashes' -o -name '.Spotlight-V100' -o -name '.fseventsd' -o -name '.TemporaryItems' \
     -o -name 'originals' -o -name 'new versions' -o -name 'archived' -o -name 'Library' \) -prune \
  -o \( -type f -not -name '._*' \( -iname "*.mkv" -o -iname "*.mp4" \) -print0 \) \
  | xargs -0 -n 1 -P "$JOBS" bash -c 'process_one "$@"' _ "$SRC" || true
# `|| true`: a failed encode makes process_one (and so xargs) exit non-zero,
# which under set -e would kill the script here — before the run report prints.

if [[ -s "$PROBLEM_LOG" ]]; then
  count="$(wc -l < "$PROBLEM_LOG" | tr -d ' ')"
  log ""
  log "══════════════════════════════════════════════════════════"
  log " RUN REPORT — ${count} file(s) with notes, warnings or errors"
  log "   NOTE  = informational (e.g. why an original was archived)"
  log "   WARN  = worth a manual check"
  log "   ERROR = file could not be converted; source untouched"
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
