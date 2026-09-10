"""Autocorrelation of the frame-size series — locates the hierarchy period directly."""
import json, subprocess, statistics, sys

def sizes(path):
    out = subprocess.run(['ffprobe','-v','quiet','-select_streams','v:0',
        '-show_entries','packet=pts_time,dts_time,size','-of','json',path],
        capture_output=True, text=True).stdout
    p = json.loads(out)['packets']
    p.sort(key=lambda x: float(x.get('pts_time') or x.get('dts_time') or 0))
    return [int(x['size']) for x in p]

def acf(xs, lag):
    n = len(xs); m = statistics.mean(xs)
    num = sum((xs[i]-m)*(xs[i+lag]-m) for i in range(n-lag))
    den = sum((x-m)**2 for x in xs)
    return num/den

for path in sys.argv[1:]:
    xs = sizes(path)
    r = [(round(acf(xs, L), 3), L) for L in range(2, 49)]
    top = sorted(r, reverse=True)[:4]
    print(f'{path.split("/")[-1]:28s} n={len(xs):4d}  ' +
          '  '.join(f'lag{L}:{v:+.3f}' for v, L in top))
