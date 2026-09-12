"""Full 8 x 5 matrix with the shipping shape: capped CRF at the measured band,
-maxrate at target, mbr-overshoot-pct=10, no -bufsize (CBR-only, a no-op here)."""
import json, subprocess, os, sys
sys.path.insert(0, '/Users/simondavis/projects/video-audio/very_thoughtful_compression')
from vtc.model import Tier, OutCodec, target_kbps

BANDS = json.load(open('bands.json'))
grid = json.load(open('grid.json'))
OUT = 'final.json'
res = json.load(open(OUT)) if os.path.exists(OUT) else {}

def probe(p):
    f = json.loads(subprocess.run(['ffprobe','-v','quiet','-print_format','json',
        '-show_format',p],capture_output=True,text=True).stdout)['format']
    return float(f['size'])*8/float(f['duration'])/1000

for name in sorted(grid):
    s = grid[name]['src']
    for tier in Tier:
        key = f'{name}/{tier.name}'
        if key in res: continue
        tgt = target_kbps(tier, s['w']*s['h'], s['fps'], OutCodec.AV1, src_kbps=s['kbps'])
        crf = BANDS[tier.name]
        r = subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-y',
            '-i',f'clips/{name}.mp4','-map','0:v:0','-an','-sn','-dn',
            '-c:v','libsvtav1','-preset','6','-pix_fmt','yuv420p10le',
            '-crf',str(crf),'-maxrate',f'{tgt}k',
            '-svtav1-params','tune=0:mbr-overshoot-pct=10','tmpf.mp4'],
            capture_output=True,text=True)
        if r.returncode: print('FAIL',key,r.stderr[-200:]); sys.exit(1)
        k = probe('tmpf.mp4')
        res[key] = {'target': tgt, 'crf': crf, 'kbps': round(k,1), 'ratio': round(k/tgt,3)}
        json.dump(res, open(OUT,'w'), indent=1)
        print(f'{key:22s} tgt {tgt:5d}k crf{crf:3d}  {k:8.0f}k  ratio {k/tgt:.3f}', flush=True)
print('DONE')
