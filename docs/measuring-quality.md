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

### Record the exact parameter string, not the label

A configuration called *shadowTools* won a blind panel and is now **unreproducible**.
It beat AV1-as-shipped on 4.5% fewer bits; six hours later the script that built it had
been cleared from `/tmp`, and the file itself carries only `Lavc62.28.102 libsvtav1` in
its metadata. We know which two flags it used and not their strengths — and
`--variance-boost-strength` is 1–4, `--variance-boost-curve` 0–2, `--luminance-qp-bias`
0–100, so "the two flags" names some hundreds of configurations.

**Write the full encoder argument list into the KEY file beside the bitrates.** A panel
is a measurement, and a measurement whose settings are not recorded is an anecdote. The
recovery here was to drop the un-pinned flag entirely and re-fit against variance boost
at its documented defaults, which is reproducible — at the cost of throwing away a
result that had already won.

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
SVT-AV1 VBR (-b:v)     69-86% of target, on every source at every tier
SVT-AV1 capped CRF     20-132% of target at the encoder's default ceiling
SVT-AV1 capped CRF     20- 97% with mbr-overshoot-pct=10  <- shipping
libx264/5 1-pass ABR   93-141% of target, breached the 1.10 gate on 10 of 16 cases
libx264/5 2-pass ABR   99-103% of target, breached on 0 of 16
```

Single-pass ABR is eliminated on that evidence alone: landing at 118% of target means
a file still reads as over-target next run and is re-encoded forever. 2-pass earns its
1.3×/1.8× cost by **converging**, not by looking better — its quality advantage
measured +0.00021 SSIM, which post-§1 we should treat as unmeasured rather than zero.

### A rate-control number can be true and still be the wrong conclusion

AV1 sat on VBR for a whole release because capped CRF "measured 1.24× over target".
The measurement was fine; what was missing was that **SVT-AV1's ceiling has a
documented leak** — `--mbr-overshoot-pct` defaults to 50, so `-maxrate` permits half
again as much. Setting it to 10 moved the worst of 40 real cases from 1.32× to 0.97×.
Two lessons, and the second is the expensive one:

- **Read the encoder's own `--help` before concluding the encoder cannot do
  something.** Every fact needed here was one command away, and `-bufsize` — which we
  had been passing all along — turns out to map to a CBR-only parameter and to have
  been doing nothing at all.
- **"It does not converge" is a claim about our arguments, not about the codec.** The
  same sentence was true of libx264 before its VBV buffer was tightened. When a path
  cannot hit a target, suspect the configuration before the format.

And one number that simply did not reproduce: the recorded `-crf 32 alone → 6644 kbps,
2.77× over` came back as **486 kbps** on real 1080p footage — 13× out. It had been
carried in a code comment and a doc table as settled fact. Re-measure a number before
building on it, especially one that is the sole evidence for a decision.

---

### Aggregating packet sizes: pick a window wider than the encoder's mini-GOP

Packet sizes come free off the container index, with no decode, which makes
per-second bitrate a tempting way to ask whether an encoder *starves* a hard passage
rather than merely hitting its average. It is a good question — convergence is a claim
about the mean, and a viewer reports the worst passage — but the window has to be
chosen against the encoder, not against the clock.

**SVT-AV1's mini-GOP is 32 frames; x265's is 8.** At 24fps a one-second bucket is 24
frames: it straddles AV1's hierarchy and cannot straddle H.265's. Measured on eight
sources at GOOD, AV1 capped-CRF against libx265, as median coefficient of variation
and median worst-window-over-mean:

| bucket | AV1 CV | H.265 CV | AV1 floor | H.265 floor | AV1 steadier |
|---|---|---|---|---|---|
| 1s | 42.9% | 30.5% | 0.24 | 0.45 | 1 of 8 |
| 2s | 33.2% | 27.8% | 0.44 | 0.51 | 1 of 8 |
| 4s | 26.0% | 21.0% | 0.66 | 0.69 | **5 of 8** |

At one second AV1 looks dramatically less consistent. At four — three times its
mini-GOP — the two are comparable and AV1 is the steadier of the pair on most sources.
**The gap was mostly the hierarchy period, not the bitrate allocation.**

**Locate the period, do not guess it.** Autocorrelating the raw frame-size series is
free and finds it directly: AV1 peaks at **lag 32 (+0.75)** with harmonics at 16 and 48,
while x265 peaks at lag 4–12 around **+0.26**. Measured independently by two sessions on
the same files, to within 0.01.

⚠️ **A window rule fixes the period and throws away the amplitude, which is the part
that might actually be visible.** AV1's cycle is about **three times stronger** than
x265's, and a 1.33-second cycle at +0.75 is a real quality pulse, not a bookkeeping
artefact. A viewer described one unprompted — *"some frames are really much better than
others… frame by frame becomes a moving target of better"* — before seeing any of these
numbers. Autocorrelation amplitude is a better candidate for that observation than
per-second CV was; it is still not proof of visibility.

**And variance is not a quality proxy in either direction.** On one blind panel, within
AV1 (same mini-GOP both sides, so no window artefact), the encode with the *higher* CV
and the *lower* floor ranked *above* the steadier one. `--enable-variance-boost` works
**by** increasing variance — spending on flat and shadow regions, saving where texture
masks. Variance diagnoses a mechanism; it does not rank quality.

Two things this does *not* say. It does not say a viewer was wrong: a 32-frame mini-GOP
is a real ~1.3s quality cycle and may well be visible, which a bitrate statistic cannot
settle either way. And it does not credit capped-CRF with fixing consistency — at a 1s
bucket, moving AV1 from VBR to capped-CRF changed median CV only 46.5% → 42.9% and made
the normalised floor slightly *worse* (0.29 → 0.24). Capped-CRF was adopted for
convergence and density, which are separate and demonstrated; consistency remains open
and is a viewing question.

### "Both codecs at their best" is not a well-defined comparison

Once you start enabling non-default options, the obvious next question is whether the
*other* encoder has been given the same courtesy. It is a fair question and it has no
stopping point, because the two encoders' knobs do not map onto each other.

What is actually known, from each encoder's own config banner rather than from memory:

| | SVT-AV1 4.1.0 default | x265 4.2 default (preset medium) |
|---|---|---|
| adaptive quantisation | `aq-mode 2` **on** | `aq-mode 2` **on** |
| psychovisual RD | `tune=0` VQ (we set it; default is PSNR) | `psy-rd 2.00` **on** |
| extra AQ layer | `enable-variance-boost` **off** | — no equivalent |
| psy in quantisation | — | `psy-rdoq` off at medium, **on at slow** |

So enabling variance boost does **not** bring AV1 level with x265's `aq-mode 2` — AV1
already had that. It adds a layer x265 has no counterpart for. Equally, x265's
`--tune grain` bundle (psy-rd doubled to 4.00, `sao` off, AQ and cu-tree disabled,
`rskip` off) has no AV1 counterpart either. Neither encoder is "further tuned" than the
other in any orderable sense.

**The comparison this tool needs is "each codec as we would ship it"** — that one
terminates, and it is the one that decides anything.

**But it is not the only question, and on its own it would have missed every AV1 fix
in this document.** As-we-would-ship-it *was* `tune=PSNR`, 8-bit, VBR and psy-off. Run
that fairly against x265 and it returns "AV1 loses at 1080p" — true of our build, false
of the codec, and we would have concluded AV1 was weak rather than that we had
configured it badly. Four real defects came out of a different question: **is this
configuration defensible on its own terms?**

So there are two activities and both are needed:

- **Audit** — read the encoder's `--help` and its config banner and ask whether each
  default is one we would choose. Generates candidates. Costs nothing but reading.
- **Panel** — adjudicate a candidate blind at matched bitrate. Costs the one scarce
  resource, a viewer's attention and freshness.

Audit widely; panel narrowly. And prefer an **intra-codec** panel where the question
allows it (this setting on versus off, same encoder both sides): it terminates, it is
immune to the knob-mapping problem above, and it is the test that earned variance boost
its place.

### Pre-register the rule before the panel runs

Decide what result would change the code *before* seeing the result, and write it down:

> Ship X if it wins on the viewer's eye at matched bitrate on 2+ clips **and** the
> re-fitted band clears the 1.10 convergence gate. Otherwise drop it and record the
> negative.

Deciding shippability first and testing second gets the dependency backwards; deciding
after seeing the result invites the number to pick its own interpretation. The bar is
credible here only because something has actually failed it — `--enable-tf=0` was
tested on that rule and lost, so the rule is not decoration.

⚠️ **A preset can switch a flag on for you.** `psy-rdoq` is inert at preset *medium*
because RDOQ itself is off there, and switches on by itself at *slow*. VTC uses medium
for SHRINK and slow for TRANSCODE — so the common path is missing a psychovisual stage
the rarer path has, which nobody chose. It also means setting `psy-rdoq` alone at medium
would do nothing without raising `rdoq-level` first: a flag that looks set and is not.

That is an unchosen asymmetry, **not a bug with a free fix**: RDOQ costs encode time,
which is why the presets differ in the first place. Enabling it at medium is a trade to
be measured like any other, not an oversight to be corrected.

## 6b. One thing we will not do

**AV1 film-grain synthesis stays off.** It would likely score well on any
grain-retention comparison, and it is nearly free. But it does not preserve grain —
the decoder *synthesises* a noise pattern that was never in the source. Retaining
detail and fabricating it are different acts, and only one of them is compatible
with "never make a file worse".

Worth stating because it is exactly the kind of flag that looks like a win in a
side-by-side and is indefensible once you ask what it actually did.

### Deferred-untested is not declined

x265's `--tune grain` has been audited and **not tested**: no evidence either way. That
is a different state from `--enable-tf=0`, which was tested against the pre-registered
bar, ranked below the default, and cost 7.8% more bits — a defensible negative.

Keep the two apart in writing. Recording an untested option as "declined" reads as
considered without being it, and it is the sentence that stops anyone testing it later.

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
- **The AV1 factors cannot express the claim anyone would want to test.** They are
  H.265's scaled by a constant — 0.45/0.60 = 0.750, 0.38/0.50 = 0.760, 0.34/0.45 =
  0.756 — so the model asserts AV1 needs ~25% fewer bits than H.265 at *every*
  resolution and says nothing about resolution dependence, which is the hypothesis.
  The fix is structural, not a better number: a replacement has to be free to bend.
  And it is **two** unmeasured hops (H.264→H.265 and H.265→AV1), so measuring the AV1
  ratio alone calibrates against an anchor that is itself in doubt — see the previous
  bullet. Honest scope is two staircases per resolution band.
- **Every AV1-vs-H.265 panel so far contradicts `AV1_FACTOR_HD = 0.45`.** At the same
  tier the model hands H.265 **33% more bits** than AV1 (0.60 against 0.45), on the
  premise that AV1 needs fewer to match it. In the panels H.265 has won while using
  *fewer actual bits* — 1450 kbps against AV1-psy's 1496, so 3% fewer, not a third
  more. The factors are still not being touched, because every one of those panels
  predates a fix to the AV1 path; but this is the specific observation that would
  change them, and the test is a matched-bitrate blind comparison at 1080p.
- **Every AV1 conclusion predates the `tune` discovery** and must be re-tested with
  `tune=0` before being acted on.
- **The AV1 efficiency factors have still not been re-measured.** `AV1_FACTOR_HD =
  0.45` and its siblings were set when AV1 was misconfigured three ways — `tune=PSNR`,
  8-bit, and VBR under-feeding it to ~78% of the budget it was given. All three are
  now fixed, so AV1 is being handed the smallest budget of the three codecs *and*
  finally spending it. Whether 0.45 is still right is an open question, and it is now
  answerable: a matched-bitrate blind comparison against H.265 at 1080p.
