# very_thoughtful_compression

Selectively re-encodes fat H.264 video files from a folder down to a sane size with FFmpeg — to **H.265/HEVC** or back to **H.264/AVC** — at a named quality tier you choose per run. The script is `very_thoughtful_compression.sh`.

"Thoughtful" because it:

- gives you clear quality options based on real-world references
- only makes a new version when it is the quality you want AND a percentage smaller that you choose
- works out the target bitrate from the source's "bits-per-pixel-per-frame" — heavily compressed originals are not compressed harder, generously encoded originals get more headroom to shrink
- pre-scans each file, models the expected output size, and skips files that definitely won't meet your space-saving threshold before spending time encoding them
- preserves all subtitles — embedded when possible, sidecar `.srt` files otherwise
- fully configurable

**The two codecs in one line each:**

- **H.264 / AVC** — almost universally playable, but ~2× larger — and increasingly inefficient above 1080p (it was never designed for 4K+).
- **H.265 / HEVC** — about 50% smaller at the same quality on average; plays on anything reasonably modern (Apple devices since ~2015, all recent TVs, Plex/VLC/Infuse).

## What actually matters: codecs vs containers

Everyone wants the same four things — **quality, small size, fast encoding, wide compatibility** — but you can't max all four at once. Understanding the two independent choices behind every video file makes the trade-offs obvious.

- **The codec** (H.264, H.265, AV1, VP9, Xvid…) does the actual compression. It decides the **file size at a given quality** and **how long encoding takes**. Nearly all the size difference between two files comes from here.
- **The container** (MP4, MKV, WebM, AVI) is just the wrapper holding the video bitstream plus its audio and subtitle tracks. It adds only rounding-error overhead to the size — what it really decides is **compatibility**, **streaming behaviour**, and **which subtitle/audio track types can ride along**.

> **Myth: "MKV is smaller than MP4."** It isn't. The same H.264 video is the same size in either container. MKV files are often big because they're used for high-bitrate rips — not because of the container.

### At the same visual quality

Rough 1–10 scores (higher is better). **Space** = how small at equal quality · **Compat** = plays out-of-the-box across today's phones, TVs and browsers · **Stream** = progressive + adaptive (HLS/DASH) friendliness · **Speed** = encode speed, where ★ means it jumps to near-max *if your machine has a hardware encoder for that codec*.

| Era | Container | Codec | Space | Compat | Stream | Speed |
|-----|-----------|-------|:-:|:-:|:-:|:-:|
| **Modern standard** | MP4 | H.264 | 5.5 | 10 | 9.5 | 8 ★ |
| | MKV | H.264 | 5.5 | 7 | 4 | 8 ★ |
| **Efficiency** | MP4 | H.265 | 7.5 | 8 | 8 | 5 ★ |
| | MKV | H.265 | 7.5 | 6.5 | 4 | 5 ★ |
| | WebM | VP9 | 7 | 5.5 | 7 | 3 ★ |
| **Next-gen** | MP4 | AV1 | 9 | 5.5 | 7 | 2 ★ |
| | MKV | AV1 | 9 | 5 | 4.5 | 2 ★ |
| **Legacy** | AVI | Xvid / DivX | 3 | 6 | 2 | 8 |
| | MPG | MPEG-2 | 2 | 6.5 | 3 | 9 |

