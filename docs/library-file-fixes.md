# Library file fixes — detection and repair reference for VTC Utilities

This is a curated, provenance-tagged reference of the video-repair and
faststart-remux logic already proven in four sibling tools, distilled so VTC's
**Utilities** mode can build its backend from real commands rather than
guesses. VTC's Utilities has two tools, both currently UI-only mocks:

1. **Faststart remux** — rewrite the `moov` atom to the front, optionally
   changing container.
2. **File-health scan → fix** — detect faults (chapters/markers past real
   duration, NAL-unit errors / ffmpeg exit 69, container/index faults), remux
   to repair, and re-encode at the file's existing bitrate/BPP only when a
   remux cannot fix it.

Everything below is drawn from the actual code. Where behaviour is not
established by the source, it says so.

## Provenance — the four repos

| Repo / file | What it is | Language | Relevance to VTC Utilities |
|---|---|---|---|
| `mp4-repair/repair_mp4.py` (+ `README.md`, `repair-guide.html`) | Deep MP4 repair: rebuilds a desynchronised H.264 sample index by parsing the elementary stream directly. Pure stream copy, no re-encode. | Python 3, stdlib only (`ffmpeg`/`ffprobe` on PATH) | Best basis for the **deepest tier** of health-fix, and for the *detection* of NAL/index damage. Refuses HEVC/VFR. |
| `everything_2_faststart_mp4/everything_2_faststart_mp4.sh` (+ `README.md`, `LICENSE`) | Batch converter/remuxer to faststart MP4 across MKV/AVI/MPG/WMV/MP4, with faststart detection, subtitle sidecars, archive/delete of originals. | Bash + small inline `python3` snippets | Best basis for **Faststart remux** (tool 1): the exact faststart detection and remux/container-change commands live here. |
| `fix-stash-video-errors/fix_stash_video_errors.sh` | Batch fixer that parses a Stash debug log, classifies errors (NAL / exit-69 / unreadable / missing), and re-encodes the bad files at their existing bitrate. | Bash + inline `python3` log parser | Best basis for the **re-encode-at-existing-BPP fallback** of tool 2, and the canonical NAL / exit-69 error signatures. |
| `partyviz/` | **Not relevant.** A live webcam "entrance visuals" capture app (OpenCV + MediaPipe pose/face embedding, person cropping). Its only `ffmpeg`/`subprocess` hits are camera capture and its launcher venv; nothing about video-file error detection or repair. Mentioned here only to record that it was checked and set aside. | Python | none |

> **Updated 2026-09-02:** the local `everything_2_faststart_mp4/` was stale; the
> latest version (pulled from Atlas) adds `CHANGELOG.md` and *fixes* the faststart
> detection this doc originally described. §1a and §2 below now reflect the
> corrected **atom-walk** detection and the CHANGELOG's real-library gotchas
> (`moov` > 4 MB, MKV Cues, temp-file placement, exit-status handling). Implement
> the atom-walk, not the byte-scan.

---

## 1. Error classes handled

Each class lists how it is **detected** and how it is **fixed**, with the exact
command and its source repo.

### 1a. moov atom not at front (not faststart)
*Provenance: `everything_2_faststart_mp4.sh` — `is_faststart()`.*

**Detect** — walk the top-level box list and see which of `moov` / `mdat` comes
first, reading only each box's 8-byte header and skipping by its declared size:

```python
# is_faststart(): walk top-level boxes (NOT a byte-scan)
#   size,typ = struct.unpack('>I4s', f.read(8))
#   size==1 → 64-bit largesize (read 8 more); size==0 → box runs to EOF
#   moov reached first → already faststart (skip); mdat first → needs the pass
```

Costs a handful of small header reads per file, regardless of size.

⚠️ **Do NOT use the old first-4 MB byte-scan** (`head.find(b'moov')` /
`b'mdat'`). The CHANGELOG documents why: in a faststart file `moov` comes first
and can be **4–8 MB** (sample tables scale with frame/packet count), so when
`moov` exceeds the 4 MB window `mdat` falls outside it, `find` returns −1, and the
file is misreported as *not* faststart — then rewritten on **every** run, forever.
Measured on a real 1,627-file library: **103 of 1,416 faststart files (7.3%)** hit
this, worst on long episodic TV; observed `moov` up to 7.68 MB. The atom-walk is
correct for any `moov` size.

