# Quality model — how tiers, targets, and re-encode decisions work

This documents the bitrate/quality logic now implemented in the `vtc` Python
engine (`vtc/model.py`, `vtc/pipeline.py`, `vtc/encode.py`) — the reference spec
that the retired `very_thoughtful_compression.sh` originally established.

## The core idea: a tier is a quality *density*, not a bitrate

A bitrate on its own is meaningless without knowing what it pays for — 8 Mbps is
fat at 720p, fine at 1080p, and starved at 4K. So a tier is defined as a **density**:
**bits per pixel per frame (bpp)** = `bitrate ÷ (pixels × fps)`. Because bpp is
normalised by resolution *and* frame rate, one tier scales to any source with no
per-resolution rules: a 4K file gets ~4× a 1080p file, a 60fps file ~2× a 30fps file.

### Why bpp is not the same number in every codec

The obvious objection: a decoded frame is a decoded frame. H.264 and H.265 both
hand back 1920×1080 pixels, the same raw bytes. Nothing is smaller after
decoding — so how can the same quality cost different bits?

Because bpp does not measure what the frame *holds*. It measures **how many bits
we had to spend describing it** well enough to rebuild. Both codecs produce a
full frame; neither produces the *same* frame. Each is an approximation of the
original, and the bits decide how close.

> Two people describe the same painting down a phone line. Both listeners end up
> with a canvas the same size. The better describer gets a closer likeness in
> fewer words. **Canvas size is the resolution — fixed. Word count is the
> bitrate. Likeness is the quality. The skill of the describer is the codec.**

So 0.077 bpp of H.265 and 0.129 bpp of H.264 look the same to you: different
bits, same likeness. H.265 spends them better — smarter prediction, variable
block sizes, better entropy coding.

This is why a tier cannot simply *be* a bpp. A tier is a **fidelity**; bpp is
what that fidelity costs in a particular codec.

### The quality number, and how it resolves

Each tier is a **quality number** — the H.264 bpp × 1000. It is codec-independent
and does not move when you change anything else; it names the likeness you want.

| Tier | Quality | H.264 bpp | 1080p30 H.264 |
|------|---------|-----------|---------------|
| OK | 64 | 0.0643 | 4.0 Mbps |
| GOOD | 80 | 0.0804 | 5.0 Mbps |
| EXCELLENT (default) | 109 | 0.1093 | 6.8 Mbps |
| STELLAR | 129 | 0.1286 | 8.0 Mbps |
| INSANE | 145 | 0.1447 | 9.0 Mbps |

From there, two steps and nothing else:

```
    codec bpp = quality ÷ 1000 × codec factor
    bitrate   = codec bpp × pixels × fps
```

The **codec factor** is what that codec's skill is worth. H.264 is the reference,
so its factor is 1.0. H.265 needs fewer bits for the same likeness, and its
advantage grows with frame size — more neighbouring pixels to predict from:

| Output codec | ≤1080p | ≤4K | above 4K |
|---|---|---|---|
| H.264 | 1.00 | 1.00 | 1.00 |
| H.265 | 0.60 | 0.50 | 0.45 |

So STELLAR (quality 129) resolves to:

| | bpp | 1080p24 | 4K24 |
|---|---|---|---|
| H.264 | 0.1286 | 6.4 Mbps | 25.6 Mbps |
| H.265 at ≤1080p | 0.0772 | 3.8 Mbps | — |
| H.265 at 4K | 0.0643 | — | 12.8 Mbps |

One quality number, one factor per codec and frame size, and the bitrate falls
out. Nothing else is tuned per resolution.

`H.264 bpp = ref_mbps × 1e6 ÷ (1920 × 1080 × 30)`, and `quality = bpp × 1000`.

EXCELLENT is calibrated so a generic ffmpeg encoder roughly matches Netflix's top
1080p rung (~5.8 Mbps, achieved with far more sophisticated per-shot encoding) —
6.8 Mbps gives the naïve encoder headroom to reach the same look. There is
deliberately **no lossy "archive" tier above INSANE**: true archival quality means
keeping the source lossless, not spending more lossy bitrate.

## The target: absolute, not relative

For a given file the target bitrate is:

```
target_kbps = tier_bpp × pixels × fps × codec_factor ÷ 1000
target_kbps = max(BITRATE_FLOOR, target_kbps)      # floor 1500 kbps
target_kbps = min(target_kbps, source_kbps)        # never inflate a source
```

