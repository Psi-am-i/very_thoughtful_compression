# thoughtful-shrink

Selectively re-encodes fat H.264 video files down to a sane size with FFmpeg — to **H.265/HEVC** (smallest) or back to **H.264/AVC** (universal playback), at a quality tier you choose per run. "Thoughtful" because it:

- lets you target a familiar quality tier (5 / 8 / 10 Mbps in H.264-at-1080p terms) and translates it to the right bitrate for the codec — for H.265 it automatically uses ~half, since HEVC reaches the same quality at roughly half the bitrate
- scales the bitrate cap with resolution, so 1080p and 4K are both handled by one rule
- uses the source's bits-per-pixel-per-frame to derive the target bitrate — heavily compressed originals are not compressed harder, while generously encoded originals get more headroom to shrink
- leaves **already-efficient** sources completely alone (a bpp floor), instead of re-encoding them for a negligible gain and a generation of quality loss
- pre-scans each file, models the expected output size, and skips files that won't meet your saving threshold before spending time encoding them
- only replaces a source when the new file is meaningfully smaller
- fully configurable

## Features

- **Codec choice** — output H.265/HEVC (smallest files, needs a reasonably modern player) or H.264/AVC (~2× larger, direct-plays on virtually anything)
- **Quality tiers** — pick 5, 8, or 10 (H.264 @ 1080p Mbps equivalent); for H.265 output the script uses ~half that bitrate for the same quality
- **Resolution-scaled cap** — the tier becomes a per-pixel ceiling, so 4K automatically gets proportionally more headroom than 1080p
- **Selective queuing** — only processes 4K H.264 files or files over a configurable size threshold (default 4 GB); all other H.264 and non-H.264 files are left alone
- **Adaptive bitrate model** — below the cap, the target is derived from the source's bits-per-pixel-per-frame, so dense and lean sources are treated differently
- **Already-compressed guard** — sources whose bpp is already at/below a floor are skipped outright (`SKIP: already efficiently compressed`)
- **Pre-scan** — estimates expected output size before committing to a full encode; skips files unlikely to meet the saving threshold
- **Apple VideoToolbox** — uses the `hevc_videotoolbox` / `h264_videotoolbox` hardware encoder when available; falls back to software `libx265` / `libx264` (CRF quality-targeted but capped to the tier via `-maxrate`)
- **10-bit preservation** — for H.265, detects 10-bit pixel formats (`yuv420p10le` etc.) and selects the `main10` profile; for H.264 output, forces 8-bit `yuv420p` + High profile for maximum player compatibility
- **Video-only support** — audio map uses the optional (`?`) flag so video-only files encode without errors
- **Safe replacement** — output replaces the source only if it is meaningfully smaller (configurable ratio threshold)
- **Original archiving** — optionally moves source files to an archive folder (preserving relative directory structure) before replacing them
- **Safe from home directory** — `~/Library` and other macOS system folders are excluded from the `find` scan
- **Problem summary** — prints a consolidated list of files that need attention at the end of each run

## Requirements

