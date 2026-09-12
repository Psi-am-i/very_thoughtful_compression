"""Same A/B, but bucketed at 1s, 2s and 4s.

SVT-AV1's mini-GOP is 32 frames (~1.33s at 24fps) against x265's 8. A one-second
bucket therefore straddles AV1's hierarchy and cannot straddle H.265's, which would
inflate AV1's per-second variance for structural reasons rather than perceptual
ones. If the gap survives a bucket wider than the mini-GOP, it is real.
"""
import json, subprocess, statistics, sys
sys.path.insert(0, '/Users/simondavis/projects/video-audio/very_thoughtful_compression')
from vtc.model import Tier, OutCodec, target_kbps
from vtc.encode import crf_for_tier, AV1_MBR_OVERSHOOT_PCT
from vtc.result import Mode

grid = json.load(open('grid.json'))

def buckets(path, width):
    out = subprocess.run(['ffprobe','-v','quiet','-select_streams','v:0',
        '-show_entries','packet=pts_time,dts_time,size','-of','json',path],
        capture_output=True, text=True).stdout
    b = {}
    for p in json.loads(out)['packets']:
        t = p.get('pts_time') or p.get('dts_time')
        if t is None: continue
        b.setdefault(int(float(t)//width), 0)
        b[int(float(t)//width)] += int(p['size'])
    keys = sorted(b)[:-1]
    kb = [b[k]*8/1000/width for k in keys]
    m = statistics.mean(kb)
    return dict(cv=round(statistics.pstdev(kb)/m*100,1), floor=round(min(kb)/m,3), n=len(kb))

TIER = Tier.GOOD
res = {}
for name in sorted(grid):
    s = grid[name]['src']; px = s['w']*s['h']
    ta = target_kbps(TIER, px, s['fps'], OutCodec.AV1, src_kbps=s['kbps'])
    th = target_kbps(TIER, px, s['fps'], OutCodec.H265, src_kbps=s['kbps'])
    _, c265, cav1 = crf_for_tier(TIER, Mode.SHRINK)
    runs = {
      'av1-capped': ['-c:v','libsvtav1','-preset','6','-pix_fmt','yuv420p10le',
                     '-crf',str(cav1),'-maxrate',f'{ta}k',
                     '-svtav1-params',f'tune=0:mbr-overshoot-pct={AV1_MBR_OVERSHOOT_PCT}'],
      'h265':       ['-c:v','libx265','-preset','medium','-crf',str(c265),
                     '-maxrate',f'{th}k','-bufsize',f'{th}k','-tag:v','hvc1'],
    }
    for label, args in runs.items():
        f = f'c2_{name}_{label}.mp4'
        subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-y','-i',
                        f'clips/{name}.mp4','-map','0:v:0','-an','-sn','-dn',*args,f],
                       check=True, capture_output=True)
        res[f'{name}/{label}'] = {str(w): buckets(f, w) for w in (1, 2, 4)}
        json.dump(res, open('consist2.json','w'), indent=1)
    a, h = res[f'{name}/av1-capped'], res[f'{name}/h265']
    print(f'{name:11s} ' + '  '.join(
        f'{w}s av1 {a[str(w)]["cv"]:5.1f}%/{a[str(w)]["floor"]:.2f} h265 {h[str(w)]["cv"]:5.1f}%/{h[str(w)]["floor"]:.2f}'
        for w in (1, 2, 4)), flush=True)
print('DONE')
