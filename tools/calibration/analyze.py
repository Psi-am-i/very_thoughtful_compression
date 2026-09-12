"""Fit crf = a + b*log2(bpp) per source, then build the AV1 ladder.

Same method as the committed x264/x265 bands: the SHAPE comes from the median
slope, the POSITION from the median CRF across the sources where the anchor rung
is actually live. Fitting each rung on its own live subset is what produced a
non-monotonic ladder last time — the subsets are not comparable — so that is
reported here only as a cross-check, never used.
"""
import json, math, statistics, sys
sys.path.insert(0, '/Users/simondavis/projects/video-audio/very_thoughtful_compression')
from vtc.model import Tier, AV1_FACTOR_HD, TIER_OVER_TOLERANCE

ANCHOR = 'GOOD'
d = json.load(open('grid.json'))

def fit(pts):
    xs = [math.log2(p[1]) for p in pts]; ys = [float(p[0]) for p in pts]
    n = len(xs); mx = sum(xs)/n; my = sum(ys)/n
    b = sum((x-mx)*(y-my) for x, y in zip(xs, ys)) / sum((x-mx)**2 for x in xs)
    a = my - b*mx
    ss_res = sum((y-(a+b*x))**2 for x, y in zip(xs, ys))
    ss_tot = sum((y-my)**2 for y in ys)
    return a, b, 1 - ss_res/ss_tot

fits = {}
print(f'{"source":11s} {"src bpp":>8s} {"CRF/2x":>7s} {"R2":>6s}   grid kbps range')
for name, v in sorted(d.items()):
    pts = [(int(k), g['bpp']) for k, g in v['grid'].items() if g['bpp'] > 0]
    if len(pts) < 5:
        print(f'{name:11s}  incomplete ({len(pts)} pts)'); continue
    a, b, r2 = fit(pts)
    s = v['src']
    src_bpp = s['kbps']*1000/(s['w']*s['h']*s['fps'])
    fits[name] = (a, b, src_bpp)
    ks = sorted(g['kbps'] for g in v['grid'].values())
    print(f'{name:11s} {src_bpp:8.4f} {-b:7.2f} {r2:6.3f}   {ks[0]:6.0f} - {ks[-1]:6.0f}')

if not fits:
    sys.exit('no complete sources yet')

slope = statistics.median(b for _, b, _ in fits.values())
print(f'\nmedian slope: {-slope:.2f} CRF per doubling of bitrate')

def live(tgt):
    return {n: a + b*math.log2(tgt) for n, (a, b, s) in fits.items()
            if s > tgt * TIER_OVER_TOLERANCE}

anchor_tier = Tier[ANCHOR]
anchor_tgt = anchor_tier.bpp * AV1_FACTOR_HD
anchor_live = live(anchor_tgt)
anchor_crf = statistics.median(anchor_live.values())
print(f'anchor {ANCHOR}: target bpp {anchor_tgt:.4f}, live on {len(anchor_live)}/{len(fits)}, '
      f'median CRF {anchor_crf:.2f}')

print(f'\n{"tier":11s} {"tgt bpp":>8s} {"ladder":>7s} {"round":>6s} | {"per-rung":>8s} {"live":>5s}')
band = {}
for tier in Tier:
    tgt = tier.bpp * AV1_FACTOR_HD
    ladder = anchor_crf + slope*(math.log2(tgt) - math.log2(anchor_tgt))
    band[tier.name] = round(ladder)
    lv = live(tgt)
    per = statistics.median(lv.values()) if lv else float('nan')
    print(f'{tier.name:11s} {tgt:8.4f} {ladder:7.2f} {round(ladder):6d} | {per:8.2f} {len(lv):5d}')

print('\nav1 column for _SHRINK_BANDS:')
for k, v in band.items():
    print(f'    "{k}": {v},')
json.dump(band, open('bands.json','w'), indent=1)