**Fix (MP4)** — stream-copy remux with the faststart flag; the video map is also
optional so audio-only MP4s don't fail:

```sh
ffmpeg -i IN -map 0:v? -map 0:a? -map 0:s? -c copy -movflags +faststart -f mp4 OUT
```

**Fix (MKV)** — MKV has no `moov`/`mdat`; move the **Cues** index ahead of the
clusters in place (verified lossless — per-stream MD5 identical, chapters
preserved):

```sh
ffmpeg -i IN -map 0 -c copy -cues_to_front 1 OUT.mkv
```

The output is walked again before it replaces the source, so a pass that silently
fails to move `moov`/`Cues` is caught. The temp is a hidden, extension-less
`.<name>.faststart.part` **beside** the source (same filesystem → atomic rename),
deliberately outside the `*.mp4` scan glob so an in-flight temp is never picked up
as another input; ffmpeg's exit status and non-empty output are verified before
the `mv`.

### 1b. NAL-unit errors / desynchronised sample index
*Provenance: `mp4-repair/repair_mp4.py`, `repair-guide.html`; error strings also matched by `fix_stash_video_errors.sh`.*

The signature — from ffmpeg/VLC on decode:

```
[h264] Invalid NAL unit size (1917467621 > 77054)
[h264] Error splitting the input into NAL units
[h264] missing picture in access unit with size N
```

Meaning: an H.264 sample is a chain of length-prefixed NAL units (4-byte
big-endian length, that many payload bytes, repeat to end of sample). A wild
length like `1917467621` is ordinary payload being read as a length — the
container's `stsz` sizes / `stco` offsets no longer describe the bytes in
`mdat`. The payload is usually intact; only the map to it is wrong.

**Detect** — two levels:

- *Census / scope* (cheap, whole file). Run a full decode to null and count
  errors:

  ```sh
  ffmpeg -v error -i IN -f null -
  ```

  Lines containing `h264 @` / `aac @` are decode errors. Zero errors → nothing
  to repair.

- *Per-sample localisation* (`repair_mp4.py::chain_ok`). For every video packet
  (from `ffprobe -show_packets` giving `pos`,`size`), validate that the sample
  is a clean length-prefix chain, reading only the 4-byte headers, never the
  payload:

  ```python
  def chain_ok(mm, pos, size, nls=4):
      off = 0
      while off + nls <= size:
          L = int.from_bytes(mm[pos+off:pos+off+nls], 'big')
          if not (1 <= L <= 2_000_000) or off + nls + L > size:
              return False
          off += nls + L
      return off == size      # must land exactly on the sample end
  ```

  Consecutive failing samples are grouped into damaged byte-regions (with
  frame index, time span, and MB size reported).

**Fix** — index rebuild by parsing the elementary stream (no re-encode). The
`repair_mp4.py` pipeline, verbatim in intent:

1. **Localise** damaged spans via `chain_ok`.
2. **Recover** — `scan_runs`/`access_units`: scan each damaged span for maximal
   chains of ≥4 valid length-prefixed NALs (a 4-deep chain effectively never
   occurs in noise), grouped into access units (one coded picture each).
3. **Re-time** — `compute_ranks`: derive each recovered frame's *display* order
   from its picture order count, parsed from the slice header
   (`parse_sps`/`parse_pps`/`parse_slice`). **This step is load-bearing** (see
   the Annex-B trap, §3).
4. **Trim the seam** — `trim_to_block`: drop the last (usually half-overwritten)
   frame before damage, then trim until display ranks form a complete `0…n-1`
   block so segment joins can't overlap timestamps.
5. **Rebuild tables** — `build_moov`: write fresh `stts`, `ctts`, `stss`,
   `stsc`, `stsz`, `co64` and an edit list around the *original* sample bytes.
   Muxed via a final stream copy:

   ```sh
   # video-only, or with rebuilt audio mapped in:
   ffmpeg -v error -i VIDEO.mp4 [-i AUDIO.aac -map 0:v -map 1:a -bsf:a aac_adtstoasc] \
          -c copy -movflags +faststart OUT.mp4
   ```

Audio: AAC in MP4 has no sync words, so audio *inside* a damaged span cannot be
recovered; the tool substitutes exactly enough silence for the next real audio
frame to land where the picture resumes (`anullsrc` → aac → adts; priming
frames skipped). `--keep-audio-gap` drops it instead (shortens file, breaks
sync). Non-AAC audio is carried through untouched.

