#!/usr/bin/env python3
"""Build a blind comparison panel: bitrate-matched, randomised, key withheld.

Usage:
    panel.py <clip> <reference> <candidate> [candidate...] [--layout 2|4] [--duplicate]

Recipes are named in RECIPES below. The FIRST named recipe is the reference: it
is encoded once at its stated CRF and every other candidate is bisected onto the
bitrate it produced. SOURCE is always included and never matched.

Three things this gets right that the throwaway version did not:

  * **Bisection, not a slope.** The CRF-per-doubling slope is a property of the
    ENCODER AND ITS PARAMETER STRING, not of the codec: 8.58 for SVT-AV1 with psy
    off, 10.04 with variance boost on, and 6.06 (ObiWan) to 11.58 (Moscow) across
    sources. Any cached constant is valid only for the exact string it was
    measured on, so there is no safe one to cache.

  * **2-up as well as 4-up.** 960x1080 per side gives four times the picture area
    of a quadrant and makes it a straight A/B. That is the format for marginal
    discrimination -- and after three 4-ups the differences are marginal.

  * **--duplicate plants the reference twice** under two labels. Ranking two
    IDENTICAL encodes calibrates the session's noise floor before anything real
    is judged: whatever gap the viewer reports between them is the width of a
    result that means nothing. Section 5 found this by accident once; it is worth
    doing deliberately.
"""
import argparse, json, random, subprocess, sys
from datetime import datetime
from pathlib import Path

CLIPS = Path("/private/tmp/claude-501/-Users-simondavis-projects-very-thoughtful-compression"
             "/392d2c74-3fe5-4b45-b77f-14e59085cbb8/scratchpad/clips")
OUT = Path("/Volumes/Scout-3MacBackup/VTC-TESTING/VTC-compare")
WORK = Path(__file__).parent / "panelwork"

# Each recipe: (ffmpeg args before the output, the CRF flag to bisect, its range).
# The CRF value in the args is only a STARTING point for a candidate; for the
# reference it is the setting under test and is left alone.
RECIPES = {
    "H265":         (["-c:v", "libx265", "-preset", "medium", "-crf", "24",
                      "-profile:v", "main", "-pix_fmt", "yuv420p", "-tag:v", "hvc1"],
                     "-crf", (8, 51)),
    "H265-GRAIN":   (["-c:v", "libx265", "-preset", "medium", "-tune", "grain", "-crf", "24",
                      "-profile:v", "main", "-pix_fmt", "yuv420p", "-tag:v", "hvc1"],
                     "-crf", (8, 51)),
    "AV1-PSY":      (["-c:v", "libsvtav1", "-preset", "6", "-crf", "37",
                      "-pix_fmt", "yuv420p10le",
                      "-svtav1-params", "tune=0:enable-variance-boost=1"],
                     "-crf", (8, 63)),
    "AV1-PLAIN":    (["-c:v", "libsvtav1", "-preset", "6", "-crf", "28",
                      "-pix_fmt", "yuv420p10le", "-svtav1-params", "tune=0"],
                     "-crf", (8, 63)),
}

TOLERANCE = 0.03


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        sys.exit(f"FAIL: {' '.join(cmd[:14])}\n{r.stderr[-700:]}")
    return r


def dims(path):
    w, h = run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                "-show_entries", "stream=width,height", "-of", "csv=p=0",
                str(path)]).stdout.strip().split(",")[:2]
    return int(w), int(h)


def crop_for(src, layout):
    """A centred crop at 1:1, sized for the layout and the SOURCE's own frame.

    Never scale: any resample hides exactly the artefacts under test. At 1080p a
    960x1080 half-panel is half the frame; at 3840x2160 the same crop is an
    EIGHTH of it, which is harsher and more revealing -- which is why section 5
    prescribes 2-up for 4K rather than quartering it.
    """
    w, h = dims(src)
    cw, ch = (960, 1080) if layout == 2 else (960, 540)
    if cw > w or ch > h:
        sys.exit(f"source {w}x{h} is smaller than the {cw}x{ch} crop this layout needs")
    return f"crop={cw}:{ch}:{(w - cw) // 2}:{(h - ch) // 2}"


def kbps(path):
    d = json.loads(run(["ffprobe", "-v", "error", "-show_entries", "format=bit_rate,duration",
                        "-of", "json", str(path)]).stdout)["format"]
    if d.get("bit_rate") and int(d["bit_rate"]) > 0:
        return int(d["bit_rate"]) / 1000
    return path.stat().st_size * 8 / float(d["duration"]) / 1000


def encode(src, dst, recipe, crf):
    args, flag, _ = RECIPES[recipe]
    args = list(args)
    args[args.index(flag) + 1] = str(crf)
    run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(src),
         "-map", "0:v:0", "-an", "-sn", "-dn", "-map_chapters", "-1", *args, str(dst)])
    return kbps(dst)