`codec_factor` is 1.0 for H.264 output. For H.265 it reflects HEVC reaching the same
quality at less bitrate, with the advantage growing at higher resolution (validated
against coding-efficiency studies — theoretical ~50%, practical ~25–40% at HD):

| Output resolution | H.265 factor | ≈ saving vs H.264 |
|---|---|---|
| ≤ 1080p | 0.60 | 40% |
| ≤ 4K | 0.50 | 50% |
| > 4K | 0.45 | 55% |

**Critically, the target is a function of the tier and the source's pixels/fps — NOT
a fraction of the source's current bitrate.** This is what fixed the original bug:
the old logic computed `target = source_bitrate × ratio`, so every re-run re-anchored
to the (now smaller) file and shaved another ~40% off, cutting a file down across
successive runs (3.5 GB → 2.2 GB → 1.2 GB …) until it bottomed out near a bpp floor.

## Frame size: a cap on height, priced into the target

The walkthrough's **Frame Size** question (and `--max-height` on the CLI) caps the
output **height** in vertical pixels — the "p" in 1080p. The aspect ratio is always
kept, and nothing is ever upscaled: a source already at or below the cap is encoded
at its own size, so a 1080p cap over a mixed library only touches what is taller.

Because a tier is a *density*, the cap is a quality control and not a cosmetic one.
The target is struck against the frame that is about to be **written**, not the one
being read:

```
capped_dims(3840, 2160, 1080) -> (1920, 1080)    # landscape: the height is capped
capped_dims(2160, 3840, 1080) -> (1080, 1920)    # portrait:  the width is capped
target = bpp × (1920 × 1080) × fps × codec_factor(1920×1080)
```

Capping 4K at 1080p quarters the pixels, so it quarters the bitrate at the *same*
bits per pixel — the same picture quality in roughly a quarter of the bytes. (The
codec factor moves too: a downscaled 4K file now earns the HD HEVC factor, 0.60,
rather than the 4K one, 0.50 — so the real ratio is `4 × 0.50/0.60 ≈ 3.3×`.) The
`-vf scale=W:H:flags=lanczos` filter, the target, and the reported bpp all come from
the same `capped_dims()` call, so the report cannot disagree with the file.

Two consequences worth knowing:

- **A cap is not a licence to re-encode.** The gates below are unchanged, so a tall
  file already *below* the capped target is still left alone: rescaling it could not
  clear the minimum-saving bar, and would spend a generation of quality for nothing.
- **It cannot reach `modern` sources.** H.265/AV1/VP9 files are never transcoded (see
  below), so a 4K HEVC file stays 4K however the cap is set. A cap only reaches files
  the engine was already willing to re-encode: H.264 and legacy codecs.

A remux is a stream copy and cannot be rescaled at all, so a frame-size cap never
applies to one. Changing the cap is part of the ledger signature, so a library that
a previous run left alone is re-evaluated rather than read as already done.

## The re-encode gate: converge, don't re-cut

A source is re-encoded **only if it is more than 10% over its tier target**
(`TIER_OVER_TOLERANCE = 1.10`):

```
if source_kbps <= target_kbps × 1.10:  skip  ("already at/under <TIER> target")
else:                                  encode to the target
```

Because a first-pass encode lands at or under the target, the next run sees the file
as at-tier and leaves it alone — the process **converges after one encode** instead
of nibbling forever. Already-efficient sources (below target) are simply left alone;
there is no separate "bpp skip floor" any more — the tier target *is* the floor.

H.265/AV1/VP9 sources are classified `modern` and never transcoded (that would only
add a generation of loss); they are only remuxed losslessly into MP4 if asked.

### The exception: genuinely bloated modern files

That rule holds for almost everything, but not for a file a bad hardware encoder
wrote at a silly bitrate — common in drone and action-cam footage. `reencode_modern`
(CLI `--reencode-modern`) opens that door **narrowly**, and it is off by default:

- **A far stricter gate.** The same two tests as H.264 — over target, and able to
  clear the minimum saving — but at `modern_over_tolerance`, **2.0×** target rather
  than 1.10×. At 2× over, the win is ~50% and clearly worth the hours; at 1.2× you
  would spend a night to save a sliver and a generation of quality.
- **AV1 excluded by default** (`modern_codecs` defaults to `hevc, vp9`). AV1 is the
  most efficient of the three, so a bloated AV1 file is rare; AV1 → H.265 is usually
  an efficiency *downgrade*; and AV1 → AV1 in software is punishing with no
  VideoToolbox AV1 encoder on most machines. Add it deliberately or not at all.
