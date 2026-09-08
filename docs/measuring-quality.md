# Measuring quality — what fooled us, and what didn't

Everything in this document was learned the hard way during one investigation
(2026-09-06/07) that started as "why do all the quality tiers look identical?" and
ended somewhere much more interesting. Each section is a trap we fell into, with the
evidence that got us out.

**The one-line summary: bitrate questions are settled by measurement, fidelity
questions by looking.** A metric is a scalable proxy for triage, never an arbiter.

---

## 1. A flat metric means the metric is blind, not that the encodes are equal

We ran 522 encodes and most SSIM curves came out flat. The obvious reading — "these
tiers are indistinguishable" — was wrong, and we wrote it into the docs before
testing it.

The test that disproved it: a **2×2 montage**, each quadrant a 960×540 crop at 1:1
from a different version of the same clip, tiled without scaling, encoded losslessly,
**placement randomised and the key withheld until after viewing**.

| | SSIM span | VMAF span | ranked by eye |
|---|---|---|---|
| Downfall (responsive) | 0.00869 | 3.69 | 4/4 correct |
| HailMary ("mute") | 0.00104 | 1.85 | 4/4 correct |

HailMary's H.265 ladder runs **887 → 6,230 kbps**, a 7× range, and SSIM moves 0.001
across it. A viewer separated all four immediately, including picking the source out
from a 6,230 kbps encode of it. Across seven trials the source was identified
correctly every time.

**SSIM can fail to distinguish; it cannot certify equivalence.** Any conclusion of the
form "these are the same" resting on a small SSIM delta is unsupported.

VMAF is better and still understates: it scored HailMary's ladder at 1.85 points,
below the ~6-point rule of thumb for a just-noticeable difference, on a ladder a
viewer ranked perfectly. That rule is calibrated for *sequential* viewing; side-by-side
at 1:1 is far more sensitive.

### Two ways a metric goes mute, for opposite reasons

| mode | example | why |
|---|---|---|
| saturated **high** | HailMary, SSIM 0.978 | trivially compressible — H.265 hits 0.9767 at 878 kbps for 1080p |
| saturated **low** | SoftSkin, SSIM 0.878 | noise-like texture no bitrate reproduces |

Downfall and HailMary are the same resolution at comparable density (0.2229 vs 0.1665
bpp) and their AV1 spans differ **19×**. It is the material, not the format.

---

## 2. The encoder may be optimising for the metric that judges it

The strangest result of the investigation: **SSIM ranked AV1 *above* H.265 on the
exact files a viewer called "worst by far".** Not blindness — collusion.

```
SVT-AV1 Encoder Lib v4.1.0
config: preset / tune / pred struct : 8 / PSNR / random access
                                          ^^^^
```

**SVT-AV1 defaults to `tune=PSNR`.** `tune=0` optimises for subjective quality
instead. We never set it, so every AV1 encode in the investigation was optimised for
exactly the thing that was scoring it, at the expense of the thing being looked at.

Worse, the comparison was never on equal terms: **x264 and x265 enable psychovisual
optimisation by default (`--psy-rd`); SVT-AV1 does not.** So a like-for-like codec
comparison using default settings is not like-for-like at all, and no amount of SSIM
or VMAF would ever have revealed it.

**Before comparing encoders, check what each one is optimising for.** A default that
targets a metric will beat that metric while losing to the eye.

---

## 3. Above the source's own density, extra bits buy nothing

TopGun's SSIM sat at 0.934 and stopped responding to bitrate — 0.9344 at 18.5 Mbps,
0.9339 at 47.7 Mbps. We blamed grain. Wrong.

TopGun is 14.2 Mbps across 3840×2080 — **0.0742 bpp**. INSANE asks for 0.2492 bpp,
**3.4× the density the source ever had**. There is no fidelity above the source to
recover, so bits past that point re-describe the source's existing compression
artefacts. SSIM correctly reported no gain.

The proof is a source with density to spare: TimeLapse at 0.2427 bpp has its whole
ladder *below* source density, and there SSIM climbs monotonically on every path
(H.264 hw +0.01336, H.265 hw +0.01259, AV1 +0.00685).

**A tier only applies to sources dense enough to reach it.** EXCELLENT wants 0.1688
bpp ≈ 17 Mbps at 1080p24 or 52 Mbps at 4K30, so upper-tier behaviour can only be
calibrated on genuinely dense sources. Production clamps every target to the source
bitrate, so lean files are skipped entirely — the regime never occurs in a real run.

---

## 4. Controls that are worth the minutes they cost

Run these before believing any fidelity measurement:

