"""Does the model's fps term hold? Three questions, at 4K, on real footage.

The model prices a tier as `bpp * pixels * fps`, i.e. it asserts that a frame
costs the SAME number of bits whatever the frame rate. Nothing has ever tested
that: the 1080p calibration set is entirely 23.98-25 fps, so the fps term has
only ever been exercised over a 4% range.

STAGE A — the fps term itself, content held constant. One 4K60 source, encoded
  at 60 fps and at 30 fps (every other frame, via `fps=30`), at the same CRF,
  PURE CRF with no ceiling so we measure what the encoder naturally wants. If
  bits-per-frame is equal across the two arms the term is right; if the 30 fps
  arm wants more per frame (bigger temporal gaps -> larger residuals) then at a
  given tier a low-fps file is being under-fed relative to a high-fps one, by
  the ratio measured here.
  Both arms decode the SAME source and the lower-fps arms keep every Nth frame of
  it, so the kept pixels are identical — no intermediate, no generation loss
  asymmetry.

  ⚠️ THE I-FRAME SHARE IS THE CONFOUND TO WATCH, and it is why this runs on 25s
  and not on a short sample. x265's keyint is 250 FRAMES at every frame rate
  (verified: "Keyframe min / max" reads 250 at 60, 30 and 15 fps), so over a clip
  longer than one keyint both arms carry the same FRACTION of I-frames and a
  per-frame comparison is fair. On a 2s sample it is not: the whole clip is one
  GOP, so the 30 fps arm's single I-frame is 1-in-60 against the 60 fps arm's
  1-in-120, and an I-frame costs several times a P-frame. A smoke run at 2s
  reported 1.49x for exactly that reason and ~5 points of it were this artefact.
  Scenecut is also on (bias 40), and a scene cut lands in both arms alike over
  fewer frames in the slower one — so the I-frame COUNT is recorded per arm and
  printed, rather than assumed away.

STAGE B — convergence at 4K, with the shipping shape (capped CRF at the measured
  band, -maxrate AND -bufsize at the tier target). The bands were fitted at
  1080p. At 4K this asks which regime each tier lands in — CRF-bound (below
  target, the intended "don't pad a file that doesn't need it") or maxrate-bound
  (at the ceiling, where the tier has stopped meaning a quality and means a
  bitrate) — and whether the achieved rate clears the 1.10 convergence gate so a
  re-run leaves the file alone.

STAGE C — Stage A's frame-rate curve again with the I-frame confound removed at
  source (scenecut off, keyint pinned) and measured on non-I frames only, which
  turns a direction into an exponent. See the note above the stage.

⚠️ SOURCES ARE iPhone Dolby Vision (HEVC Main 10, HLG/BT.2020). VTC would never
  re-encode them (hevc is MODERN — remux only), and they are 10-bit HDR where the
  bands were fitted on 8-bit SDR. So these numbers are about the ARITHMETIC and
  the RATE BEHAVIOUR, which is all this script claims. Nothing here is a quality
  judgement and none of it should be turned into one: that needs SDR 8-bit H.264
  clips and a blind panel. Encoding forces 8-bit main profile deliberately, to
  measure the path a real (H.264) library file would take — see 1892510.
"""
import json, math, os, re, subprocess, sys, time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
from vtc.model import Tier, OutCodec, target_kbps, source_bpp, over_target, TIER_OVER_TOLERANCE
from vtc.encode import crf_for_tier, Mode

T = '/Volumes/Scout-3MacBackup/VTC-TESTING/'
CLIPS = {
    # name          file                 ss   probed fps   note
    'iP30': dict(src=T + 'IMG_0375.MOV',   ss=1,  fps=30, portrait=True),
    'iP60': dict(src=T + 'IMG_0378 2.MOV', ss=7,  fps=60, portrait=False),
}
DUR = int(os.environ.get('FPS_TERM_DUR', 25))   # seconds of each clip (2 for a smoke test)
PIXELS = 3840 * 2160           # both are 4K; rotation does not change the count
TMP = 'fps_tmp.mp4'
OUT = 'fps_term.json'
res = json.load(open(OUT)) if os.path.exists(OUT) else {}