**Verify** — a clean decode is *not* enough; ordering must be checked too:

```sh
# 1. no decode errors
ffmpeg -v error -i OUT.mp4 -f null -
# 2. timing survived — expect pts != dts and has_b_frames=1
ffprobe -v error -select_streams v:0 -read_intervals %+2 \
        -show_entries packet=pts,dts -of csv=p=0 OUT.mp4
# 3. mean inter-frame difference over an undamaged stretch matches the original
```

### 1c. Truncated / structurally-damaged container
*Provenance: `mp4-repair/repair-guide.html` diagnosis ladder; `fix_stash_video_errors.sh` "broken" class.*

These look like 1b but are **not** index-desync and are not fixable by index
rebuild — rule them out first:

| Check | Command / method | Rules out |
|---|---|---|
| size vs bitrate | `ffprobe -show_format` — `duration × bitrate` should ≈ file size | truncated download |
| atom walk | parse top-level boxes to EOF; `mdat` should end exactly at EOF (watch the 64-bit form: `size==1`, real length in following 8 bytes) | truncation / structural damage |
| sample entry | inspect `stsd`; `encv`/`senc` ⇒ encrypted (DRM), `avc1` ⇒ plain | DRM |
| error census | `ffmpeg -v error -i F -f null -` | scope; shows if audio is hit too |

`fix_stash_video_errors.sh` detects the hard case from a Stash log line
`FFProbe encountered an error with <PATH>` and classifies the file **broken**
(unreadable). Its only "fix" is to *report* and optionally delete — a
genuinely broken/incomplete file is not repairable, re-download is the honest
answer.

### 1d. ffmpeg exit status 69 / frame-extraction failures
*Provenance: `fix_stash_video_errors.sh` (Category 1/2).*

**Detect** — from a log/stderr, any of these patterns marks a file
**re-encode**:

```
Invalid NAL | missing picture in access unit | Error splitting the input into NAL
exit status 69
ffmpeg command produced no output | failed to generate marker image.*error running ffmpeg | image: unknown format
```

`exit status 69` is Stash/ffmpeg's signal that thumbnail/marker generation
failed on a bitstream it couldn't decode cleanly — the same underlying class as
the NAL errors, surfaced at the application layer.

**Fix** — re-encode video in software at the source's own bitrate cap
(faststart in one pass), audio copied if possible else AAC:

```sh
# primary: keep audio
ffmpeg -y -i IN -c:v libx264 -crf 18 -maxrate <src>k -bufsize <2×src>k \
       -c:a copy -movflags +faststart TMP
# fallback: re-encode audio
ffmpeg -y -i IN -c:v libx264 -crf 18 -maxrate <src>k -bufsize <2×src>k \
       -c:a aac -b:a 384k -movflags +faststart TMP
```

`<src>` comes from `ffprobe format=bit_rate` (kbps), falling back to the video
stream's `stream=bit_rate`, and finally to an `8000k` cap (`bufsize` = 2×
maxrate = `16000k`). On success the temp replaces the file in place (`mv -f`).
Files that fail even this are offered for deletion.

> This is the "re-encode at the file's existing bitrate/BPP" fallback the brief
> asks for — **capped-CRF at the source bitrate**, so the output cannot exceed
> the input's rate. It matches VTC's own capped-CRF quality path (see
> `docs/quality-model.md`: `-crf 20/21 -maxrate <target> -bufsize`), differing
> only in that here the target is *the source's own bitrate* rather than a tier.

### 1e. Codec incompatible with MP4 (needs transcode to remux)
*Provenance: `everything_2_faststart_mp4.sh` — `needs_transcode_video()`.*

**Detect** — the primary video codec cannot be stream-copied into MP4:

```sh
ffprobe -v error -select_streams v:0 -show_entries stream=codec_name -of csv=p=0 IN
# wmv1|wmv2|wmv3|vc1|msmpeg4v1|msmpeg4v2|msmpeg4v3|msmpeg4  → must transcode
```

**Fix** — re-encode video (audio to AAC) at the source bitrate cap:

```sh
ffmpeg -y -i IN -map 0:v -map 0:a -c:v libx264 -crf 18 -preset slow \
       -maxrate <src>k -bufsize <2×src>k -c:a aac -b:a 384k OUT
```