- **A budget the user sets, spent worst-first.** `modern_max_files` caps how many run
  at once (0 = all), and `pick_modern_shortlist()` ranks candidates by predicted bytes
  **saved** — not by percentage, and not by file size — so a night's encoding goes on
  the fattest files.

  The engine does not pick that number: **it is answered, not defaulted.** Both front
  ends put the queue in front of the user first — `pipeline.modern_review()` reports
  how many qualify, what they weigh, what comes back, and how much work it is — and
  ask how many to do now. In the GUI that is a sheet after the scan; on the CLI it is
  `--dry-run`, which lists the worst offenders and names `--modern-max`.

  The time estimate is **measured, never assumed** — see below.

The budget is a **drip, not a ceiling**. A file that qualifies but falls outside it
gets its own outcome, `DEFER_MODERN`, which the ledger deliberately does **not**
record: it is deferred, not settled, so the next run comes back for it. Without that
distinction a per-run budget of 25 would have meant 25 files ever. The report says
"queued for a later run" rather than "left alone", for the same reason.

Enabling the option (or loosening its bar, or changing the codec list) is part of the
ledger signature, so a library that previous runs recorded as "modern, left alone" is
re-evaluated instead of appearing to ignore the setting.

The tool can never eat its own output this way: our own files are HEVC, but they
carry our tags and are caught by the second-generation guard before any of this runs.

## How long it will take

The app quotes a time in three places — the "about N hours" before you commit, the
countdown during a run, and the modern re-encode review — and they are all the same
arithmetic, `pipeline.encode_seconds()`. A review promising ten hours beside a clock
counting forty is a bug nobody finds until 3am.

**The unit is output pixel-frames per second**, not minutes of video
(`pipeline.encode_work` = width × height × fps × duration). An encoder is a
pixels-per-second machine, so:

- a 4K file costs roughly four times a 1080p one of the same length;
- a 60fps file costs twice a 30fps one;
- and a **frame-size cap makes the run genuinely faster** — capping a 4K library at
  1080p quarters the work, and an estimate priced on the source frame would quote
  four times the truth.

**The rate is measured on this machine.** Every run records what it actually achieved
(`_observed_rate`, excluding remuxes — a stream copy is near-instant and would inflate
the figure into a promise no encode could keep), blended a third at a time into a
stored value so one odd run cannot lurch the clock. Rates are kept per
hardware/software path, per output codec, and per `jobs` setting: the first two differ
by an order of magnitude, and the third matters because the rate is measured *per
stream* — four encodes at once contend for the same silicon, so a rate learned at
`jobs=1` would claim a fourfold speed-up that contention never delivers.

Measured accuracy, hardware VideoToolbox, synthetic content:

| Measured on | Predicting | Error |
|---|---|---|
| 1080p30 | 2160p30 (4× the pixels) | +8% |
| 1080p30 | 1080p60 (2× the frames) | +1% (the old fps-blind model: −49%) |

Before the first run there is nothing to measure, so two fallbacks stand in, in order:
a tier preview clip (a real encode at the real settings, but five seconds of it, mostly
start-up — rough, and labelled "a sample encode"), then constants measured once on an
M-series Mac (6.0× realtime hardware, 0.82× software, at 1080p30). At the reference
30fps those constants reproduce the older duration÷speed model exactly, so nothing was
silently re-rated when this changed.

Where no estimate is possible at all, the app says so rather than inventing a number.

### The countdown during a run

The pre-run quote is one number for the whole library. The clock during a run has a
harder job, because a library is *ordered*: it can spend an hour on instant skips (a
lean show, alphabetically early) and then walk into a block of real encodes. So the
countdown is never "seconds per file so far × files left" — that average is mix-blind,
and at 3,308 of 3,680 files it once promised four minutes for 372 files that were
mostly encodes.

Instead every file is predicted individually (above), and what remains is **predicted
work still to do**, calibrated against the real clock:

```
eta = heavy_left × corr  +  light_left × measured_cost_per_skip
```

Two pools, calibrated separately, because they are five thousand times apart — an
encode averages ~230s, every kind of skip 0.02–0.25s — and their errors are unrelated.
One shared correction would let thousands of skips drag the handful of encodes around.

- **Heavy** (encodes and remuxes): predicted seconds, multiplied by `corr` — how much a
  predicted second has really cost so far this run (`heavy_real / heavy_pred`). Clamped
  to 0.2–5.0 so one freak file cannot produce a fantasy, and held at 1.0 until enough
  encoding is behind us to mean anything.