def probe(p):
    d = json.loads(subprocess.run(
        ['ffprobe', '-v', 'quiet', '-print_format', 'json', '-show_streams', '-show_format', p],
        capture_output=True, text=True).stdout)
    v = next(s for s in d['streams'] if s['codec_type'] == 'video')
    f = d['format']
    dur = float(f['duration'])
    # I-frame count and per-frame-type bits: the one confound a per-frame comparison
    # across frame rates cannot assume away (see the note above), so it is measured,
    # not inferred. `nonI_bits` is the mean bits of every non-I frame, which is the
    # confound-free version of bits-per-frame.
    rows = subprocess.run(['ffprobe', '-v', 'quiet', '-select_streams', 'v:0',
                           '-show_entries', 'frame=pict_type,pkt_size',
                           '-of', 'csv=p=0', p],
                          capture_output=True, text=True).stdout.strip().splitlines()
    ni_bytes = ni_count = i_bytes = i_count = 0
    for row in rows:
        parts = row.split(',')
        if len(parts) < 2:
            continue
        # ffprobe emits the two fields in schema order (pkt_size, pict_type);
        # find them by shape rather than by position so a reorder cannot skew this.
        sz = next((int(x) for x in parts if x.strip().isdigit()), 0)
        pt = next((x.strip() for x in parts if x.strip().isalpha()), '')
        if pt == 'I':
            i_bytes += sz; i_count += 1
        else:
            ni_bytes += sz; ni_count += 1
    return dict(w=v['width'], h=v['height'], frames=int(v.get('nb_frames', 0)),
                iframes=i_count, dur=dur,
                i_bits=round(i_bytes * 8 / i_count) if i_count else 0,
                nonI_bits=round(ni_bytes * 8 / ni_count) if ni_count else 0,
                kbps=float(f['size']) * 8 / dur / 1000)


def encode(src, ss, fps, args, portrait):
    """Run one encode, return (probe dict, seconds, x265 keyint). Output deleted."""
    # verbose, not info: x265's "Keyframe min / max" banner only prints at verbose,
    # and the keyint it reports is part of the measurement.
    cmd = ['ffmpeg', '-hide_banner', '-loglevel', 'verbose', '-y']
    if portrait:
        # Encode the frame as STORED (3840x2160) rather than as displayed. The
        # display matrix says -90; autorotating would hand the encoder a 2160x3840
        # portrait frame, which is not the 4K landscape geometry the bands assume.
        cmd += ['-noautorotate']
    cmd += ['-ss', str(ss), '-t', str(DUR), '-i', src,
            '-map', '0:v:0', '-an', '-sn', '-dn',
            # Force CFR. The 60 fps source is only NEAR-CFR (a handful of 1/55 and
            # 1/66 deltas), and the whole point is that frame rate is the only
            # thing differing between the arms.
            '-vf', f'fps={fps}',
            *args, TMP]
    t0 = time.time()
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        print('FAIL\n', ' '.join(cmd), '\n', r.stderr[-600:]); sys.exit(1)
    secs = time.time() - t0
    m = re.search(r'Keyframe min / max[^:]*:\s*\d+\s*/\s*(\d+)', r.stderr)
    info = probe(TMP)
    os.remove(TMP)
    return info, secs, (m.group(1) if m else '?')


def record(key, row):
    res[key] = row
    json.dump(res, open(OUT, 'w'), indent=1)


# ── Stage A — the fps term, content held constant ─────────────────────────────
# Pure CRF: no -maxrate, no -bufsize, so we see what the encoder naturally wants.
# Three rungs of the x265 SHRINK band at 60/30, and a fuller frame-rate curve at
# one rung — a single ratio cannot show whether the relationship is linear, and
# the model's claim is about the whole range, not one pair.
# Only exact divisors of 60: `fps=N` then picks every 60/N-th frame with no
# duplication, so every arm is a strict subset of the same decoded frames.
ARMS = {24: (60, 30), 19: (60, 30, 20, 15), 16: (60, 30)}
print('══ STAGE A — bits per frame vs frame rate, same content, pure CRF ══')
print(f"{'arm':16s} {'kbps':>9s} {'frames':>7s} {'I':>4s} {'bits/frame':>11s} "
      f"{'bpp':>8s} {'keyint':>7s} {'secs':>6s}")
