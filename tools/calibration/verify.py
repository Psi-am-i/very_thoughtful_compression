"""Rate-control verification: for every clip x tier, compare the three candidate
shapes against the tier's AV1 target. Reports ratio-to-target; the convergence
gate is 1.10."""
import json, subprocess, os, sys, time
sys.path.insert(0, '/Users/simondavis/projects/very_thoughtful_compression')
from vtc.model import Tier, OutCodec, target_kbps

BANDS = json.load(open('bands.json'))     # {"OK": 24, ...}
d = json.load(open('grid.json'))
OUT = 'verify.json'
res = json.load(open(OUT)) if os.path.exists(OUT) else {}

def probe(p):
    out = subprocess.run(['ffprobe','-v','quiet','-print_format','json','-show_format',p],
                         capture_output=True,text=True).stdout
    f = json.loads(out)['format']
    return float(f['size'])*8/float(f['duration'])/1000

def enc(src, extra, out='tmpv.mp4'):
    r = subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-y','-i',src,
        '-map','0:v:0','-an','-sn','-dn','-c:v','libsvtav1','-preset','6',
        '-svtav1-params','tune=0','-pix_fmt','yuv420p10le',*extra,out],
        capture_output=True,text=True)
    return (None, r.stderr.strip()[-200:]) if r.returncode else (probe(out), '')

for name, v in sorted(d.items()):
    s = v['src']
    for tier in Tier:
        tgt = target_kbps(tier, s['w']*s['h'], s['fps'], OutCodec.AV1, src_kbps=s['kbps'])
        crf = BANDS[tier.name]
        key = f'{name}/{tier.name}'
        if key in res: continue
        row = {'target': tgt, 'crf': crf, 'src_kbps': round(s['kbps'])}
        for label, extra in (
            ('crf',    ['-crf', str(crf)]),
            ('capped', ['-crf', str(crf), '-maxrate', f'{tgt}k', '-bufsize', f'{tgt}k']),
            ('vbr',    ['-b:v', f'{tgt}k']),
        ):
            kbps, err = enc(f'clips/{name}.mp4', extra)
            row[label] = None if kbps is None else round(kbps, 1)
            row[label + '_ratio'] = None if kbps is None else round(kbps/tgt, 3)
            if err: row[label + '_err'] = err
        res[key] = row
        json.dump(res, open(OUT,'w'), indent=1)
        print(f'{key:22s} tgt {tgt:5d}k crf{crf:3d}  '
              f'crf {str(row["crf_ratio"]):>6s}  capped {str(row["capped_ratio"]):>6s}  '
              f'vbr {str(row["vbr_ratio"]):>6s}', flush=True)
print('DONE')
