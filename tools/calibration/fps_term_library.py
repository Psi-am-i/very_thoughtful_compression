"""The fps term again, on LIBRARY footage, over the range the library actually spans.

`fps_term.py` measured `bits/frame ∝ fps^-0.634` on one 4K iPhone clip. That is not a
number to re-price a library with: phone footage is ISP-denoised and grain-free, it was
handheld (motion is what this term is about), and it was HDR 10-bit HEVC, which VTC would
never re-encode. The exponent is content-dependent even though its direction is physics.

So measure it again on the real thing. Five shows in the library are 50 fps AND H.264 AND
SDR, which is the one frame rate above the calibration anchor that exists here in any
quantity. Each is encoded twice at the same CRF — every frame (50 fps) and every other
frame (25 fps) — so the content, the pixels of the surviving frames, and the encoder
settings are identical and only the frame rate differs. 25 vs 50 is also precisely the
range that matters: the bands are calibrated at a median of 23.988 fps across their eight
fitting clips, so 25 fps is the anchor and 50 fps is where the model is most wrong.

⚠️ 30 SECONDS IS THE MINIMUM FOR ANY TEST CLIP — Simon's rule, 2026-09-12, and it is not
arbitrary here: x265's keyint is 250 FRAMES, so a short clip is one GOP and the slower arm
then carries double the I-frame share, which inflates its bits-per-frame. 30s at 25 fps is
750 frames, three keyints. The earlier 4K run used 25s and predates the rule.

Method follows fps_term.py Stage C, which is the confound-free one:
  scenecut=0 with a pinned keyint -> I-frames land on the same frame INDEXES in both arms
  compare non-I frames only       -> I-frames excluded from the number entirely
⚠️ scenecut=0 is NOT a shipping setting; it isolates one term and goes no further.
"""
import json, math, os, subprocess, sys, time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

T = '/Volumes/RAID/TV/'
SHOWS = {
    'ITCrowd':   'The IT Crowd/Season 01/The IT Crowd (2006) - S01E01 - Yesterdays Jam [WEBRip-1080p][AAC 2.0][x264]-Lostfilm.mp4',
    'Mandy':     'Mandy/Season 01/Mandy (2020) - S01E01 - Jobseeker [WEBDL-1080p][AAC 2.0][h264]-NTb.mp4',
    'NightyNight': 'Nighty Night/Season 01/Nighty Night (2004) - S01E01 - Episode One [WEBDL-480p][AAC 2.0][h264]-HiNGS.mkv',
    'Ellie':     'Ellie & Natasia/Season 01/Ellie & Natasia (2022) - S01E01 - Episode 1 [WEBDL-1080p][AAC 2.0][h264]-playWEB.mp4',
    'FakeFortune': 'Fake or Fortune!/Season 13/Fake or Fortune! (2011) - S13E05 - What Happened Next A Double Whodunnit [WEBDL-1080p][AAC 2.0][h264]-7VFr33104D.mp4',
}
DUR = 30                        # the minimum, per the rule above
DEPTH = 0.40                    # sample 40% into the episode, as mkclips.py does
RATES = (50, 25)
CRFS = (24, 19)                 # OK and EXCELLENT rungs of the x265 SHRINK band
TMP = 'fpslib_tmp.mp4'
OUT = 'fps_term_library.json'
res = json.load(open(OUT)) if os.path.exists(OUT) else {}


def probe(p):
    d = json.loads(subprocess.run(
        ['ffprobe', '-v', 'quiet', '-print_format', 'json', '-show_streams', '-show_format', p],
        capture_output=True, text=True).stdout)
    v = next(s for s in d['streams'] if s['codec_type'] == 'video')
    a, b = v['avg_frame_rate'].split('/')
    return dict(w=v['width'], h=v['height'], fps=float(a) / float(b),
                dur=float(d['format']['duration']),
                kbps=float(d['format']['size']) * 8 / float(d['format']['duration']) / 1000)