for crf, rates in ARMS.items():
    for fps in rates:
        key = f'A/crf{crf}/{fps}fps'
        if key not in res:
            info, secs, keyint = encode(
                CLIPS['iP60']['src'], CLIPS['iP60']['ss'], fps,
                ['-c:v', 'libx265', '-crf', str(crf), '-preset', 'medium',
                 '-profile:v', 'main', '-pix_fmt', 'yuv420p', '-tag:v', 'hvc1'],
                portrait=False)
            bits_per_frame = info['kbps'] * 1000 / fps
            record(key, dict(crf=crf, fps=fps, kbps=round(info['kbps'], 1),
                             frames=info['frames'], iframes=info['iframes'],
                             bits_per_frame=round(bits_per_frame),
                             bpp=round(bits_per_frame / PIXELS, 6),
                             ishare=round(info['iframes'] / max(info['frames'], 1), 4),
                             keyint=keyint, secs=round(secs, 1)))
        r = res[key]
        print(f"crf{crf:<3d} {r['fps']:>3d} fps   {r['kbps']:9.1f} {r['frames']:>7d} "
              f"{r['iframes']:>4d} {r['bits_per_frame']:>11,d} {r['bpp']:8.5f} "
              f"{r['keyint']:>7s} {r['secs']:>6.1f}", flush=True)
    ref = res[f'A/crf{crf}/60fps']
    for fps in rates[1:]:
        r = res[f'A/crf{crf}/{fps}fps']
        ratio = r['bits_per_frame'] / ref['bits_per_frame']
        # The model says this ratio is 1.000 at every frame rate. Anything above
        # means a slower file's frames cost more, so a tier priced linearly in fps
        # hands the slower file proportionally fewer bits than its frames want.
        flag = '' if abs(r['ishare'] - ref['ishare']) < 0.002 else '  ⚠ I-share differs'
        print(f"  -> crf {crf}: a {fps} fps frame costs {ratio:.3f}x a 60 fps frame "
              f"(model's claim: 1.000){flag}", flush=True)
    print(flush=True)

# ── Stage B — convergence at 4K with the shipping shape ───────────────────────
print('══ STAGE B — capped CRF at the band, -maxrate/-bufsize at target ══')
print(f"{'clip/tier':22s} {'fps':>3s} {'target':>8s} {'crf':>4s} {'kbps':>9s} "
      f"{'ratio':>6s} {'regime':>12s} {'gate':>5s} {'secs':>6s}")
for name, c in CLIPS.items():
    for tier in Tier:
        key = f'B/{name}/{tier.name}'
        crf = crf_for_tier(tier, Mode.SHRINK)[1]          # libx265 column
        tgt = target_kbps(tier, PIXELS, c['fps'], OutCodec.H265)
        if key not in res:
            info, secs, keyint = encode(
                c['src'], c['ss'], c['fps'],
                ['-c:v', 'libx265', '-crf', str(crf), '-preset', 'medium',
                 '-maxrate', f'{tgt}k', '-bufsize', f'{tgt}k',
                 '-profile:v', 'main', '-pix_fmt', 'yuv420p', '-tag:v', 'hvc1'],
                portrait=c['portrait'])
            ratio = info['kbps'] / tgt
            record(key, dict(fps=c['fps'], target=tgt, crf=crf, iframes=info['iframes'],
                             kbps=round(info['kbps'], 1), ratio=round(ratio, 3),
                             # >0.97 of a ceiling that tight means the ceiling is
                             # what bound the rate, not the CRF.
                             regime='maxrate-bound' if ratio > 0.97 else 'CRF-bound',
                             converges=not over_target(info['kbps'], tgt),
                             keyint=keyint, secs=round(secs, 1)))
        r = res[key]
        print(f"{name + '/' + tier.name:22s} {r['fps']:>3d} {r['target']:>7d}k {r['crf']:>4d} "
              f"{r['kbps']:9.1f} {r['ratio']:6.3f} {r['regime']:>12s} "
              f"{'OK' if r['converges'] else 'FAIL':>5s} {r['secs']:>6.1f}", flush=True)

