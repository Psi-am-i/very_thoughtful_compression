# thoughtful_h264_to_h265_conversion

Selectively re-encodes H.264 MP4 files to H.265/HEVC using FFmpeg. "Thoughtful" because it only encodes files where the saving is genuinely worth the CPU cost — it pre-scans each file, models the expected output size, and skips files that won't meet your threshold before spending time encoding them.

## Features

- **Selective queuing** — only processes 4K H.264 files or files over a configurable size threshold (default 4 GB); all other H.264 and non-H.264 files are left alone
- **Pre-scan** — estimates expected output size before committing to a full encode; skips files unlikely to meet the saving threshold
- **Adaptive bitrate model** — target bitrate is derived from the source's bits-per-pixel-per-frame, not a fixed number, so dense and lean sources are treated differently
- **Apple VideoToolbox** — uses `hevc_videotoolbox` hardware encoder when available; falls back to software `libx265`
- **10-bit preservation** — detects 10-bit pixel formats (`yuv420p10le` etc.) and selects the `main10` HEVC profile automatically
- **Safe replacement** — output replaces the source only if it is meaningfully smaller (configurable ratio threshold)
- **Original backup** — optionally copies source files to `_originals/` (preserving relative directory structure) before deleting them
- **Problem summary** — prints a consolidated list of files that need attention at the end of each run

## Requirements

- [`ffmpeg`](https://ffmpeg.org/download.html) and `ffprobe` with `libx265` support
- `python3` (standard library only)
- macOS (for VideoToolbox) or Linux (uses `libx265`)

## Usage

```bash
./h264_to_h265.sh [SRC] [DST] [JOBS]
```

| Argument | Default | Description |
|----------|---------|-------------|
| `SRC` | `.` | Source directory (or single file) to process |
| `DST` | Same as `SRC` | Destination root for output files; defaults to in-place replacement |
| `JOBS` | Prompted | Number of parallel encoding jobs |

The script prompts interactively on first run. Set the environment variables below to skip all prompts for scripted/scheduled use.

### Environment variables

| Variable | Values | Description |
|----------|--------|-------------|
| `JOBS` | integer | Number of parallel jobs — skips the prompt |
| `MIN_SAVING_RATIO` | `0.0`–`1.0` | Output must be smaller than `source_size × ratio` to replace the source. Default `0.85` (requires 15% saving). |
| `MIN_SAVING_SET` | any non-empty value | Set to skip the saving-threshold prompt |
| `DELETE_SOURCE` | `0` / `1` | Remove (and back up) source after a successful encode |
| `DELETE_SOURCE_SET` | any non-empty value | Set to skip the delete-source prompt |
| `FFMPEG` | path | Override the `ffmpeg` binary location |
| `FFPROBE` | path | Override the `ffprobe` binary location |

### Examples

```bash
# Interactive
./h264_to_h265.sh /Volumes/NAS/Movies

# Non-interactive: 1 job, keep source, require 20% saving
JOBS=1 MIN_SAVING_RATIO=0.80 MIN_SAVING_SET=1 DELETE_SOURCE=0 DELETE_SOURCE_SET=1 \
  ./h264_to_h265.sh /Volumes/NAS/Movies

# Single file
./h264_to_h265.sh /Volumes/NAS/Movies/BigFilm.mp4
```

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
3. The pre-scan estimates the output will be at least `MIN_SAVING_RATIO` smaller than the source

Files in H.265, VP9, AV1, or any non-H.264 codec are skipped without modification.

## Encoding strategy

The script tries audio-copy first (preserving the original audio track without re-encoding), then falls back to AAC at 384 kbps if the audio codec is incompatible with the MP4 container.

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
