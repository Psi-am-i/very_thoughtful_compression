"""Float-CRF bisection (0.25 steps) so a panel can actually be matched."""
import json, subprocess, sys, os
sys.path.insert(0,'/Users/simondavis/projects/very_thoughtful_compression')
from vtc.model import Tier, OutCodec, target_kbps
from vtc.encode import crf_for_tier
from vtc.result import Mode
TIER = Tier.INSANE
def probe(p):
    f=json.loads(subprocess.run(['ffprobe','-v','quiet','-print_format','json','-show_format',p],
        capture_output=True,text=True).stdout)['format']
    return float(f['size'])*8/float(f['duration'])/1000
def enc(src,out,crf,grain):
    a=['ffmpeg','-hide_banner','-loglevel','error','-y','-i',src,'-map','0:v:0',
       '-map_chapters','-1','-an','-sn','-dn','-c:v','libx265','-preset','medium','-crf',str(crf)]
    if grain: a+=['-tune','grain']
    subprocess.run(a+['-tag:v','hvc1',out],check=True,capture_output=True)
    return probe(out)
grid=json.load(open('gridvb.json'))
for name in sys.argv[1:]:
    s=grid[name]['src']; src=f'clips/{name}.mp4'
    tgt=target_kbps(TIER,s['w']*s['h'],s['fps'],OutCodec.H265,src_kbps=s['kbps'])
    cd=crf_for_tier(TIER,Mode.SHRINK)[1]
    ref=enc(src,f'gp_{name}_OFF.mp4',cd,False)
    print(f'{name}: default crf {cd} -> {ref:.0f} kbps  (tier target {tgt}k)',flush=True)
    lo,hi=float(cd),float(cd)+12; best=None
    for _ in range(9):
        mid=round(((lo+hi)/2)*4)/4
        got=enc(src,f'gp_{name}_TRY.mp4',mid,True); err=got/ref-1
        print(f'   grain crf {mid:5.2f} -> {got:7.0f} kbps ({err*100:+5.1f}%)',flush=True)
        if best is None or abs(err)<abs(best[1]):
            best=(mid,err); os.replace(f'gp_{name}_TRY.mp4',f'gp_{name}_ON.mp4')
        if abs(err)<=0.02: break
        if got>ref: lo=mid+0.25
        else: hi=mid-0.25
        if lo>hi: break
    crf_g,err=best
    if abs(err)>0.05:
        print(f'   ⛔ REFUSING {name}: best {err*100:+.1f}%'); continue
    print(f'   matched: grain crf {crf_g} at {err*100:+.1f}%',flush=True)
    json.dump({'name':name,'ref_kbps':round(ref),'crf_default':cd,'crf_grain':crf_g,
               'err_pct':round(err*100,1),'target':tgt,'src_kbps':round(s['kbps']),
               'tier':TIER.name,'dims':f"{s['w']}x{s['h']}",'fps':round(s['fps'],3)},
              open(f'gp_{name}.json','w'),indent=1)
print('DONE')