def frame_bits(p):
    """(non-I mean bits, I mean bits, frame count, I count) for an encoded file."""
    rows = subprocess.run(['ffprobe', '-v', 'quiet', '-select_streams', 'v:0',
                           '-show_entries', 'frame=pict_type,pkt_size', '-of', 'csv=p=0', p],
                          capture_output=True, text=True).stdout.strip().splitlines()
    ni_b = ni_n = i_b = i_n = 0
    for row in rows:
        parts = row.split(',')
        if len(parts) < 2:
            continue
        sz = next((int(x) for x in parts if x.strip().isdigit()), 0)
        pt = next((x.strip() for x in parts if x.strip().isalpha()), '')
        if pt == 'I':
            i_b += sz; i_n += 1
        else:
            ni_b += sz; ni_n += 1
    return (ni_b * 8 / ni_n if ni_n else 0, i_b * 8 / i_n if i_n else 0, ni_n + i_n, i_n)


print(f"30s samples, 40% into each episode. x265 medium, scenecut off, keyint pinned.")
print(f"{'show':14s} {'res':>10s} {'crf':>4s} {'fps':>4s} {'kbps':>8s} {'frames':>7s} "
      f"{'I':>3s} {'nonI bits':>10s} {'secs':>6s}")
exps = []
for name, rel in SHOWS.items():
    src = T + rel
    if not os.path.exists(src):
        print('MISSING', name); continue
    s = probe(src)
    ss = round(s['dur'] * DEPTH)
    for crf in CRFS:
        for fps in RATES:
            key = f'{name}/crf{crf}/{fps}fps'
            if key not in res:
                cmd = ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y',
                       '-ss', str(ss), '-t', str(DUR), '-i', src,
                       '-map', '0:v:0', '-an', '-sn', '-dn',
                       '-vf', f'fps={fps}',
                       '-c:v', 'libx265', '-crf', str(crf), '-preset', 'medium',
                       '-x265-params', 'scenecut=0:keyint=250:min-keyint=250',
                       '-profile:v', 'main', '-pix_fmt', 'yuv420p', '-tag:v', 'hvc1', TMP]
                t0 = time.time()
                r = subprocess.run(cmd, capture_output=True, text=True)
                if r.returncode:
                    print('FAIL', key, r.stderr[-400:]); sys.exit(1)
                secs = time.time() - t0
                o = probe(TMP)
                ni, ib, n, i_n = frame_bits(TMP)
                os.remove(TMP)
                res[key] = dict(w=o['w'], h=o['h'], crf=crf, fps=fps,
                                kbps=round(o['kbps'], 1), frames=n, iframes=i_n,
                                nonI_bits=round(ni), i_bits=round(ib), secs=round(secs, 1))
                json.dump(res, open(OUT, 'w'), indent=1)
            r = res[key]
            print(f"{name:14s} {str(r['w']) + 'x' + str(r['h']):>10s} {r['crf']:>4d} "
                  f"{r['fps']:>4d} {r['kbps']:>8.1f} {r['frames']:>7d} {r['iframes']:>3d} "
                  f"{r['nonI_bits']:>10,d} {r['secs']:>6.1f}", flush=True)
        a, b = res[f'{name}/crf{crf}/25fps'], res[f'{name}/crf{crf}/50fps']
        ratio = a['nonI_bits'] / b['nonI_bits']
        exp = math.log(ratio) / math.log(2)      # 50 -> 25 is one halving
        exps.append(exp)
        print(f"  -> {name} crf {crf}: a 25 fps frame costs {ratio:.3f}x a 50 fps frame "
              f"-> fps**-{exp:.3f}\n", flush=True)

exps.sort()
med = exps[len(exps) // 2] if len(exps) % 2 else (exps[len(exps) // 2 - 1] + exps[len(exps) // 2]) / 2
print(f"exponent across {len(exps)} (show x crf) pairs: {min(exps):.3f}-{max(exps):.3f}, "
      f"median {med:.3f}")
print(f"the model assumes 0.000 (a frame costs the same at any rate)")
print(f"iPhone 4K clip gave 0.634 — {'CONSISTENT' if abs(med - 0.634) < 0.12 else 'DIFFERENT'}")
print(f"\nAt a 24 fps anchor, re-pricing fps -> 24*(fps/24)**{1 - med:.3f} gives targets of:")
for f in (23.976, 25, 29.97, 50, 60):
    print(f"  {f:6.2f} fps  {24 * (f / 24) ** (1 - med) / f:.3f}x today's target")