*(Scores are rough calibration for guidance, not precise benchmarks. Space efficiency is a property of the **codec** — it's identical across containers on the same row.)*

Reading it: **H.264** is the "just works everywhere" baseline, but the least space-efficient modern codec. **H.265** roughly halves the size and still plays on most 2015-and-newer hardware. **AV1** is smaller again, but hardware *decode* is only on very recent devices. **Legacy** codecs (Xvid, MPEG-2) are both bigger *and* older — almost always worth replacing.

### What do you actually want?

Pick what matters for *this* library — the tool can't guess it:

1. **Compatibility** — must it play on anything you own (→ MP4 + H.264), or is a modern-device-only library fine (→ H.265 / AV1)? Planning to **archive or re-edit** the footage? Consider working in **lossless** first (below) and only making a lossy copy for final delivery.
2. **Space saving** — as small as possible · a modest trim · keep the quality and only shave obvious fat · size doesn't matter.
3. **Encode speed** — as fast as possible (→ hardware encoder) · roughly real-time is fine · time is no object (→ software, best quality-per-bit).

### Platform sweet-spots

- **macOS** — MP4 with H.264 or H.265. Almost every Mac has a *hardware* encoder for one or both, so they're fast *and* high quality: choose H.264 for maximum compatibility, or H.265 when size matters more. Hardware AV1 encoding doesn't exist on Apple silicon yet.
- **Windows / Linux** — depends on your GPU. Recent NVIDIA / Intel / AMD chips add hardware **H.265** and often **AV1** (sometimes VP9), shifting the sweet-spot toward those for much smaller files at similar speed. Without a supported GPU, hardware H.264 or software H.265 is the practical choice.

## Quality tiers

Each tier is a **quality ceiling** anchored at a resolution, calibrated against streaming-service bitrates people already know. Sources **at or below** the tier's resolution come out imperceptibly different from the source; **bigger** sources are deliberately squeezed down to that grade — pick a higher tier if you want full 4K/8K fidelity preserved.

| Tier | Imperceptible at | Ceiling (H.264-equiv) | …as H.265 | Benchmark |
|------|------------------|----------------------|-----------|-----------|
| 1 STANDARD | 480p / 576p | 2.5 Mbps | ~1.4 Mbps | ≈ DVD / Netflix SD |
| 2 HIGH | 720p | 5 Mbps | ~2.8 Mbps | ≈ top Netflix/Amazon 720p |
| 3 EXCELLENT *(default)* | 1080p | 10 Mbps | ~5.5 Mbps | above top Netflix/Amazon 1080p (~6–8 Mbps), below Blu-ray |
| 4 STELLAR | 4K | 32 Mbps | ~16 Mbps | ≈ Netflix 4K UHD grade |
| 5 INSANE | 8K | 100 Mbps | ~45 Mbps | beyond streaming; archival |

The ceiling scales *down* per-pixel for sources smaller than the anchor (an SD file never gets 1080p-sized bitrate), and never scales up. Mbps figures are H.264-equivalent; for H.265 output the script automatically uses less bitrate for the same quality — ×0.55 at ≤1080p, ×0.50 at 4K, ×0.45 above, reflecting that HEVC's advantage over H.264 **grows** with resolution.

**Honesty notes.** "Imperceptible" assumes typical film/TV at 24–30 fps; very grainy or high-motion 50/60 fps material may need one tier up. Software encodes (libx264/libx265) use quality-targeted capped-CRF, which is slightly better than the hardware VideoToolbox encoder's average-bitrate targeting at the same ceiling. And the streaming benchmarks are what Netflix/Amazon actually deliver — our EXCELLENT ceiling sits *above* their 1080p top rate on purpose, since they hand-tune every title and we don't.

## Why "thoughtful"

The script never applies one dumb rule (like "half the bitrate") to every file. Per file, it:

1. **Measures how densely the source is already encoded** — bits per pixel per frame (bpp), from the real bitrate, resolution and frame rate.
2. **Leaves already-efficient files completely alone** — at/below 0.05 bpp a re-encode buys almost nothing and costs a generation of quality (`SKIP: already efficiently compressed`).
3. **Adapts the target to the source**: generously encoded sources (≥0.08 bpp) keep 55% of their bitrate, lean ones (≤0.03 bpp) keep 70%, linear in between — so moderately fat files shrink proportionally instead of all pinning to the ceiling.
4. **Caps at the tier ceiling** so nothing ever comes out bigger than the quality grade you asked for, with a hard floor (1500 kbps) so unusual inputs can't produce garbage.
5. **Pre-scans before encoding** — models the expected output size and skips files that won't meet your saving threshold, instead of discovering that after an hour of encoding.
6. **Verifies after encoding** — the source is only replaced if the new file actually exists, is non-empty, and is meaningfully smaller. Otherwise the original stays and the file is reported.
7. **Refuses to destroy subtitle tracks silently** — see below.

## What gets encoded

The scan covers `.mkv`, `.mp4`, `.mov`, `.avi`, `.webm`, `.m4v`, `.ts`, `.wmv`, `.flv`, recursively. Each file is sorted by its **video codec** into one of four actions:

| Source codec | Action |
|---|---|
| **H.264** (`h264`/`avc`) | **Shrink** if it's fat and worth it (see below). If it's already efficient but sits in a non-MP4 container, it's **remuxed** to MP4 instead. Already-efficient H.264 in MP4 is left alone. |
| **H.265 / AV1 / VP9** (modern, efficient) | **Never transcoded** — re-encoding these only costs a generation of quality. If in a non-MP4 container they're **remuxed** losslessly into MP4; if already MP4, left alone. |
| **Legacy / MP4-incompatible** (MPEG-2, VC-1, Xvid/DivX, WMV, MS-MPEG4, …) | **Transcoded** to your chosen codec at **maximum fidelity** (quality-targeted CRF capped at the *source's own* bitrate, so quality is preserved rather than squeezed to a tier) and written as MP4. |
| **Mezzanine / other** (ProRes, DNxHD, FFV1, raw, …) | Left untouched. |

The last two behaviours are opt-in, asked once at startup:

- *"If possible, convert files into MP4 for maximum compatibility with NO loss of quality?"* — the lossless **remux** (a fast `-c copy`, no re-encode; also fixes MP4 faststart). Default yes.
- *"If a file uses a codec incompatible with MP4, transcode it with maximum fidelity and convert it to MP4?"* — the legacy **transcode**. Default yes.

**When does a fat H.264 shrink?** All of these must hold — judged by content, not file size:

1. Its bpp is above the already-efficient floor (`BPP_SKIP_FLOOR`, default 0.05).
2. The pre-scan predicts the output will beat your minimum-saving threshold.

The minimum-saving prompt is codec-aware: a healthy H.265 re-encode saves 30–45%, so its default is **25%** (a smaller prediction means the source was already efficient); H.264→H.264 only trims fat, so its default is **15%**. The minimum-saving gate applies only to a shrink — a remux (lossless) and a compatibility transcode (fidelity-first) are kept regardless of size.

At the end of the run a **space-saved** summary reports the total original size, new size, and bytes/percent saved across every file replaced.

## Streams: video, audio, subtitles

**Video profiles.** H.265 output uses the `main` profile — or `main10` when the source is 10-bit — tagged `hvc1` so Apple players recognise it. H.264 output forces 8-bit `yuv420p` + High profile for maximum player compatibility.

**Audio.** All audio tracks are stream-copied untouched (original codec, channels and quality preserved). Only if the audio codec can't live in an MP4 container does the script fall back to re-encoding to AAC at 384 kbps.

**Subtitles.**

- **Text tracks** (SRT, ASS/SSA, WebVTT — typical in MKVs) are embedded into the MP4 as `mov_text`, all of them.
- If embedding fails, each text track is **extracted to a sidecar `.srt`** next to the output (`Name.eng.srt`, or `Name.1.eng.srt`, `Name.2.ger.srt`… when there are several) — Plex, VLC and Infuse pick these up automatically.
- **Image-based tracks** (PGS from Blu-ray, DVD/DVB bitmaps) can't exist in MP4 and can't become `.srt` without OCR. They are necessarily dropped from the output — **but the original file is then archived instead of deleted, even if you chose "delete originals"** (to `originals/` if you configured an archive folder, else `archived/` in the source root), so nothing is irrecoverably lost.
- Every one of these events is explained in the run report at the end.

## Requirements

- [`ffmpeg`](https://ffmpeg.org/download.html) and `ffprobe` with `libx265` / `libx264` support
- `python3` (standard library only)
- macOS (for VideoToolbox) or Linux (uses `libx265` / `libx264`)

## Usage

```bash
./very_thoughtful_compression.sh [SRC]
```

`SRC` is the directory to scan recursively (default: current directory). The script prompts interactively for all options on startup, grouped so they flow *quality → compatibility → execution → destination*:

**Quality**
- **Output codec** — H.265/HEVC (default) or H.264/AVC
- **Quality tier** — STANDARD / HIGH / EXCELLENT (default) / STELLAR / INSANE
- **Minimum size saving** — how much a re-encode must shrink to be kept; codec-aware defaults (25% for H.265, 15% for H.264)

**Compatibility**
- **Remux to MP4** — losslessly rehome MP4-friendly codecs (H.264/H.265/AV1/VP9) that sit in other containers (default yes)
- **Transcode incompatible codecs** — re-encode legacy/MP4-incompatible codecs (MPEG-2/VC-1/Xvid/WMV) at maximum fidelity (default yes)

**Execution**
- **Encoder** — Hardware (VideoToolbox, default) or Software (libx264/libx265) — only asked if a working hardware encoder is actually detected; see `FORCE_VT` below to skip this prompt
- **Parallel encoding jobs** — 1 (default), 2, or 4

**Destination**
- **Output location** — in place, or a separate folder (flat or mirrored structure)
- **Original handling** — archive to a folder, delete, or leave in place

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
| `FORCE_VT` | `1` to force VideoToolbox, `0` to force software (`libx265` / `libx264`) — also skips the interactive encoder prompt entirely |
| `BITRATE_FLOOR` | Minimum target bitrate in kbps (default: `1500`) |
| `BPP_SKIP_FLOOR` | Sources at/below this bits-per-pixel-per-frame are already efficient and skipped (default: `0.050`) |
| `HEVC_EFFICIENCY_HD` | H.265 bitrate as a fraction of equivalent H.264, ≤1080p (default: `0.55`) |
| `HEVC_EFFICIENCY_4K` | …at ≤4K (default: `0.50`) |
| `HEVC_EFFICIENCY_8K` | …above 4K (default: `0.45`) |
| `BPP_HIGH` | Bits-per-pixel threshold for maximum compression ratio (default: `0.08`) |
| `BPP_LOW` | Bits-per-pixel threshold for minimum compression ratio (default: `0.03`) |
| `RATIO_HIGH` | Compression ratio at `BPP_HIGH` (default: `0.55` — 55% of source) |
| `RATIO_LOW` | Compression ratio at `BPP_LOW` (default: `0.70` — 70% of source) |

## Bitrate model (the maths)

```
cap_kbps = tier_mbps * 1000 * min(src_pixels / tier_pixels, 1.0) * codec_factor
codec_factor = 1.0 (H.264) | 0.55 ≤1080p / 0.50 ≤4K / 0.45 >4K (H.265)
target = max(min(BITRATE_FLOOR, cap), min(src_kbps * ratio, cap))
```

where `ratio` comes from the source's bpp:

| Source bpp | Compression ratio applied |
|------------|--------------------------|
| ≥ 0.08 (generous) | 55% of source bitrate |
| ≤ 0.03 (lean) | 70% of source bitrate |
| Between | Linear interpolation |

If the source bitrate cannot be probed, the tier ceiling for that resolution is used as a safe fallback and the file is flagged in the run report.

## Encoding strategy

Attempts are ordered so a normal file takes exactly one pass: embed text subs + stream-copy audio → then AAC audio fallback → then (only if subtitles were the problem) the same two without embedded subs, followed by sidecar `.srt` extraction.

On macOS, VideoToolbox hardware encoding is used when available. Otherwise — or with `FORCE_VT=0` — software `libx265` / `libx264` runs at `crf=21` / `crf=20` `preset=medium`, quality-targeted but capped to the tier ceiling with `-maxrate` / `-bufsize`.

Output always includes `-movflags +faststart` so files are immediately streamable.

## Run report

At the end of each run the script prints a consolidated report of anything that needs your attention:

```
══════════════════════════════════════════════════════════
 RUN REPORT — 2 file(s) with notes, warnings or errors
   NOTE  = informational (e.g. why an original was archived)
   WARN  = worth a manual check
   ERROR = file could not be converted; source untouched
══════════════════════════════════════════════════════════
  [NOTE: original archived to /Volumes/NAS/Movies/archived instead of deleted — 2 image-based subtitle track(s) (hdmv_pgs_subtitle) cannot be carried into MP4]
    /Volumes/NAS/Movies/BluRayRip.mkv

  [ERROR: encode failed (both audio-copy and AAC fallback) — file skipped]
    /Volumes/NAS/Movies/Problematic.mp4

══════════════════════════════════════════════════════════
```

| Label | Meaning |
|-------|---------|
| `NOTE` (subtitles) | Subtitle tracks couldn't all be carried over; explains where the original went (archived, kept) or that sidecar `.srt` files were written. |
| `WARN` (bitrate) | Source bitrate could not be probed; the tier ceiling was used. Worth a manual check. |
| `WARN` (output) | Output file missing or empty after the encode — source was not deleted. |
| `ERROR` | All encode strategies failed; the source file was left untouched. |

## License

[MIT](LICENSE) — see the license file for the FFmpeg compatibility note.
