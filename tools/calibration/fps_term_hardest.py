"""The fps exponent for the HARDEST case, at a compliant 30s.

`fps_term.py` measured the 4K60 iPhone clip at 25s, which breaks the 30s-minimum rule
(Simon, 2026-09-12). That clip matters more than its source suggests: of everything
measured it is the only one where EVERY frame carries new information (100% survive
mpdecimate, against 96% for The IT Crowd and 25% for Fake or Fortune!), and the exponent
tracks exactly that — 0.678 here at 100% novelty, rising to ~1.05 when most frames are
duplicates. It is therefore the case that has to set the number. On the SOFTWARE path
that is a ceiling argument: `-maxrate` must accommodate content whose frames are all
distinct, because an over-tight ceiling silently costs quality while an over-generous one
costs only a missed saving (CRF binds and the file lands at its natural rate regardless).
On the DEFAULT hardware path there is no CRF and the target IS the delivered bitrate, so
the same choice is simply the least aggressive reading of the measurement.

So the number that goes into model.py has to come from a 30s measurement of this clip.
Method as fps_term.py Stage C: scenecut off, keyint pinned, non-I frames only.
"""
import json, math, os, subprocess, sys, time

SRC = '/Volumes/Scout-3MacBackup/VTC-TESTING/IMG_0378 2.MOV'
SS, DUR, CRF = 7, 30, 19
TMP = 'hardest_tmp.mp4'
OUT = 'fps_term_hardest.json'
res = json.load(open(OUT)) if os.path.exists(OUT) else {}

# ⚠️ THE CACHE KEY CARRIES EVERY PARAMETER, not just the frame rate. This harness
# sources a constant that ships in model.py, and the exact thing that happened while
# fitting it was a re-run with DUR changed from 25 to 30 — on a key of `str(fps)` alone
# that would have silently replayed the 25s answer and reported it as the 30s one.
# A scratch harness can get away with a partial key; this one cannot.
def cache_key(fps: int) -> str:
    return f"{os.path.basename(SRC)}|ss{SS}|dur{DUR}|crf{CRF}|{fps}fps"


def frame_bits(p):
    rows = subprocess.run(['ffprobe', '-v', 'quiet', '-select_streams', 'v:0',
                           '-show_entries', 'frame=pict_type,pkt_size', '-of', 'csv=p=0', p],
                          capture_output=True, text=True).stdout.strip().splitlines()
    ni_b = ni_n = i_n = 0
    for row in rows:
        parts = row.split(',')
        if len(parts) < 2:
            continue
        sz = next((int(x) for x in parts if x.strip().isdigit()), 0)
        pt = next((x.strip() for x in parts if x.strip().isalpha()), '')
        if pt == 'I':
            i_n += 1
        else:
            ni_b += sz; ni_n += 1
    return ni_b * 8 / ni_n, ni_n + i_n, i_n


print(f"4K, {DUR}s from {SS}s, x265 medium crf {CRF}, scenecut off, keyint pinned")
print(f"{'fps':>4s} {'kbps':>9s} {'frames':>7s} {'I':>3s} {'nonI bits':>11s} {'secs':>6s}")
for fps in (60, 30):
    key = cache_key(fps)
    if key not in res:
        t0 = time.time()
        r = subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y',
                            '-ss', str(SS), '-t', str(DUR), '-i', SRC,
                            '-map', '0:v:0', '-an', '-sn', '-dn', '-vf', f'fps={fps}',
                            '-c:v', 'libx265', '-crf', str(CRF), '-preset', 'medium',
                            '-x265-params', 'scenecut=0:keyint=250:min-keyint=250',
                            '-profile:v', 'main', '-pix_fmt', 'yuv420p', '-tag:v', 'hvc1',
                            TMP], capture_output=True, text=True)
        if r.returncode:
            print('FAIL', r.stderr[-400:]); sys.exit(1)
        secs = time.time() - t0
        f = json.loads(subprocess.run(['ffprobe', '-v', 'quiet', '-print_format', 'json',
                                       '-show_format', TMP],
                                      capture_output=True, text=True).stdout)['format']
        ni, n, i_n = frame_bits(TMP)
        os.remove(TMP)
        res[key] = dict(fps=fps, kbps=round(float(f['size']) * 8 / float(f['duration']) / 1000, 1),
                        frames=n, iframes=i_n, nonI_bits=round(ni), secs=round(secs, 1))
        json.dump(res, open(OUT, 'w'), indent=1)
    r = res[key]
    print(f"{r['fps']:>4d} {r['kbps']:>9.1f} {r['frames']:>7d} {r['iframes']:>3d} "
          f"{r['nonI_bits']:>11,d} {r['secs']:>6.1f}", flush=True)

ratio = res[cache_key(30)]['nonI_bits'] / res[cache_key(60)]['nonI_bits']
a = math.log(ratio) / math.log(2)
print(f"\na 30 fps frame costs {ratio:.3f}x a 60 fps frame  ->  bits/frame ~ fps**-{a:.3f}")
print(f"(the same clip at 25s gave 0.642 for this pair; 30s is the compliant number)")
print(f"\nre-pricing fps -> 24*(fps/24)**{1 - a:.3f}, anchored at the band's 23.988 fps:")
for f in (23.976, 25, 29.97, 50, 60):
    print(f"  {f:6.2f} fps  target {24 * (f / 24) ** (1 - a) / f:.3f}x today's")
