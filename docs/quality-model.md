# Quality model — how tiers, targets, and re-encode decisions work

This documents the bitrate/quality logic now implemented in the `vtc` Python
engine (`vtc/model.py`, `vtc/pipeline.py`, `vtc/encode.py`) — the reference spec
that the retired `very_thoughtful_compression.sh` originally established.

## How this document states numbers

Comparisons are easy to write ambiguously — "70%" can mean seven tenths of the size,
seven tenths faster, or seven tenths slower, and a reader cannot tell which. So there
is one rule here, and it holds everywhere:

> **A ratio is always `subject ÷ baseline` for a NAMED quantity, written `×`.**

- `size ×0.16` — sixteen hundredths of the baseline's size.
- `speed ×3.2` — three point two times the baseline's speed.

Because the quantity is always named, `×` below 1 always means *less of that thing*
and above 1 always means *more of it*. The direction never flips, and words like
"faster" or "smaller" never appear as a bare multiplier. Whether more is good depends
on the quantity, which is why the quantity is stated.

**Percentages are only ever a share of an explicitly named whole** — "used 68% of the
bitrate it was given" — never a comparison between two things.

## Two different quality words: bpp and SSIM

The app has always spoken in **bpp** — bits per pixel per frame. It is worth being
exact about what that is, because a second measure now appears alongside it:

- **bpp is what is ASKED FOR.** A tier *is* a bpp; the target bitrate is that density
  multiplied out by the frame and the frame rate. It is a dial, set before encoding,
  and it says nothing on its own about how the result looks.
- **SSIM is what was GOT.** It compares the encoded frames against the source and
  scores the fidelity, after the fact. It cannot be set, only measured.

They are not interchangeable, and the interesting question — *what does a given bpp
actually buy?* — needs both. Measured, it buys much less than the ladder suggests:

| Alien, software AV1 | bpp | SSIM |
|---|---|---|
| OK | 0.030 | 0.9813 |
| EXCELLENT | 0.052 | 0.9819 |
| INSANE | 0.078 | 0.9822 |

bpp ×2.6 from OK to INSANE, for 0.0009 of SSIM. See
[encoder-comparison.md](encoder-comparison.md) for where that holds and where it does
not, and for the point at which SSIM stops responding to bitrate altogether.

**A tier does not mean the same thing on every path**, which matters when reading any
of this. Measured share of the bitrate each path actually used, against what the tier
gave it:

| path | used |
|---|---|
| H.265 hardware | 95–101% — ABR aims at the target, so the tier *is* the bitrate |
| H.265 software | 26–100% — CRF is often satisfied below the ceiling |
| AV1 software | 20–97% — capped-CRF, same two regimes as the H.26x software paths |

**This used to mean the software tier ladder was inert, and that was a bug, not the
design.** CRF was fixed by *mode* (SHRINK 20/21), so the tier reached libx264/libx265
only as the `-maxrate` ceiling. Once CRF's natural rate fell below the tier target the
ceiling never bound and every higher tier produced the same file — measured at 62% of
INSANE's promised density on H.264 software, against 95–102% at every tier on hardware,
which has no CRF. Alien landed at the same 1.4 Mbps whether asked for OK or INSANE.

Each tier now has its own **measured CRF band** (see *Encoders and what actually
controls quality*), so the tier reaches the software encoder twice: as quality via CRF,
and as a ceiling via `-maxrate`. Two regimes follow, and both are intended:

- **Dense or hard sources are ceiling-bound** — CRF's natural rate exceeds the target,
  `-maxrate` holds it, and the file lands at ~100% of the tier density.
- **Ordinary sources are CRF-bound** — the band is satisfied below the target, so the
  file lands *under* nominal density and is never padded to reach it.

## What a tier actually means

Calling a tier "up to this density, at this quality" is circular, because this model
defines quality *as* density. The definition needs two axes, and it has them — a tier
sets two numbers that do two different jobs:

- **A threshold** — the density above which a file is judged wasteful and worth
  re-encoding at all. This decides *whether* anything happens.
- **A quality** — the CRF band it is re-encoded *at*, if it crosses that threshold.

So **INSANE does not mean "re-encode everything to maximum quality"**. It means "only
touch files fatter than this generous budget, and when you do, encode them well". The
threshold is a property of the source; the quality is a property of the encode. Neither
is defined in terms of the other.

The observable consequence is counterintuitive and worth stating plainly: **a higher
tier touches FEWER files.** A 720p race at 3.64 Mbps is re-encoded at OK, and left
alone at EXCELLENT — a higher tier sets a more generous budget the source no longer
exceeds. Picking INSANE does less work than picking OK, not more.

