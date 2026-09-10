"""CRF grid for libsvtav1, matching how the x264/x265 bands were derived:
9-point CRF grid on 30s real-footage clips, measured video-only bitrate."""
import json, subprocess, os, sys, time

CLIPS = sorted(f for f in os.listdir('clips') if f.endswith('.mp4'))
CRFS = [12, 17, 22, 27, 32, 37, 42, 47, 52]
OUT = 'grid.json'
res = json.load(open(OUT)) if os.path.exists(OUT) else {}

def probe(p):
    d = json.loads(subprocess.run(['ffprobe','-v','quiet','-print_format','json',
        '-show_streams','-show_format',p],capture_output=True,text=True).stdout)
    v = next(s for s in d['streams'] if s['codec_type']=='video')
    n,dd = v['avg_frame_rate'].split('/')
    fps = float(n)/float(dd)
    dur = float(d['format']['duration'])
    size = float(d['format']['size'])
    return dict(w=v['width'], h=v['height'], fps=fps, dur=dur,
                kbps=size*8/dur/1000, codec=v['codec_name'])

for c in CLIPS:
    name = c[:-4]
    src = f'clips/{c}'
    info = probe(src)
    res.setdefault(name, {'src': info, 'grid': {}})
    for crf in CRFS:
        if str(crf) in res[name]['grid']:
            continue
        t0 = time.time()
        r = subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-y','-i',src,
            '-map','0:v:0','-an','-sn','-dn','-c:v','libsvtav1','-crf',str(crf),
            '-preset','6','-svtav1-params','tune=0','-pix_fmt','yuv420p10le','tmp.mp4'],
            capture_output=True,text=True)
        if r.returncode:
            print('FAIL', name, crf, r.stderr[-300:]); sys.exit(1)
        o = probe('tmp.mp4')
        bpp = o['kbps']*1000/(info['w']*info['h']*info['fps'])
        res[name]['grid'][str(crf)] = dict(kbps=round(o['kbps'],1), bpp=round(bpp,5),
                                           secs=round(time.time()-t0,1))
        json.dump(res, open(OUT,'w'), indent=1)
        print(f'{name:10s} crf {crf:2d}  {o["kbps"]:8.0f} kbps  bpp {bpp:.5f}  {time.time()-t0:5.1f}s', flush=True)
print('DONE')