- [`ffmpeg`](https://ffmpeg.org/download.html) and `ffprobe` with `libx265` / `libx264` support
- `python3` (standard library only)
- macOS (for VideoToolbox) or Linux (uses `libx265` / `libx264`)

## Usage

```bash
./thoughtful-shrink.sh [SRC]
```

`SRC` is the directory to scan recursively (default: current directory). The script prompts interactively for all options on startup:

- **Output codec** — H.265/HEVC (default) or H.264/AVC
- **Quality tier** — 5, 8 (default), or 10 (H.264 @ 1080p Mbps equivalent)
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
| `FORCE_VT` | `1` to force VideoToolbox, `0` to force software (`libx265` / `libx264`) |
| `BITRATE_FLOOR` | Minimum target bitrate in kbps (default: `1500`) |
| `BPP_SKIP_FLOOR` | Sources at/below this bits-per-pixel-per-frame are already efficient and skipped (default: `0.050`) |
| `HEVC_EFFICIENCY` | H.265 bitrate as a fraction of the equivalent H.264 bitrate (default: `0.55`) |
| `BPP_HIGH` | Bits-per-pixel threshold for maximum compression ratio (default: `0.08`) |
| `BPP_LOW` | Bits-per-pixel threshold for minimum compression ratio (default: `0.03`) |
| `RATIO_HIGH` | Compression ratio at `BPP_HIGH` (default: `0.55` — 55% of source) |
| `RATIO_LOW` | Compression ratio at `BPP_LOW` (default: `0.70` — 70% of source) |

## Bitrate model

The quality **tier** you pick is an H.264-at-1080p figure. It becomes a per-pixel ceiling that scales with resolution, and — for H.265 output — is multiplied by `HEVC_EFFICIENCY`:

```
cap_kbps = tier_mbps * 1000 * (pixels / 1920x1080) * codec_factor
codec_factor = HEVC_EFFICIENCY (H.265) or 1.0 (H.264)
```

So an "8" tier is ~8000 kbps for H.264 output or ~4400 kbps for H.265 at 1080p, and proportionally more at 4K.

Below that cap, the target is still trimmed by the source's **bits per pixel per frame (bpp)** so moderately fat files shrink proportionally rather than all pinning to the ceiling:

| Source bpp | Compression ratio applied |
|------------|--------------------------|
| ≥ 0.08 (generous) | 55% of source bitrate |
| ≤ 0.03 (lean) | 70% of source bitrate |
| Between | Linear interpolation |

A hard floor (`BITRATE_FLOOR`, capped to never exceed the tier) prevents garbage output on unusual inputs. If the source bitrate cannot be probed, the tier cap for that resolution is used as a safe fallback (and the file is flagged in the problem summary).

## What gets encoded

A file is queued for encoding only if it meets **all** of the following:

1. The video codec is H.264 (`h264` / `avc`)
2. Either: the source width is ≥ 3840 px (4K), **or** the file size is ≥ 4 GB
3. Its bpp is above `BPP_SKIP_FLOOR` (not already efficiently compressed)
4. The pre-scan estimates the output will be meaningfully smaller than the source (per `MIN_SAVING_RATIO`)

Files in H.265, VP9, AV1, or any non-H.264 codec are skipped without modification.

## Encoding strategy

The script tries audio stream-copy first (preserving the original audio without re-encoding), then falls back to AAC at 384 kbps if the audio codec is incompatible with the MP4 container.

On macOS, VideoToolbox hardware encoding is used when available. On Linux or when VideoToolbox is unavailable — or with `FORCE_VT=0` — software `libx265` / `libx264` is used at `crf=21` / `crf=20` `preset=medium`, quality-targeted but capped to the tier ceiling with `-maxrate` / `-bufsize`.

Output always includes `-movflags +faststart` so files are immediately streamable.

## Problem summary

At the end of each run the script prints a summary of any files that triggered a warning or error:

```
══════════════════════════════════════════════════════════
 PROBLEMS — 2 file(s) need attention
══════════════════════════════════════════════════════════
  [WARN: could not probe source bitrate; used tier cap (4400k) — verify output quality]
    /Volumes/NAS/Movies/Corrupted.mp4

  [ERROR: encode failed (both audio-copy and AAC fallback) — file skipped]
    /Volumes/NAS/Movies/Problematic.mp4

══════════════════════════════════════════════════════════
```

| Label | Meaning |
|-------|---------|
| `WARN` (bitrate) | Source bitrate could not be probed; the tier cap was used. Output may be over-compressed or over-sized — worth a manual check. |
| `ERROR` | Both encode strategies failed; the source file was left untouched. |
| `WARN` (output) | Output file is missing or empty after the encode completed — source was not deleted. |

## License

[MIT](LICENSE) — see the license file for the FFmpeg compatibility note.