# ── Stage C — the same curve with the I-frame confound removed at source ──────
# Stage A's 20 and 15 fps arms carry roughly DOUBLE the I-frame share of the 60 fps
# arm: keyint is 250 frames in all of them, but scenecut is on and a scene cut fires
# on the same content over fewer frames when the frame rate is lower — so the slower
# arms got extra I-frames, which inflates their bits-per-frame. Its 60 vs 30 pair is
# clean (0.733% vs 0.667% I-share) and needs no correction; the wider curve does.
#
# Fix it two ways at once rather than arguing about the size of the artefact:
#   scenecut=0 with a pinned keyint  -> I-frames land on the same frame INDEXES
#   compare nonI_bits, not bits/frame -> I-frames excluded from the number entirely
# ⚠️ scenecut=0 is NOT a shipping setting. This arm exists to isolate one term and
# nothing here should be carried into the encoder config.
print('══ STAGE C — the curve with scenecut off, comparing non-I frames only ══')
print(f"{'arm':16s} {'kbps':>9s} {'frames':>7s} {'I':>4s} {'I bits':>10s} "
      f"{'nonI bits':>10s} {'secs':>6s}")
CRF_C = 19
for fps in (60, 30, 20, 15):
    key = f'C/crf{CRF_C}/{fps}fps'
    if key not in res:
        info, secs, keyint = encode(
            CLIPS['iP60']['src'], CLIPS['iP60']['ss'], fps,
            ['-c:v', 'libx265', '-crf', str(CRF_C), '-preset', 'medium',
             '-x265-params', 'scenecut=0:keyint=250:min-keyint=250',
             '-profile:v', 'main', '-pix_fmt', 'yuv420p', '-tag:v', 'hvc1'],
            portrait=False)
        record(key, dict(crf=CRF_C, fps=fps, kbps=round(info['kbps'], 1),
                         frames=info['frames'], iframes=info['iframes'],
                         i_bits=info['i_bits'], nonI_bits=info['nonI_bits'],
                         secs=round(secs, 1)))
    r = res[key]
    print(f"crf{CRF_C:<3d} {r['fps']:>3d} fps   {r['kbps']:9.1f} {r['frames']:>7d} "
          f"{r['iframes']:>4d} {r['i_bits']:>10,d} {r['nonI_bits']:>10,d} "
          f"{r['secs']:>6.1f}", flush=True)

ref = res[f'C/crf{CRF_C}/60fps']
print(f"\n  a non-I frame's cost against the 60 fps arm (model's claim: 1.000 at every rate)")
fits = []
for fps in (30, 20, 15):
    r = res[f'C/crf{CRF_C}/{fps}fps']
    ratio = r['nonI_bits'] / ref['nonI_bits']
    # If cost per frame goes as fps**-a, then a = ln(ratio) / ln(60/fps).
    a = math.log(ratio) / math.log(60 / fps)
    fits.append(a)
    print(f"    {fps:>3d} fps  {ratio:6.3f}x   implies bits/frame ~ fps**-{a:.3f}")
mean_a = sum(fits) / len(fits)
print(f"\n  exponent across the three points: {min(fits):.3f}-{max(fits):.3f} "
      f"(mean {mean_a:.3f}); the model assumes 0.000")
print(f"  so bitrate goes as fps**{1 - mean_a:.3f}, not fps**1.000 — at 60 fps against a "
      f"24 fps fit that is {(60 / 24) ** (1 - mean_a) / (60 / 24):.3f}x the bits the model asks for")

print(f'\nDONE — gate is {TIER_OVER_TOLERANCE:.2f}x target; results in {OUT}')
