#!/usr/bin/env python3
"""Blind panel: does variance boost hold on fresh content, and is enable-tf costing grain?

Four quadrants of the same clip, bitrate-matched by CRF search, tiled 1:1 at
960x540, montage encoded losslessly, placement randomised and the key written
to a sidecar that the viewer does not open first.

  SOURCE                     the untouched clip
  H265                       libx265 crf 24, 8-bit (the shipping decision)
  AV1-PSY                    tune=0 + enable-variance-boost=1, 10-bit
  AV1-PSY-NOTF               the same, plus enable-tf=0

Only ONE thing differs between AV1-PSY and AV1-PSY-NOTF, so a difference is
attributable. Bundling enable-tf with variance boost would credit a win to the
wrong flag -- the peer session's point, and a good one.
"""
import json, math, random, subprocess, sys
from pathlib import Path

CLIPS = Path("/private/tmp/claude-501/-Users-simondavis-projects-very-thoughtful-compression"
             "/392d2c74-3fe5-4b45-b77f-14e59085cbb8/scratchpad/clips")
OUT = Path("/Volumes/Scout-3MacBackup/VTC-TESTING/VTC-compare")
WORK = Path("/private/tmp/claude-501/-Users-simondavis"
            "/ba0a6a5c-b5aa-4c00-bb55-aeca376a4b31/scratchpad/psy")
WORK.mkdir(parents=True, exist_ok=True)

CLIP = sys.argv[1] if len(sys.argv) > 1 else "Shadows"
SRC = CLIPS / f"{CLIP}.mp4"

# 960x540 crop at 1:1 from the centre. Never scale -- a resample hides exactly
# the artefacts under test (docs/measuring-quality.md section 5).
CROP = "crop=960:540:480:270"
TOLERANCE = 0.03          # +/-3% counts as bitrate-matched
AV1_SLOPE = 8.58          # measured CRF per doubling of bitrate for SVT-AV1


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        print("FAIL:", " ".join(cmd[:12]), "\n", r.stderr[-600:])
        sys.exit(1)
    return r


def kbps(path):
    r = run(["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=bit_rate", "-show_entries", "format=bit_rate,duration",
             "-of", "json", str(path)])
    d = json.loads(r.stdout)
    br = d["format"].get("bit_rate")
    if br and int(br) > 0:
        return int(br) / 1000
    size = path.stat().st_size * 8
    return size / float(d["format"]["duration"]) / 1000


def enc_h265(dst, crf):
    run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(SRC),
         "-map", "0:v:0", "-an", "-sn", "-dn", "-map_chapters", "-1",
         "-c:v", "libx265", "-crf", str(crf), "-preset", "medium",
         "-profile:v", "main", "-pix_fmt", "yuv420p", "-tag:v", "hvc1", str(dst)])


def enc_av1(dst, crf, params):
    run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(SRC),
         "-map", "0:v:0", "-an", "-sn", "-dn", "-map_chapters", "-1",
         "-c:v", "libsvtav1", "-crf", str(crf), "-preset", "6",
         "-pix_fmt", "yuv420p10le", "-svtav1-params", params, str(dst)])


def match_av1(name, params, target, start_crf):
    """Walk CRF to land within TOLERANCE of target, using the measured slope."""
    crf = start_crf
    dst = WORK / f"{name}.mp4"
    for attempt in range(6):
        enc_av1(dst, crf, params)
        got = kbps(dst)
        err = got / target - 1
        print(f"  {name}: crf {crf} -> {got:.0f} kbps ({err:+.1%})", flush=True)
        if abs(err) <= TOLERANCE:
            return dst, crf, got
        # CRF moves by the measured slope per doubling of bitrate; over target
        # means raise CRF. This is the same relationship the band was fitted on.
        step = AV1_SLOPE * math.log2(got / target)
        crf = max(1, min(63, round(crf + step)))
    return dst, crf, got


print(f"clip: {SRC.name}  source {kbps(SRC):.0f} kbps", flush=True)

h265 = WORK / "h265.mp4"
enc_h265(h265, 24)
target = kbps(h265)
print(f"  H265 crf 24 -> {target:.0f} kbps  (this is the match target)", flush=True)

psy, psy_crf, psy_br = match_av1("av1-psy", "tune=0:enable-variance-boost=1", target, 34)
notf, notf_crf, notf_br = match_av1("av1-psy-notf",
                                    "tune=0:enable-variance-boost=1:enable-tf=0", target, psy_crf)

quads = [
    ("SOURCE",       SRC,   kbps(SRC),  "-"),
    ("H265-crf24",   h265,  target,     "crf 24, 8-bit, preset medium"),
    ("AV1-PSY",      psy,   psy_br,     f"crf {psy_crf}, tune=0 + variance-boost, 10-bit"),
    ("AV1-PSY-NOTF", notf,  notf_br,    f"crf {notf_crf}, the same + enable-tf=0"),
]
random.shuffle(quads)
pos = ["top-left", "top-right", "bottom-left", "bottom-right"]

montage = OUT / f"panel-PSYTF-{CLIP}.mp4"
cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
for _, p, _, _ in quads:
    cmd += ["-i", str(p)]
fc = "".join(f"[{i}:v]{CROP},setsar=1[q{i}];" for i in range(4))
fc += "[q0][q1]hstack[t];[q2][q3]hstack[b];[t][b]vstack[v]"
cmd += ["-filter_complex", fc, "-map", "[v]",
        "-c:v", "libx264", "-qp", "0", "-preset", "veryfast",
        "-pix_fmt", "yuv420p", str(montage)]
run(cmd)

key = OUT / f"KEY-panel-PSYTF-{CLIP}.txt"
lines = [f"{CLIP} 1080p - variance boost, and whether enable-tf costs grain",
         "  (960x540 crops at 1:1, no scaling, lossless montage)", ""]
for p, (name, _, br, note) in zip(pos, quads):
    lines.append(f"  {p:<13}{name}")
lines.append("")
for name, _, br, note in quads:
    lines.append(f"  {name:<15}{br:>8.0f} kbps   {note}")
key.write_text("\n".join(lines) + "\n")

print("\nbuilt:", montage)
print("key:  ", key)
print("\n".join(lines))