Two regimes follow from the quality half, and both are intended:

- **Dense or hard sources are ceiling-bound** — CRF's natural rate exceeds the target,
  `-maxrate` holds it, and the file lands at ~100% of the tier density.
- **Ordinary sources are CRF-bound** — the band is satisfied below the target, so the
  file lands *under* nominal density and is never padded to reach it.

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

## AV1

AV1 is the third output codec, and the one with the sharpest trade-offs: the most
efficient of the three and the least widely playable. It is offered, never assumed —
H.265 remains the suggested default.

**Efficiency factors** sit 25% below the H.265 ones (`AV1_FACTOR_HD` 0.45 vs 0.60, and
so on), the conservative end of SVT-AV1's published 20–40% advantage over x265. That
number is only honest at a preset that earns it, so `AV1_PRESET` and those factors move
together — encoding faster would quietly under-deliver the quality the tier promises.
Preset 6 is the balance point.

⚠️ **AV1's speed ranking does not survive a change of resolution**, so any single
sentence about "how fast AV1 is" is wrong somewhere. On 1080p television SVT-AV1
preset 6 runs at 1.6× realtime against libx265's 2.6× — the slowest path the app
offers. On **4K it is the other way round**: measured on the same 60s clip, AV1 took
80s where libx265 took 255s (speed ×3.2 relative to libx265), produced a smaller file,
and scored slightly higher SSIM. Software H.265 degrades far worse with frame size
than AV1 does.

On a Mac there is no hardware AV1 either way, so AV1 always means software — several
times slower than the hardware H.265 most runs use today, whatever the resolution.

**AV1 runs with `--enable-variance-boost`**, which SVT-AV1 ships disabled along with
the rest of its *Psychovisual Options* section, while x264/x265 enable psy-rd by
default. It is an *additional* layer on top of the `aq-mode 2` SVT-AV1 already runs —
the config banner reads `AQ mode / Variance Boost : 2 / 0` — spending bits on flat and
shadow regions and saving where texture masks. It won a blind matched-bitrate panel
against AV1 as previously shipped, and psy-on AV1 then beat libx265 on the same panel,
the first time it has done so here. Left at documented defaults deliberately: a
hand-tuned variant won an earlier panel and is now unreproducible because its strengths
were never recorded.

**Rate control is capped-CRF, the same shape as the H.26x paths** — `-crf <band>
-maxrate <target>`, plus one argument the other two do not need.

This reverses an earlier conclusion. AV1 was on VBR (`-b:v`) because capped-CRF
"measured 1.24× over target" and so could never converge. The real story is two
encoder defaults:

- **`--mbr-overshoot-pct` defaults to 50.** SVT-AV1's capped-CRF ceiling is
  *specified* to leak by up to half, so `-maxrate` on its own is a suggestion rather
  than a cap. We now pass **`mbr-overshoot-pct=10`**.
- **`-bufsize` does nothing here.** ffmpeg maps it to SVT-AV1's `--buf-sz`, which the
  encoder documents as CBR-only. The tight VBV buffer that makes the x265 ceiling bind
  has no AV1 equivalent, so we no longer pass one — it only made the argument list
  look like it were doing something.

Measured over 8 real sources × 5 tiers, as ratio to target (gate is 1.10):

| Mode | Range | Breached the gate |
|---|---|---|
| `-crf` alone | 0.20 – 2.40 | 14 of 40 |
| capped-CRF, encoder default `mbr-overshoot-pct=50` | 0.20 – 1.32 | 7 of 40 |
| capped-CRF, `mbr-overshoot-pct=25` | — | 0 of 12 retested, worst **1.091** |
| capped-CRF, **`mbr-overshoot-pct=10`** | 0.20 – 0.97 | **0 of 40 ✓** |
| the above, re-verified with variance boost on | 0.44 – 1.01 | **0 of 40 ✓** |
| VBR `-b:v` | 0.69 – 0.86 | 0 of 40 — but never near target |

**VBR converged only by never arriving.** It delivered 69–86% of target on every
source at every tier, so an AV1 file landed about a quarter below the density its tier
promised and no tier setting could lift it. Capped-CRF restores the two regimes the
other software paths have: hard sources ceiling-bound near target, ordinary ones
CRF-bound well below it (0.20× on Obi-Wan), which is the intended "never pad a file
that does not need it".

⚠️ **10 is a margin, not a target.** 25 lands hard content nearer 100% of tier density,
but leaves 0.9% of headroom on the hardest source measured. That is the same trap as
setting the x265 ceiling at target × 1.10: one source slightly harder than anything
tested tips over the gate and re-encodes on every run forever.

