"""Per-second bitrate consistency, straight off the container index (no decode).

Convergence is a claim about the AVERAGE. Consistency is a claim about the worst
second, which is what a viewer actually reports. VBR chases the average, so it can
starve a hard second to pay for an easy one and still land on target.
"""
import json, subprocess, statistics, sys

def per_second(path):
    out = subprocess.run(['ffprobe','-v','quiet','-select_streams','v:0',
        '-show_entries','packet=pts_time,dts_time,size','-of','json',path],
        capture_output=True, text=True).stdout
    pkts = json.loads(out)['packets']
    buckets = {}
    for p in pkts:
        t = p.get('pts_time') or p.get('dts_time')
        if t is None: continue
        buckets.setdefault(int(float(t)), 0)
        buckets[int(float(t))] += int(p['size'])
    if len(buckets) < 3: return None
    # drop the last (partial) second
    keys = sorted(buckets)[:-1]
    kbps = [buckets[k]*8/1000 for k in keys]
    m = statistics.mean(kbps)
    return dict(n=len(kbps), mean=round(m,1), cv=round(statistics.pstdev(kbps)/m*100,1),
                lo=round(min(kbps)), hi=round(max(kbps)),
                swing=round(max(kbps)/min(kbps),2))

for p in sys.argv[1:]:
    r = per_second(p)
    label = p.split('/')[-1]
    if not r: print(f'{label:34s} too short'); continue
    print(f'{label:34s} n={r["n"]:3d}  mean {r["mean"]:7.0f}k  CV {r["cv"]:5.1f}%  '
          f'lo/hi {r["lo"]:5d}/{r["hi"]:5d}  swing {r["swing"]:5.2f}x')
