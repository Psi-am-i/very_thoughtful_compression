"""2-up split screen per docs/measuring-quality.md §5:
crop at 1:1 (never scale), 960x1080 per side, lossless montage, randomised,
key withheld — and the KEY records the FULL argument list (the shadowTools lesson).
"""
import json, subprocess, sys, random, datetime, os

OUT = os.environ.get('VTC_PANEL_DIR', '/Volumes/Scout-3MacBackup/VTC-TESTING/VTC-compare')
CROP_X = 480          # centre 960-wide band of a 1920 frame, at 1:1
random.seed()

for name in sys.argv[1:]:
    meta = json.load(open(f'gp_{name}.json'))
    off, on = f'gp_{name}_OFF.mp4', f'gp_{name}_ON.mp4'
    sides = [('default', off, meta['crf_default']), ('tune=grain', on, meta['crf_grain'])]
    random.shuffle(sides)
    (lname, lfile, lcrf), (rname, rfile, rcrf) = sides
    stamp = datetime.datetime.now().strftime('%m%d-%H%M%S')
    dest = f'{OUT}/SPLIT-GRAIN-{name}-{stamp}.mp4'
    fc = (f'[0:v]crop=960:1080:{CROP_X}:0,setsar=1[l];'
          f'[1:v]crop=960:1080:{CROP_X}:0,setsar=1[r];'
          f'[l][r]hstack=inputs=2[s];'
          f'[s]drawbox=x=958:y=0:w=4:h=1080:color=gray@0.9:t=fill[v]')
    subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-y',
        '-i',lfile,'-i',rfile,'-filter_complex',fc,'-map','[v]',
        '-c:v','libx264','-qp','0','-preset','veryfast','-pix_fmt','yuv420p',
        '-map_chapters','-1','-an','-sn','-dn',dest], check=True)
    key = (f"{name} — x265 tune=grain ON vs OFF at MATCHED BITRATE, {meta['tier'] if 'tier' in meta else 'INSANE'} tier\n"
           f"  Source {meta['dims']} @ {meta['fps']}fps, {meta['src_kbps']} kbps\n"
           f"  Crop 960x1080 at x={CROP_X}, 1:1 (no scaling). Lossless montage (-qp 0).\n\n"
           f"  LEFT   {lname:12s} crf {lcrf}\n"
           f"  RIGHT  {rname:12s} crf {rcrf}\n\n"
           f"  Both encodes landed at {meta['ref_kbps']} kbps ({meta['err_pct']:+.1f}% apart).\n"
           f"  Tier target was {meta['target']} kbps.\n\n"
           f"  FULL ARGUMENTS (record these — a winning config was once lost for want of them):\n"
           f"    default     ffmpeg -i SRC -map 0:v:0 -map_chapters -1 -an -sn -dn \\\n"
           f"                  -c:v libx265 -preset medium -crf {meta['crf_default']} -tag:v hvc1\n"
           f"    tune=grain  ffmpeg -i SRC -map 0:v:0 -map_chapters -1 -an -sn -dn \\\n"
           f"                  -c:v libx265 -preset medium -crf {meta['crf_grain']} -tune grain -tag:v hvc1\n"
           f"    x265 4.2+1-e444744.  tune=grain sets psy-rd 4.00, sao off, rskip off, AQ+cu-tree off.\n")
    open(f'{OUT}/KEY-SPLIT-GRAIN-{name}-{stamp}.txt','w').write(key)
    print(f'built {dest}\n{key}')