Audio-only incompatibility (video copies fine, audio codec won't go into MP4)
is handled by a two-stage fallback: try `-c copy`; if that fails, retry with
`-c:v copy -c:a aac -b:a 384k`, logged as an `AUDIO` problem.

### 1f. Chapters / markers beyond real duration
**No existing tool on this machine detects or fixes this** — it is named in
VTC's Utilities spec but is **not implemented** in any of the four repos.
Provenance: absence confirmed by reading all four. VTC will have to build it.
The mechanical shape (not from source, offered as guidance): read chapter
markers via `ffprobe -show_chapters -of json`, read true media duration via
`ffprobe -show_format`/`-show_streams`, flag any chapter `start_time`/`end_time`
past the stream duration, and drop or clamp them with a metadata remux
(`ffmpeg -i IN -map_chapters -1 -c copy OUT` to strip all chapters, or write a
corrected chapter metadata file with `-map_metadata`/`-i chapters.txt`). This is
the one class with **no proven reference implementation** here.

---

## 2. Faststart remux (VTC tool 1)
*Provenance: `everything_2_faststart_mp4.sh`.*

**Need detection** — `is_faststart()` (see §1a): **atom-walk** of the top-level
boxes, faststart iff `moov` precedes `mdat` (NOT the old first-4 MB byte-scan —
that misfires when `moov` > 4 MB). MKV uses the EBML **Cues** equivalent
(`mkv_cues_front()`). Non-MP4 inputs going to MP4 always go through remux.

**The remux command** — for an existing MP4 that isn't faststart (optional video
map so audio-only files don't fail):

```sh
ffmpeg -i IN.mp4 -map 0:v? -map 0:a? -map 0:s? -c copy -movflags +faststart -f mp4 OUT.mp4
```

**Container change** — the same tool converts other containers *into* faststart
MP4. Stream-copy is tried first; codec-driven fallbacks kick in:

| Input | Strategy (in order) |
|---|---|
| `.mkv` | `-c copy` video+audio → MP4; on failure `-c:v copy -c:a aac -b:a 384k`; on failure full libx264 transcode |
| `.avi` `.mpg` `.mpeg` | `-c:v copy -c:a aac -b:a 384k`; on failure full libx264 transcode |
| `.wmv` (and vc1/wmv3/msmpeg4 in any container) | always `-c:v libx264 -crf 18 -preset slow -maxrate <src>k -bufsize <2×> -c:a aac -b:a 384k` (cannot stream-copy into MP4) |
| `.mp4` | faststart check + fix only, never re-encoded |

In `both` mode the tool remuxes to a temp, then — **only if the temp isn't
already faststart** — runs a second `-c copy -movflags +faststart` pass
(`-map 0`). This "check the intermediate before paying for a second pass" is the
main efficiency gotcha worth carrying into VTC.

**Edge cases / gotchas** (from the script + README, since there is no CHANGELOG):

- **Optional-stream mapping.** `-map 0:a?` / `-map 0:s?` prevent video-only
  files from erroring — a real trap if VTC hard-codes `-map 0:a`.
- **Use the atom-walk detection, not a byte-scan** (see §1a): the first-4 MB
  scan misreported **7.3% of a real 1,627-file library** (`moov` > 4 MB, up to
  7.68 MB) and reprocessed them forever. The walk reads only box headers.
- **MKV faststart = Cues to front**, not a container change:
  `ffmpeg -i IN -map 0 -c copy -cues_to_front 1` (lossless stream copy). Only MP4
  uses `-movflags +faststart`. A file that fails the Cues walk is usually
  genuinely corrupt (also fails `ffprobe`), not merely unoptimised — report it.
- **Temp file must sit outside the scan glob.** Write a hidden, extension-less
  `.<name>.faststart.part` beside the source; a `*.faststart.tmp.mp4` temp gets
  swept up by the `*.mp4` `find` and reprocessed. Verify ffmpeg exit status +
  non-empty output + re-check faststart before the atomic `mv`.
- **Local temp then move.** All work happens in `/tmp/mp4work`, then `mv -f` to
  destination — built so network drives see one sequential write, not partial
  writes. VTC already has this concern solved in `vtc/netmove.py`.
- **Subtitle sidecars.** Text subtitle tracks (subrip/ass/ssa/webvtt/mov_text/
  text) are exported to `NAME.subNN.<lang>.srt` next to the output; image subs
  are left in place. VTC's `resolve_container`/`_select_subs` already covers
  this ground.
- **Empty-output guard.** If the output MP4 is missing/empty, the source is
  kept and a `WARN` is logged — never delete the original before confirming a
  non-empty result.

---

## 3. MP4 repair (`repair_mp4.py`) — strategy, interface, deps
*Provenance: `mp4-repair/`.*

**Scope / refusals.** H.264 video + AAC audio, **constant frame rate only**. It
explicitly `die()`s on: non-H.264 video, HEVC, variable frame rate
(`d[0] != d[-1]`), `pic_order_cnt_type 1`, unsupported NAL length size, negative
composition offsets, a non-self-contained trailing segment. These are hard
guard-rails, not silent best-effort.

**What it detects.** A sample index (`stsz`/`stco`) desynchronised from `mdat`,
localised per-sample via `chain_ok` and grouped into contiguous damaged regions
(§1b).

**Repair pipeline** (single strategy, no re-encode fallback):
localise → recover NAL runs → regroup into access units → re-time from picture
order count → trim seam → rebuild sample tables → mux with `-c copy -movflags
+faststart`. It rebuilds `moov` by hand (`build_moov` writes `ftyp`+`mdat`+`moov`
directly), then re-muxes through ffmpeg so the output is a normal faststart MP4.

**The one trap it exists to avoid** (from `repair-guide.html` §05 and the
README): do **not** route recovered video through a raw Annex-B `.h264` file and
`-c copy`. Annex-B carries no timestamps, so ffmpeg sets `pts = dts`, flattening
`ctts`, and every B-frame then displays in decode order — decodes clean, plays
*worse* than the broken original. The fix is to reconstruct display order from
the picture order count and write `ctts` yourself:

```python
delay = max(1, max(i - s['rank'] for i, s in enumerate(samples)))
for i, s in enumerate(samples):
    s['ctts'] = (s['rank'] + delay - i) * tick   # must be >= 0
```

**CLI / interface:**

```sh
python3 repair_mp4.py INPUT.mp4                 # -> "INPUT (repaired).mp4"
python3 repair_mp4.py INPUT.mp4 -o OUT.mp4
python3 repair_mp4.py INPUT.mp4 --dry-run       # diagnose only, writes nothing
python3 repair_mp4.py INPUT.mp4 --keep-audio-gap
```

Never modifies the input; refuses to write over it; writes nothing if the file
is healthy; ends with a decode pass and prints the residual error count.

**Dependencies:** Python 3 **stdlib only** (`argparse, json, mmap, os, struct,
subprocess, sys, tempfile`) plus `ffmpeg`/`ffprobe` on PATH. No third-party
packages. This makes it near drop-in for VTC's Python package.

---

## 4. Batch video-error fix (`fix_stash_video_errors.sh`)
*Provenance: `fix-stash-video-errors/`.*

**What it is.** A one-shot batch fixer keyed off a **Stash debug log** (default
`~/Library/Log/Stash/stash-debug.log`). It is log-driven, not a filesystem
scan — it fixes exactly the files Stash flagged.

**Parse** (inline `python3`, ANSI-stripped, only lines containing `ERRO`):

| Category | Log signature | Action |
|---|---|---|
| re-encode | `Invalid NAL` / `missing picture in access unit` / `Error splitting the input into NAL` / `exit status 69` / `ffmpeg command produced no output` / `failed to generate marker image…` / `image: unknown format` | re-encode (§1d) |
| broken | `FFProbe encountered an error with <PATH>` | report; optional delete (unreadable — `broken` takes priority over `reencode` for the same path) |
| missing | `no such file or directory` | report only (already gone) |
| app-level | missing scene IDs, duplicate performers | ignored (not a video-file problem) |

File paths are pulled from the log with several regexes (all anchored to
`/Volumes/…`): the `ffmpeg -i` argument, angle-bracketed `-i`, quoted paths, and
the `FFProbe encountered an error with <…>` form.

**Commands it runs.** Bitrate probe then capped-CRF software re-encode, exactly
as §1d. Everything is interactive (prompts to re-encode / to delete broken /
to delete files that failed re-encode). Output replaces the source in place.
Reminder printed at the end to re-run Stash "Generate".

**Reusability.** The *classification table and error signatures* are the
valuable part for VTC — the log-parsing front-end is Stash-specific and should
be dropped. The re-encode command is directly reusable (and already mirrors
VTC's own capped-CRF path).

---

## 4b. IMPLEMENTED — what actually shipped, and what was verified

`vtc/utilities.py` (engine), `--faststart` / `--health` / `--fix` / `--deep` (CLI),
`Api.utility_scan` / `utility_fix` / `stop_utility` (GUI). Both tools are
**report-first**: they look at a whole library, and one that starts by rewriting
files is not one anyone can safely try.

**Faststart detection is a structural walk, and it was verified against the real
library**, because the failure mode is a false positive that rewrites a healthy
file on every run for ever:

- MP4 — walk the top-level boxes, first of `moov`/`mdat` wins. Cross-checked
  against libavformat's own parser on real episodes: agreed on every file. The
  synthetic 5 MB-`moov` case (the one the old byte-scan gets wrong) is pinned as a
  regression test — the walk says faststart, the byte-scan says it needs fixing.
- MKV — walk the Segment's children, first of `Cues`/`Cluster` wins. **A raw byte
  search for the Cues ID is NOT a valid check**: on a real 1.1 GB episode it hits
  at offset 93, which is inside the *SeekHead*, where that ID is stored as a
  pointer. The genuine `Cues` element was at byte 1,133,004,673 — after 1,044
  clusters. A scanner is fooled by the pointer; the structural walk is not.
- Anything unreadable returns **None** (unknown), never `False`. "I cannot tell"
  must not collapse into "needs fixing".

Measured on the real library: **319 of 3,717 files (8.6%)** have their index at the
back, almost all MKV WEB-DLs, which is normal for that source and exactly what
`-cues_to_front 1` addresses.

**The remux is lossless, and that was checked rather than assumed.** A real 1,133 MB
episode with 2 audio and 30 subtitle tracks: every stream byte-identical by
per-stream MD5 before and after, duration unchanged, no temp left behind. The
output is walked again before it replaces the source, so a pass that silently
fails to move the index is caught instead of shipped.

**Health checks, cheapest first**, with the honest limits stated:

| Fault | Detection | Remedy |
|---|---|---|
| chapters past the real end | `-show_chapters` vs duration, 1s slack so rounding is not "damage" | remux |
| truncated container | last top-level box ends past EOF | **none** — a half-downloaded file is gone, not broken |
| unreadable | probe fails (structural check still runs first, so "300 MB short" beats "unreadable") | none |
| bitstream damage | decode census, bounded window | re-encode, opt-in |

The decode census is the only check that reads the media rather than the headers,
so it is **off by default** and bounded when on — and a bounded read can only find
damage inside the window it read, which is stated rather than hidden.

**The repair ladder never escalates on its own**: remux first (seconds, lossless),
and a re-encode only if explicitly enabled — capped-CRF at *the file's own bitrate*,
so a repair never doubles as a shrink. A fault with no automatic remedy says so
instead of burning an hour proving it.

---

## 5. Mapping onto VTC's Utilities

### Tool 1 — Faststart remux
**Best basis: `everything_2_faststart_mp4.sh`.**
- Need-detection: port `is_faststart()` (§1a) — or, for rigour, an atom walk.
- Remux: `ffmpeg -i IN -map 0:v -map 0:a? -map 0:s? -c copy -movflags +faststart OUT`.
- Container-change variants and the codec-incompatibility fallbacks (§1e, §2)
  give VTC the "optionally changing container" half for free.
- **Gaps VTC fills:** wire it through `vtc/encode.py`'s existing `_run_ffmpeg`
  (abortable Popen, progress) and `vtc/netmove.py` (safe network move) instead
  of the bash temp/mv machinery; reuse VTC's subtitle/container logic
  (`resolve_container`, `_select_subs`) rather than the script's sidecar loop.

### Tool 2 — Health scan → remux → re-encode-at-existing-BPP fallback
A three-rung ladder, each rung from a different repo:

1. **Scan / detect** — census (`ffmpeg -v error -i F -f null -`) for scope; the
   diagnosis ladder (§1c) to separate truncation/DRM from index desync; per-
   sample `chain_ok` (from `repair_mp4.py`) to localise NAL/index damage;
   codec check (`needs_transcode_video`) for MP4-incompatibility; **new work**
   for chapters-past-duration (§1f).
2. **Remux to repair** — for faststart/moov and container faults, the §2 remux.
   For a desynchronised H.264 index, the full `repair_mp4.py` rebuild is the
   "remux/deeper-repair without re-encoding" path.
3. **Re-encode at existing BPP (fallback)** — when a remux can't fix it, the
   `fix_stash_video_errors.sh` / legacy-format command:
   `-c:v libx264 -crf 18 -maxrate <src>k -bufsize <2×>k -c:a copy|aac
   -movflags +faststart`, with `<src>` from `ffprobe format=bit_rate` (video-
   stream fallback, then an 8000k cap).

**Best basis per rung:** detection → `repair_mp4.py` + the guide's ladder;
lossless repair → `repair_mp4.py` (index) and `everything_2_faststart_mp4.sh`
(faststart/container); re-encode fallback → `fix_stash_video_errors.sh`.

**Gaps VTC must fill:**
- **Scan-only vs scan-&-fix modes.** Only `repair_mp4.py --dry-run` has a true
  scan-only mode. VTC's health scan needs a report-first pass across a whole
  library (the bash tools are either fix-in-place or log-driven).
- **Chapters/markers past duration** — no reference implementation (§1f).
- **HEVC/VFR index repair** — `repair_mp4.py` refuses both; VTC inherits that
  limit unless it extends the parser.
- **Re-encode "at existing BPP" vs VTC tiers.** The existing tools cap at the
  *source's own bitrate* (capped-CRF), which is exactly "existing BPP." VTC
  should keep that semantics for repair (don't shrink to a tier while fixing) —
  it already has capped-CRF plumbing in `vtc/encode.py` / `docs/quality-model.md`,
  so the only new bit is feeding *source bitrate* as the cap instead of a tier
  target.

---

## 6. Reuse notes — languages, deps, licensing

| Repo | Language / deps | Drop-in for VTC (Python)? |
|---|---|---|
| `repair_mp4.py` | Python 3, **stdlib only** + ffmpeg/ffprobe | **Yes, nearly drop-in.** Import as a module under `vtc/` (e.g. `vtc/repair.py`); replace its `subprocess.run` calls with VTC's `_run_ffmpeg` and add `NO_WINDOW`/`TEXT_UTF8` from `vtc/winproc.py` for Windows parity (see how `vtc/ffprobe.py` already does this). Replace `die()`/`sys.exit` with exceptions so it never kills the app. |
| `everything_2_faststart_mp4.sh` | Bash + inline `python3` | **Adapt, don't port line-for-line.** The *logic* (faststart detection, remux command ladder, codec-incompatibility table, empty-output guard) transfers cleanly to `vtc/encode.py`; the bash orchestration (xargs `-P`, `/tmp` temp, `mv`, interactive prompts, `find` pruning) is replaced by VTC's pipeline/netmove. `is_faststart` becomes a small Python function. |
| `fix_stash_video_errors.sh` | Bash + inline `python3` | **Reuse the signatures, drop the shell.** The error-classification table (§4) and the capped-CRF re-encode command port directly; the Stash-log front-end is not wanted. |
| `partyviz/` | Python (OpenCV/MediaPipe) | **Not applicable** — unrelated to video-file repair. |

**Shared ffmpeg/ffprobe conventions already in VTC** (so new code should match):
- `vtc/ffprobe.py::probe()` runs one `ffprobe -v error -show_streams
  -show_format -of json` per file into a `MediaInfo` dataclass, never raising
  (errors land in `.ok`/`.error`). Health-scan detection should extend this
  rather than re-shell ffprobe ad hoc.
- `vtc/encode.py::_run_ffmpeg` runs ffmpeg via a registered abortable `Popen`
  (so a run can be cancelled), and VTC already emits `-movflags
  +faststart+use_metadata_tags` on MP4 output — the faststart flag is native to
  the codebase.
- All ffmpeg/ffprobe invocations go through `winproc.NO_WINDOW`/`TEXT_UTF8` on
  Windows.

**Licensing.** `everything_2_faststart_mp4/LICENSE` is **MIT, © 2026 Simon
Davis**, with a note that it invokes FFmpeg as an external process but does not
bundle it — so no GPL/LGPL entanglement from linking. `repair_mp4.py` and
`fix_stash_video_errors.sh` carry no in-file licence header on this machine;
they are the same author's work in the same `~/projects` tree, so reuse inside
VTC is unencumbered, but if VTC ships them it should add an explicit MIT header
to match. As long as ffmpeg stays an external binary (as it already is in VTC),
there is no copyleft obligation on VTC's own code.
