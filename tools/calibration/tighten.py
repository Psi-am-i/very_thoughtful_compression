"""Does --mbr-overshoot-pct close the capped-CRF leak?

Runs the cases that breached the 1.10 gate plus controls that did not, so a fix
that merely crushes easy content shows up as such.
"""
import json, subprocess, os, sys
sys.path.insert(0, '/Users/simondavis/projects/very_thoughtful_compression')
from vtc.model import Tier, OutCodec, target_kbps

BANDS = json.load(open('bands.json'))
grid = json.load(open('grid.json'))
CASES = [('RedDwarf', t) for t in ('OK','GOOD','EXCELLENT','STELLAR','INSANE')] + [
    ('PandP','INSANE'), ('Rehearsal','INSANE'), ('Rehearsal','OK'),
    ('XMen97','INSANE'), ('Shadows','STELLAR'), ('Moscow','OK'), ('ObiWan','OK')]
PCTS = [0, 10, 25]
OUT = 'tighten.json'
res = json.load(open(OUT)) if os.path.exists(OUT) else {}

def probe(p):
    f = json.loads(subprocess.run(['ffprobe','-v','quiet','-print_format','json',
        '-show_format',p],capture_output=True,text=True).stdout)['format']
    return float(f['size'])*8/float(f['duration'])/1000

for name, tname in CASES:
    s = grid[name]['src']
    tier = Tier[tname]
    tgt = target_kbps(tier, s['w']*s['h'], s['fps'], OutCodec.AV1, src_kbps=s['kbps'])
    crf = BANDS[tname]
    row = res.setdefault(f'{name}/{tname}', {'target': tgt, 'crf': crf})
    for pct in PCTS:
        if str(pct) in row: continue
        r = subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-y',
            '-i',f'clips/{name}.mp4','-map','0:v:0','-an','-sn','-dn',
            '-c:v','libsvtav1','-preset','6','-pix_fmt','yuv420p10le',
            '-crf',str(crf),'-maxrate',f'{tgt}k',
            '-svtav1-params',f'tune=0:mbr-overshoot-pct={pct}','tmpt.mp4'],
            capture_output=True,text=True)
        if r.returncode:
            print('FAIL', name, tname, pct, r.stderr[-200:]); sys.exit(1)
        k = probe('tmpt.mp4')
        row[str(pct)] = round(k/tgt, 3)
        json.dump(res, open(OUT,'w'), indent=1)
    print(f'{name}/{tname:10s} tgt {tgt:5d}k crf{crf:3d}  ' +
          '  '.join(f'pct{p}: {row[str(p)]:.3f}' for p in PCTS), flush=True)
print('DONE')
