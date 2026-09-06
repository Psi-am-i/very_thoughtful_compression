# Encoder comparison — measured, on real films

375 encodes: five sources chosen for variety, three clip lengths from the same
start, every encode path this machine has, every quality tier. The raw table is
`results.csv` beside the samples; the samples themselves are kept so any claim
here can be checked by looking at the file.

| source | frame | codec | bitrate | why it is in the set |
|---|---|---|---|---|
| Yu-Gi-Oh! S01E10 | 1280×720 | h264 | 2.8 Mbps | flat cel animation — the easiest thing to compress |
| Alien (1979) | 1920×800 | h264 | 2.5 Mbps | old, dark, heavy film grain |
| Annihilation (2018) | 1920×1080 | h264 | 3.6 Mbps | modern, colourful, moderate motion |
| Dune Part Two (2024) | 1920×1080 | h264 | 5.6 Mbps | modern, very dark, fine detail |
| Top Gun Maverick (2022) | 3840×2080 | **hevc** | 9.4 Mbps | large modern 4K source |

All measurements are **SSIM against that exact clip**, so "smaller" is never
reported without saying what it cost.

---

## 1. What a codec is worth, at matched quality

The useful question is not "how small can it go" but "how small at the same
quality". Taking software H.264 at EXCELLENT as the baseline, and finding the
cheapest encode of each other codec that matches its SSIM:

| source | H.264 baseline | H.265 | AV1 |
|---|---|---|---|
| Yu-Gi-Oh! | 1.5 Mbps | — | **79%** |
| Alien | 2.4 Mbps | — | **46%** |
| Annihilation | 2.4 Mbps | 49% | 60% |
| Dune Part Two | 2.9 Mbps | 45% | 50% |
| **Top Gun (4K)** | 30.1 Mbps | 38% | **16%** |

The gain is not a constant — it depends on the content far more than on the
codec's reputation. Flat animation barely rewards a modern codec (AV1 saves 21%);
a 4K live-action source rewards it enormously (AV1 matches H.264 at **a sixth**
of the bitrate). The dashes are cases where H.265 never reached the baseline
within the tier range, which is itself worth knowing.

![Top Gun rate-distortion](images/rd-topgun.svg)
![Alien rate-distortion](images/rd-alien.svg)

---

## 2. What it actually looks like

The same block of pixels, at 9× with nearest-neighbour scaling, so this is real
pixel structure rather than a resampled impression of it. The region is chosen by
measurement — the busiest, well-lit block in the frame — because a flat or dark
area is where every codec looks identical and proves nothing.

**Alien, OK tier** — the grain is the story. H.264 keeps most of it, H.265 smooths
it, AV1 smooths it further and spends the saved bits elsewhere:

![Alien crop](images/crop-alien-ok.png)

![Annihilation crop](images/crop-annih-ok.png)

Whether that smoothing is a loss or a gain is a matter of taste, and it is
precisely the judgement SSIM cannot make for you — which is why the samples are
kept rather than only the numbers.

---

## 3. Speed, and a model that turns out to be wrong

VTC's time estimates assume encoding cost scales with **output pixel-frames**, so
that a rate measured on one file predicts another. Measured across resolutions,
normalised to 1080p30:

| path | 1080p sources | Top Gun (4K) | |
|---|---|---|---|
| H.264 hardware | ~7.5× | **8.03×** | scales fine |
| H.265 hardware | ~7.3× | **8.04×** | scales fine |
| H.264 software | ~4.0× | **2.06×** | half the rate per pixel |
| H.265 software | ~2.2× | **0.78×** | **a third** |

**Hardware amortises larger frames well — 4K is its fastest case.** Software
collapses: `libx265` on 4K runs at a third of its per-pixel rate on 1080p, almost
certainly memory bandwidth rather than arithmetic.

Two consequences for the app:

- the pixel-frame work model **under-predicts software 4K encoding by 2–3×**, and
- a benchmark cannot sample cheap 1080p files and extrapolate to a 4K library.

There is also a reversal worth knowing: on 1080p, SVT-AV1 is the slowest path
here. On 4K it is **three times faster than x265** (80s against 255s for the same
clip) *and* produced a smaller file at higher SSIM. Codec speed rankings do not
survive a change of resolution.

---

## 4. How long a sample has to be

This is what the whole matrix was built to answer. Taking the 60-second clip as
the reference and asking how far the shorter ones are from it, across 118 cells:

| | median error | 90th percentile | worst |
|---|---|---|---|
| 10s vs 60s | **10.9%** | 27.5% | 38.5% |
| 30s vs 60s | **2.6%** | 12.8% | 26.8% |

A ten-second sample is not a measurement. It is worst on `libx265`
(median 21.6%, worst 35.2%) — the most analysis-heavy encoder needs the most
frames before its rate control settles — and short clips read systematically
**slow** (−4.9%), which is start-up cost being counted as throughput.

**So the benchmark's floor is 30 seconds, and 60 is better.** An earlier idea —
shortening 4K samples to keep the cost down — would have pushed exactly into the
unreliable zone, and was abandoned on this evidence.

---

## 5. Where SSIM stops helping

On Top Gun, SSIM sits near 0.934 and **stops responding to bitrate**: H.264
hardware scores 0.9344 at 18.5 Mbps and 0.9339 at 47.7 Mbps — slightly *worse* for
two and a half times the data. Frame counts were checked and align exactly, so
this is not a measurement artefact.

The explanation is the grain. It is high-entropy and effectively noise; no
bitrate reproduces it exactly, and SSIM penalises any difference without caring
whether the result looks better. So on grainy 4K, **SSIM saturates before quality
does**, and the tier ladder looks flat when it is not.

That is a limit of the metric, not of the encoders — and the reason this document
shows crops as well as numbers.