def bisect(src, dst, recipe, target):
    """Integer-bisect CRF onto `target` kbps. ~6 encodes over a 55-wide range."""
    lo, hi = RECIPES[recipe][2]
    best = None
    while lo <= hi:
        crf = (lo + hi) // 2
        got = encode(src, dst, recipe, crf)
        err = got / target - 1
        print(f"  {recipe}: crf {crf} -> {got:.0f} kbps ({err:+.1%})", flush=True)
        if best is None or abs(err) < abs(best[2] / target - 1):
            best = (crf, dst.with_suffix(f".{crf}.mp4"), got)
            dst.replace(best[1])
        if abs(err) <= TOLERANCE:
            break
        if got > target:            # too many bits -> raise CRF
            lo = crf + 1
        else:
            hi = crf - 1
    return best


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("clip", help=f"clip name from {CLIPS}")
    ap.add_argument("reference", choices=RECIPES, help="encoded at its stated CRF; sets the target")
    # NB no `choices=` here: with nargs="*" argparse validates the EMPTY list
    # against choices and rejects it, so a zero-candidate run (the noise-floor
    # calibration) cannot be expressed. Validated by hand below instead.
    ap.add_argument("candidates", nargs="*", help="bisected onto the reference's bitrate")
    ap.add_argument("--layout", type=int, choices=(2, 4), default=2)
    ap.add_argument("--duplicate", action="store_true",
                    help="include the reference twice, to calibrate the noise floor")
    ap.add_argument("--ratio", type=float, default=1.0,
                    help="candidates target RATIO x the reference's bitrate, instead of "
                         "matching it. This is the staircase knob: r=0.75 is what the "
                         "model currently asserts AV1 needs against H.265. Raise r when "
                         "the candidate looks worse, lower it when it looks better; "
                         "'cannot tell' is the RESULT, not a failed comparison.")
    ap.add_argument("--with-source", action="store_true",
                    help="include the untouched source as a panel. Opt-in: in a 2-up "
                         "A/B of two near-identical encodes it would spend half the "
                         "screen on a question already answered 7 times out of 7.")
    a = ap.parse_args()
    clip, ref, cands, layout, dup = a.clip, a.reference, a.candidates, a.layout, a.duplicate
    unknown = [c for c in cands if c not in RECIPES]
    if unknown:
        ap.error(f"unknown recipe(s) {unknown}; choose from {list(RECIPES)}")
    src = CLIPS / f"{clip}.mp4"
    if not src.exists():
        ap.error(f"no such clip: {src}")
    WORK.mkdir(parents=True, exist_ok=True)

    print(f"clip: {src.name}  source {kbps(src):.0f} kbps", flush=True)
    refpath = WORK / f"{ref}.mp4"
    target = encode(src, refpath, ref, RECIPES[ref][0][RECIPES[ref][0].index("-crf") + 1])
    print(f"  {ref} (reference) -> {target:.0f} kbps", flush=True)

    panels = ([("SOURCE", src, kbps(src), "untouched")] if a.with_source else [])
    panels.append((ref, refpath, target, "reference"))
    want_kbps = target * a.ratio
    if a.ratio != 1.0:
        print(f"  candidates target r={a.ratio:.3f} x reference = {want_kbps:.0f} kbps",
              flush=True)
    for c in cands:
        crf, path, got = bisect(src, WORK / f"{c}.mp4", c, want_kbps)
        panels.append((c, path, got, f"crf {crf}, r={got / target:.3f}"))
    if dup:
        panels.append((f"{ref}-DUP", refpath, target, "IDENTICAL to the reference"))

    want = 2 if layout == 2 else 4
    if len(panels) != want:
        sys.exit(f"layout {layout} needs exactly {want} panels, got {len(panels)}: "
                 f"{[p[0] for p in panels]}")

    random.shuffle(panels)
    crop = crop_for(src, layout)
    if layout == 2:
        positions, stack = ["left", "right"], "[q0][q1]hstack[v]"
    else:
        positions = ["top-left", "top-right", "bottom-left", "bottom-right"]
        stack = "[q0][q1]hstack[t];[q2][q3]hstack[b];[t][b]vstack[v]"

    # ⛔ The filename must NOT name the recipes. It was built from the SHUFFLED
    # list, so it gave away both the composition and the left/right order — in
    # the player's title bar, before a single frame was watched. A blind test
    # whose answer is in its own filename is not a blind test. The composition
    # lives in the sidecar key and nowhere else.
    tag = "SPLIT" if layout == 2 else "PANEL"
    stamp = datetime.now().strftime("%m%d-%H%M%S")
    montage = OUT / f"{tag}-{clip}-{stamp}.mp4"
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
    for _, p, _, _ in panels:
        cmd += ["-i", str(p)]
    fc = "".join(f"[{i}:v]{crop},setsar=1[q{i}];" for i in range(len(panels))) + stack
    cmd += ["-filter_complex", fc, "-map", "[v]", "-c:v", "libx264", "-qp", "0",
            "-preset", "veryfast", "-pix_fmt", "yuv420p", str(montage)]
    run(cmd)

    lines = [f"{clip} - {'2-up split screen' if layout == 2 else '4-up panel'}",
             f"  crops at 1:1, no scaling, lossless montage", ""]
    lines += [f"  {pos:<13}{p[0]}" for pos, p in zip(positions, panels)]
    lines += [""] + [f"  {n:<15}{b:>8.0f} kbps   {note}" for n, _, b, note in panels]
    key = OUT / f"KEY-{montage.stem}.txt"
    key.write_text("\n".join(lines) + "\n")
    print("\nbuilt:", montage, "\nkey:  ", key, "\n" + "\n".join(lines))


if __name__ == "__main__":
    main()