⚠️ **SVT-AV1 overshoots on very short clips**, and it is a fixed start-up cost that
amortises: measured at a 2100 kbps target, 6s lands 1.19× over, 15s 1.06×, 30s 1.04×,
60s 1.01×. Real files are minutes long so this never bites a run — but the tier
**previews are five-second samples**, so an AV1 preview will look larger than the codec
really is. Do not read the preview panel as a fair size comparison for AV1.

**Hardware AV1 encoding is PC-only.** `_HW_CANDIDATES[AV1]` lists `av1_nvenc`
(NVIDIA RTX 40-series), `av1_qsv` (Intel Arc) and `av1_amf` (AMD RDNA3). There is
deliberately no `av1_videotoolbox`: Apple silicon *decodes* AV1 from the M3 but nothing
Apple makes encodes it, so listing one would make every Mac pay for a probe that can
only fail. Macs take the software path. None of the three could be tested on the
machine this was written on — which is what the functional one-frame probe in
`_encoder_works` is for: an absent or broken encoder fails it and the run falls through
to software rather than failing every file.

**Playability is the real cost.** AV1 decode needs an M3+ Mac, an RTX 30-series or
newer, Intel 11th-gen or newer, RDNA2 or newer, or a 2023-and-later TV. Anything older
either transcodes on the server or will not play at all — which for a Plex library is
the opposite of the tool's usual promise that nothing downstream notices.

### Benchmarking the user's own machine

Everything above is a constant measured on one developer's Mac until the user's own
machine has been measured, which is what `vtc/bench.py` is for — `--benchmark` on the
CLI, offered on first run and repeatable from Settings in the app.

It points at the library, draws files **at random** (a library is alphabetical, so the
first N files are one show, shot one way), takes a stream-copied slice from the middle
of each (titles and credits are not the content), and encodes it down **every path this
machine actually has** — probed, so a listed-but-broken encoder never reaches an
estimate. Two samples by default, because one can be a static interview or a confetti
cannon and those differ by more than the thing being measured.

It reports both halves of the choice, which point opposite ways:

- **speed@1080p** — × realtime normalised to a 1080p30 frame, so the figure describes
  the machine rather than whichever files were drawn.
- **of target** — how much of the tier's bitrate allowance each path actually spent,
  with SSIM beside it. Without the SSIM "smaller" is not a result: any codec can be
  smaller by being worse.

A representative run on real television:

| codec | path | speed@1080p | of target | SSIM |
|---|---|---|---|---|
| H.264 | hardware | 5.1× | 103% | 0.9902 |
| H.264 | software | 3.6× | **57%** | 0.9923 |
| H.265 | hardware | 5.6× | 98% | 0.9896 |
| H.265 | software | 1.5× | **58%** | 0.9908 |
| AV1 | software | 1.1× | 83% | 0.9917 |

Hardware spends its whole ABR allowance; software's capped CRF is satisfied at little
over half of it, at slightly *better* SSIM. Hardware is several times faster, software
usually produces a much smaller file at the same quality — neither is simply better,
which is why the app measures rather than recommends.

The rates it learns go into the same store everything else reads, ranked: a real run of
the library beats a benchmark of it, which beats a five-second preview clip, which
beats the shipped constants. Each is labelled, so the app can say where its number came
from. Benchmarks run sequentially and file under `jobs=1`; a run at higher concurrency
falls back to that measurement rather than to the constants, and the in-run correction
closes the rest.

### Measured encoder speeds

Real television, five-minute samples, the app's own arguments, × realtime:

| | H.264 hw | H.264 sw | H.265 hw | H.265 sw | AV1 sw |
|---|---|---|---|---|---|
| The Crown 1080p24 | 9.53 | 3.69 | 9.22 | 2.55 | 1.70 |
| Stath Lets Flats 1080p25 | 9.08 | 2.52 | 8.96 | — | 1.44 |
| Clarkson's Farm 720p25 | 17.73 | 10.60 | 14.83 | 3.53 | — |

**These are 1080p/720p television.** They do not carry to 4K — see the resolution
table above, and [encoder-comparison.md](encoder-comparison.md) for the full 375-encode
matrix across five kinds of content.

**Do not re-derive these from `lavfi`.** Synthetic sources gave 7.27 against 6.32 for
H.264 — a gap a third the real size — and made SVT-AV1 look faster than libx265 at a
resolution where it is in fact slower.

Quality per bit runs the other way, and it is worth knowing before choosing hardware
for speed. At the same tier, software's capped CRF is frequently satisfied far below
the tier target, while the hardware ABR path spends the whole allowance:

