# Calibration harnesses

Every measured number in `docs/quality-model.md` and `docs/measuring-quality.md` came
out of one of these. They are here because a winning configuration was once lost for
want of exactly this: `shadowTools` beat AV1-as-shipped in a blind panel, lived only in
a script under `/tmp`, and was unreproducible six hours later
(see measuring-quality.md §5).

They are **not** part of the shipped app. They are not packaged, not imported by `vtc/`,
and not covered by the suite. They read `vtc.model` and `vtc.encode` so the settings
they measure are the settings that ship.

## What produced what

| script | produced |
|---|---|
| `mkclips.py` | the 8-clip 1080p set — 30s at 40% depth, `-c copy`, chapters/audio/subs stripped |
| `grid.py` | the 9-point CRF grid; `-svtav1-params` is the line to edit for a new configuration |
| `analyze.py` | the fit `crf = a + b·log2(bpp)`, the median slope, and the ladder |
| `final.py` | convergence verification against the 1.10 gate |
| `verify.py` | the three-way rate-control comparison (plain CRF / capped / VBR) |
| `tighten.py` | the `mbr-overshoot-pct` sweep that found SVT-AV1's 50% default leak |
| `consist.py` | per-window bitrate consistency off the container index, no decode |
| `consist2.py` | the same at 1s/2s/4s — how the mini-GOP artefact was found |
| `acf.py` | autocorrelation of frame sizes; locates the hierarchy period directly |
| `gp2.py` | float-CRF bisection to match two encodes to the same bitrate |
| `grainmontage.py` | 2-up split screen, 1:1 crops, lossless, randomised, key withheld |
| `panel.py`, `panel_psy.py` | 4-up and staircase panel builders (from the second session) |

## Two rules these encode

**Refuse rather than mislead.** `gp2.py` will not build a panel it could not match to
within 5%. An earlier matcher handed over a 143 kbps encode to be judged as if it were
1450; an extreme failure is obvious, a mild one produces a confident wrong conclusion.

**A tier only fires above the source's own density.** Before choosing clips for a panel,
check the source is genuinely over the threshold for the tier being tested — otherwise
the target clamps to the source and the encode inflates rather than shrinks, testing a
regime production never reaches. Measured H.265 thresholds against the 1080p set:

```
OK 0.0637  GOOD 0.0849  EXCELLENT 0.1114  STELLAR 0.1379  INSANE 0.1644   (bpp, incl. the 1.10 gate)
```

so of that set only The Rehearsal (0.2055) and Red Dwarf (0.2546) are live at INSANE.

## Paths

They carry absolute paths to a scratchpad and to `/Volumes/Scout-3MacBackup/VTC-compare`.
Fix the constants at the top before re-running; they are recorded as they ran.
