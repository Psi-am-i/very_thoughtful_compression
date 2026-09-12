# Future versions — ideas accepted but not built

Things Simon has said yes to that are not in the current version. Each entry says what
it is, why it is wanted, and what it would take — enough that a later session can pick it
up without re-deriving the reasoning. An idea that has been *rejected* does not belong
here; those live with the decision that rejected them, in the doc that covers the area.

---

## Per-file temporal novelty, to price the `fps` term per file

**Accepted 2026-09-12.** The `fps` term is currently one conservative constant
(`FPS_PRICE_EXPONENT` in `vtc/model.py` — see quality-model.md, "The `fps` term"). That
constant exists because a single number cannot express what was measured:

| Source | Frames genuinely distinct | Exponent `a` |
|---|---|---|
| Fake or Fortune! | 25% | 1.041 |
| Mandy | 64% | 1.047 |
| Nighty Night | — | 0.945 |
| Ellie & Natasia | — | 0.938 |
| The IT Crowd | 96% | 0.857 |
| iPhone 4K60 | 100% | 0.678 |

**The exponent is a measure of how much new information each extra frame carries.** Where
most frames are near-duplicates the extra frames were free, so halving the frame rate
changes the bitrate not at all (Mandy: 1556 kbps at 50 fps against 1560 at 25 — the same
number). Where every frame is new, the survivors have to work much harder. Fake or
Fortune! is nominally 50 fps and carries about 12 fps of actual content.

So the shipped constant is set at the **hardest** case (all frames distinct), because the
target is also the `-maxrate` ceiling and the two errors are not symmetric: too generous
costs a missed saving, too tight silently costs quality. That is safe, and it leaves
savings on the table. Measured: Fake or Fortune! at OK tier would shrink 3658 → ~948 kbps,
a 74% saving, which the conservative constant declines to attempt.

**What it would take.** The novelty measurement itself is cheap and needs no decode of the
whole file — `mpdecimate` over a sample reports how many frames survive as distinct:

```sh
ffmpeg -ss <depth> -t 10 -i SRC -map 0:v:0 -vf mpdecimate -an -f null -
```

That fraction maps onto `a` (roughly `a ≈ 0.68` at 100% distinct rising toward `1.05` at
25%); the mapping wants fitting on more than six points before it is trusted, and the fit
should be recorded the way every other measured number in this project is. The work around
it is the larger part:

- a new probe stage in `vtc/ffprobe.py`, with a cost budget — it must not double scan time
- the value threaded through `MediaInfo` → `target_kbps()`, which currently takes `fps`
  alone, and into the preview/estimate paths so the UI agrees with the engine
- **it changes the ledger signature**, so existing entries would re-run once
- a decision about what a tier *means* when its target depends on content, which is the
  honest version of a question the tiers have so far been able to dodge

**Careful, when it is built:** never let the per-file exponent go above the conservative
constant for content the probe is unsure about. The failure mode is silent — a starved
encode still probes valid, still matches play length, and is still smaller, so every check
in the pipeline passes a file whose quality is below the tier it claims.