- **Light** (skips and resumes): a *measured* flat cost per file. Predicting them
  individually is pointless; measuring them is trivial.

`corr` is also what absorbs parallelism: at `jobs=4` each file's wall time between
boundaries is roughly a quarter of its solo cost, so `corr` settles near 0.25 on its own.

It re-estimates at every file boundary — the honest moment, when something actually
finished — then every 30s for the first 5 minutes of a file, then leaves the clock to
count down. Re-emitting more often only made the number twitch and flashed
"re-estimating…" for no new information.

### What it cannot know: how hard the content is

One rate per machine is an average over whatever that machine last encoded, and
**content difficulty varies more than anything else in this model**. A grainy film
scan, confetti, water, foliage or a hand-held concert has far more entropy per frame
than flat cel animation or a static interview, and the encoder spends real time on the
difference — the same pixel count can differ by a factor of two or more, in either
direction, on the same machine and settings.

Consequences worth knowing:

- A library that is **half grain-heavy live action and half animation** will see the
  estimate wander, because a single blended rate sits between two populations rather
  than describing either.
- A run of unusually easy content will finish early and *raise* the stored rate, so the
  next estimate on hard content reads optimistic — and the reverse. The one-third blend
  is what stops this oscillating; it damps rather than chases.
- Within a run, `corr` above corrects for it directly, so the countdown converges even
  when the opening quote was wrong. **The pre-run number is the one that suffers**,
  because it has nothing to correct against yet.

This is deliberately not modelled. Guessing difficulty from a bitrate or a genre would
be a confident number with nothing behind it, and the honest fallback — measure, damp,
correct in flight, and say when we do not know — is already right most of the time.
If it ever needs improving, the principled fix is to key the rate on something
measurable about the source (its own bits per pixel is the obvious candidate: a lean
source is usually easy content, a fat one usually hard), not on a genre label.

## Encoders and what actually controls quality

- **Software (libx264/libx265)** — capped-CRF: `-crf 20/21 -maxrate <target> -bufsize`.
  CRF is a constant-*quality* target; the tier bitrate is a ceiling on peaks. This is
  the quality path.
- **Hardware (VideoToolbox)** — `-b:v <target>` only (no true CRF). Here the tier
  bitrate *is* the quality knob, which is why the bpp calibration matters most on this
  path.

`MIN_SAVING_RATIO` is a separate, post-encode guard: a shrink is only kept if the
output is actually ≥ N% smaller than the source.

## Resume ledger

Every file that reaches a terminal (non-error) decision is recorded in
`.vtc_processed.log` at the scan root, as:

```
<settings-signature>\t<abspath>\t<size>\t<mtime>
```

The signature is `TIER|CODEC|rmx?|xc?|outputmode`, with `|bppN` appended when the
tier has been retuned (only then, so history written before per-tier bpp existed
still matches). On a re-run:

- **Same settings** → recorded files are skipped without re-probing (fast resume,
  e.g. after `touch /tmp/hevc_stop` stops a run mid-way).
- **Changed settings** (different tier/codec/options) → signature differs, so
  everything is re-evaluated.

Correctness does not depend on the ledger — the absolute-target gate already prevents
re-cutting. The ledger is a resume/speed optimisation. Disable with `LEDGER=0`;
relocate with `LEDGER_FILE=...`.

## Knobs

Model constants live in `vtc/model.py` and per-run settings in `vtc/config.py`
(`RunConfig`); the CLI exposes them as flags.

| Constant / setting | Default | Meaning | CLI |
|---|---|---|---|
| `tier_bpp` | the tier's own anchor | per-tier density override (Advanced settings → Quality tiers) | `--bpp` |
| `TIER_OVER_TOLERANCE` | 1.10 | re-encode only if source is >10% over target | — |
| `BITRATE_FLOOR_KBPS` | 1500 | never target below this (kbps) | — |
| `HEVC_FACTOR_HD/4K/8K` | 0.60 / 0.50 / 0.45 | H.265 bitrate vs H.264 at same quality | — |
| ignore rules | none | size / extension / filename rules that remove files from the scan | `--ignore-under/-over/-ext/-name` |
| ledger enabled / file | on / `<scan>/.vtc_processed.log` | resume ledger toggle / path | `--no-ledger` / `--ledger-file` |
| encoder backend | auto | hardware (VideoToolbox) vs software | `--encoder {auto,hardware,software}` |
