# thoughtful_h264_to_h265_conversion

Selectively re-encodes H.264 MP4 files to H.265/HEVC using FFmpeg. "Thoughtful" because it:

- only makes an H.265 version when it is the same perceptual quality AND substantially smaller
- uses the source's bits-per-pixel-per-frame to derive the target bitrate — heavily compressed originals are not compressed harder, while generously encoded originals get more headroom to shrink
- pre-scans each file, models the expected output size, and skips files that definitely won't meet your saving threshold before spending time encoding them
- fully configurable

## Features

- **Selective queuing** — only processes 4K H.264 files or files over a configurable size threshold (default 4 GB); all other H.264 and non-H.264 files are left alone
- **Pre-scan** — estimates expected output size before committing to a full encode; skips files unlikely to meet the saving threshold
- **Adaptive bitrate model** — target bitrate is derived from the source's bits-per-pixel-per-frame, not a fixed number, so dense and lean sources are treated differently
- **Apple VideoToolbox** — uses `hevc_videotoolbox` hardware encoder when available; falls back to software `libx265`
- **10-bit preservation** — detects 10-bit pixel formats (`yuv420p10le` etc.) and selects the `main10` HEVC profile automatically
- **Video-only support** — audio map uses optional (`?`) flag so video-only files encode without errors
- **Safe replacement** — output replaces the source only if it is meaningfully smaller (configurable ratio threshold)
- **Original archiving** — optionally moves source files to an archive folder (preserving relative directory structure) before replacing them
- **Safe from home directory** — `~/Library` and other macOS system folders are excluded from the `find` scan
- **Problem summary** — prints a consolidated list of files that need attention at the end of each run

## Requirements

- [`ffmpeg`](https://ffmpeg.org/download.html) and `ffprobe` with `libx265` support
- `python3` (standard library only)
- macOS (for VideoToolbox) or Linux (uses `libx265`)

## Usage

```bash
./h264_to_h265.sh [SRC]
```

`SRC` is the directory to scan recursively (default: current directory). The script prompts interactively for all options on startup:

- **Output location** — in place, or a separate folder (flat or mirrored structure)
- **Original handling** — archive to a folder, delete, or leave in place
- **Parallel encoding jobs** — 1 (default), 2, or 4
- **Minimum size saving** — how much smaller the output must be to replace the source (default: 15%)

### Graceful stop

```bash
touch /tmp/hevc_stop   # finish current file(s), skip the rest
rm /tmp/hevc_stop      # clear the stop flag to resume/re-run
```

### Environment variables

| Variable | Description |
|----------|-------------|
| `FFMPEG` | Override the `ffmpeg` binary path |
| `FFPROBE` | Override the `ffprobe` binary path |
| `FORCE_VT` | `1` to force VideoToolbox, `0` to force software `libx265` |
| `BITRATE_FLOOR` | Minimum target bitrate in kbps (default: `3800`) |
| `BITRATE_CAP_HD` | Maximum target bitrate for HD (≤ 1080p) in kbps (default: `8000`) |
| `BITRATE_CAP_4K` | Maximum target bitrate for 4K (≥ 2160p) in kbps (default: `16000`) |
| `BPP_HIGH` | Bits-per-pixel threshold for maximum compression ratio (default: `0.08`) |
| `BPP_LOW` | Bits-per-pixel threshold for minimum compression ratio (default: `0.03`) |
| `RATIO_HIGH` | Compression ratio at `BPP_HIGH` (default: `0.55` — 55% of source) |
| `RATIO_LOW` | Compression ratio at `BPP_LOW` (default: `0.70` — 70% of source) |

## Bitrate model

Target bitrate is derived from the source's **bits per pixel per frame (bpp)**:

| Source bpp | Compression ratio applied |
|------------|--------------------------|
| ≥ 0.08 (generous) | 55% of source bitrate |
| ≤ 0.03 (lean) | 70% of source bitrate |
| Between | Linear interpolation |

Hard limits ensure quality floors and caps:

| | Floor | Cap (HD ≤ 1080p) | Cap (4K ≥ 2160p) |
|-|-------|-----------------|-----------------|
| Bitrate | 3800 kbps | 8000 kbps | 16000 kbps |

If the source bitrate cannot be probed, the relevant cap is used as a safe fallback (and the file is flagged in the problem summary).

## What gets encoded

A file is queued for encoding only if it meets **all** of the following:

1. The video codec is H.264 (`h264` / `avc`)
2. Either: the source width is ≥ 3840 px (4K), **or** the file size is ≥ 4 GB
3. The pre-scan estimates the output will be meaningfully smaller than the source (per `MIN_SAVING_RATIO`)

Files in H.265, VP9, AV1, or any non-H.264 codec are skipped without modification.

## Encoding strategy

The script tries audio stream-copy first (preserving the original audio without re-encoding), then falls back to AAC at 384 kbps if the audio codec is incompatible with the MP4 container.

On macOS, VideoToolbox hardware encoding is used when available. On Linux or when VideoToolbox is unavailable, `libx265 crf=21 preset=medium` is used.

Output always includes `-movflags +faststart` so files are immediately streamable.

## Problem summary

At the end of each run the script prints a summary of any files that triggered a warning or error:

```
══════════════════════════════════════════════════════════
 PROBLEMS — 2 file(s) need attention
══════════════════════════════════════════════════════════
  [WARN: could not probe source bitrate; used cap (8000k) — verify output quality]
    /Volumes/NAS/Movies/Corrupted.mp4

  [ERROR: encode failed (both audio-copy and AAC fallback tried) — file skipped]
    /Volumes/NAS/Movies/Problematic.mp4

══════════════════════════════════════════════════════════
```

| Label | Meaning |
|-------|---------|
| `WARN` (bitrate) | Source bitrate could not be probed; the bitrate cap was used. Output may be over-compressed or over-sized — worth a manual check. |
| `ERROR` | Both encode strategies failed; the source file was left untouched. |
| `WARN` (output) | Output file is missing or empty after the encode completed — source was not deleted. |

## License

[MIT](LICENSE) — see the license file for the FFmpeg compatibility note.