| | bitrate | SSIM |
|---|---|---|
| The Crown, H.265 hardware | 5019 kbps | 0.99503 |
| The Crown, H.265 software | **1088 kbps** | 0.99232 |

Near-identical SSIM at roughly a fifth of the size. Hardware is about three times
faster; software often produces a far smaller file at the same tier. Neither is simply
"better", and the app offers both for that reason.

## How long it will take

The app quotes a time in three places — the "about N hours" before you commit, the
countdown during a run, and the modern re-encode review — and they are all the same
arithmetic, `pipeline.encode_seconds()`. A review promising ten hours beside a clock
counting forty is a bug nobody finds until 3am.

**The unit is output pixel-frames per second**, not minutes of video
(`pipeline.encode_work` = width × height × fps × duration). An encoder is a
pixels-per-second machine, so:

- a 60fps file costs twice a 30fps one;
- a 4K file costs *at least* four times a 1080p one of the same length — four times
  the pixels, and for software encoders **considerably worse than that** (below);
- and a **frame-size cap makes the run genuinely faster** — capping a 4K library at
  1080p removes at least three quarters of the work, and an estimate priced on the
  source frame would quote several times the truth.

⚠️ **The unit is not resolution-independent for software encoders.** Measured over
375 encodes ([encoder comparison](encoder-comparison.md)), as a ratio of the 4K rate
to the same encoder's 1080p rate — so `×1.00` would mean "scales perfectly":

| path | rate at 4K, relative to its own 1080p rate |
|---|---|
| H.264 hardware | ×1.07 |
| H.265 hardware | ×1.10 |
| H.264 software | ×0.52 |
| H.265 software | **×0.35** |

Hardware amortises larger frames slightly *better*; software collapses, almost
certainly memory bandwidth rather than arithmetic. So `encode_seconds()`
under-predicts a software 4K run — by up to about three times — and a benchmark
cannot sample cheap 1080p files and extrapolate to a 4K library.

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

- **Software (libx264/libx265/libsvtav1)** — capped-CRF: `-crf <band> -maxrate <target>`.
  CRF is a constant-*quality* target; the tier bitrate is a ceiling on peaks. This is
  the quality path. The band is **per tier and measured**, not a constant:

  | tier | libx264 | libx265 | libsvtav1 |
  |---|---|---|---|
  | OK | 21 | 24 | 32 |
  | GOOD | 18 | 21 | 28 |
  | EXCELLENT | 16 | 19 | 24 |
  | STELLAR | 15 | 18 | 21 |
  | INSANE | 14 | 16 | 19 |

  ⛔ **The AV1 column and `AV1_PSY_PARAMS` are one measurement.** A psychovisual
  option moves the CRF/rate relationship bodily, not just the picture:
  `--enable-variance-boost` alone roughly *doubles* the bitrate at the same CRF, which
  is why enabling it moved this column by 6–7 CRF on every rung and steepened AV1's
  slope from 8.58 to 10.04. Change the string without re-fitting the column and every
  tier gets about twice the bitrate it asked for.

  TRANSCODE sits below the same tier's band, keeping the fidelity offset the old fixed
  pair had — **two CRF on the H.26x paths, three on AV1**, because a CRF step is not
  worth the same on all three (see the slopes below: two CRF buys x265 ~30% more bits
  and AV1 only ~17%).

  The bands come from fitting `crf = a + b·log2(bpp)` per source over a 9-point CRF
  grid on 30s real-footage clips (8 sources, 126 encodes for H.26x; a separate 8
  sources and 72 encodes for AV1): the *shape* is the slope and the *position* is the
  median of the sources where the anchor rung is live. Fitting each rung independently
  does **not** work: the live source set differs per rung, so the rungs are not
  comparable and the ladder comes out non-monotonic — it did so again on the AV1 pass,
  putting INSANE at 14.2 *above* STELLAR's 15.0 on the four dense sources that are the
  only ones live that high. Validated for H.26x by interpolating each source's own
  measured CRF→SSIM curve at these values — SSIM climbs at every rung on every source
  that has signal.

  ⚠️ **The slopes are not interchangeable between codecs, and not even between
  configurations of one codec.** Median CRF per doubling of bitrate: **5.11 on x264,
  5.21 on x265, 10.04 on SVT-AV1 with variance boost** (8.58 without it; R² 0.99+
  across the eight AV1 sources either way). Any correction that assumes one constant —
  a rate-matcher, a preview estimate, a "just drop 6 CRF" rule of thumb — is wrong for
  AV1 by nearly a factor of two. Per source it ranges 6.06 to 11.58, so even 10.04 is a
  median rather than a constant: **bisect to match a bitrate, do not single-step.**
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