| control | expected | what it rules out |
|---|---|---|
| reference vs itself | exactly 1.000000 | framesync, alignment |
| lossless `x264 -qp 0` vs reference | exactly 1.000000 | the whole compare pipeline |
| deliberate ±1, ±2 frame shifts | symmetric falloff, peak at 0 | temporal misalignment |
| plain CRF encode vs your own args | should match closely | your argument construction |

All four passed on the file we had accused of a measurement fault, which is how we
knew to look at the source density instead.

**Never compare a windowed encode made with fast-seek (`-ss` before `-i`) against a
full-clip reference.** That produced a bogus CRF sweep (0.966–1.000) which contradicted
the full-clip result and cost a wrong "the measurement is broken" call. Full-clip,
no-seek is the only trustworthy form.

---

## 5. Building an honest comparison

- **Crop at 1:1, never scale.** Any resample hides exactly the artefacts you are
  judging. A 960×540 crop of a 4K frame shows 1/16 of the area — harsher and more
  revealing than any fit-to-screen view.
- **Encode the montage losslessly** (`-qp 0`). At any normal quality you layer this
  encoder's artefacts over the ones under test.
- **Randomise placement and withhold the key.** Knowing which panel is the source is
  exactly what makes people see differences that aren't there.
- **Split-screen (2-up, 960×1080) beats quartered for 4K** — four times the area per
  panel and a proper A/B.
- **Match the bitrate, and verify that you did.** Several of our preset comparisons
  carried a 6–7% bitrate spread, the same order as the effect being measured.
- **Strip chapters.** `-an` drops audio but MKV chapters become a `bin_data` stream in
  MP4: a 69-second clip carrying 22 chapters reported a 95-minute duration and played
  as an hour of black. Use `-map 0:v:0 -map_chapters -1 -dn`.

### An accidental control worth repeating deliberately

A script bug once put the **same file in two quadrants**. They were ranked 3rd and
4th — different. That calibrates the noise floor for free: at that level of
similarity, ordering is not meaningful. **Put a duplicate in one quadrant on purpose
occasionally**; if the viewer separates two identical clips, the rest of that trial's
fine distinctions are noise.

---

## 6. Rate control is a bitrate question, so measure it

These stand regardless of any fidelity metric, because they are counts of bits:

```
SVT-AV1 VBR (-b:v)     64-84% of target, worse at slower presets
SVT-AV1 CRF mode       97-99% of target
libx264/5 1-pass ABR   93-141% of target, breached the 1.10 gate on 10 of 16 cases
libx264/5 2-pass ABR   99-103% of target, breached on 0 of 16
```

Single-pass ABR is eliminated on that evidence alone: landing at 118% of target means
a file still reads as over-target next run and is re-encoded forever. 2-pass earns its
1.3×/1.8× cost by **converging**, not by looking better — its quality advantage
measured +0.00021 SSIM, which post-§1 we should treat as unmeasured rather than zero.

---

## 6b. One thing we will not do

**AV1 film-grain synthesis stays off.** It would likely score well on any
grain-retention comparison, and it is nearly free. But it does not preserve grain —
the decoder *synthesises* a noise pattern that was never in the source. Retaining
detail and fabricating it are different acts, and only one of them is compatible
with "never make a file worse".

Worth stating because it is exactly the kind of flag that looks like a win in a
side-by-side and is indefensible once you ask what it actually did.

## 7. What a tier means

Defining a tier *as* a bits-per-pixel density makes every description circular. A tier
sets **two numbers doing two different jobs**:

- **A threshold** — the density above which a file is judged wasteful and worth
  re-encoding at all. Decides *whether* anything happens.
- **A quality** — the CRF band it is re-encoded *at*, if it crosses that threshold.

So INSANE does not mean "re-encode everything to maximum quality". It means "only touch
files fatter than this generous budget, and encode those well". Neither number is
defined in terms of the other.

The observable consequence is counterintuitive: **a higher tier touches fewer files.**
A 720p race at 3.64 Mbps is SHRINK at OK and REMUX at EXCELLENT — verified through
`pipeline.decide()`.

---

## 8. Open, at the time of writing

- **`HEVC_FACTOR_HD = 0.60` may be too aggressive.** On Downfall at EXCELLENT, H.265
  received exactly its prescribed 60% of H.264's bitrate (5,874 vs 9,715) and was
  ranked *below* H.264 by eye. If that holds, the model's central premise — hand the
  efficient codec fewer bits — is overstated at 1080p. Needs H.264 vs H.265 at matched
  bitrate.
- **Every AV1 conclusion predates the `tune` discovery** and must be re-tested with
  `tune=0` before being acted on.
