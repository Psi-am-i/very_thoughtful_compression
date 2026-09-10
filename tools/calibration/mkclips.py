import json, subprocess, os
T='/Volumes/RAID/TV/'
SRC = {
 'Shadows':   'What We Do in the Shadows (2019) {tvdb-358211}/Season 01/What We Do in the Shadows (2019) - S01E01 - Pilot [WEBDL-1080p][EAC3 5.1][h264]-playWEB.mp4',
 'PandP':     'Pride and Prejudice (1995)/Season 01/Pride and Prejudice (1995) - S01E01 - Episode 1 [HDTV-1080p][AC3 2.0][x264].mp4',
 'Moscow':    'A Gentleman in Moscow/Season 01/A Gentleman in Moscow (2024) - S01E01 - A Master of Circumstance [WEBDL-1080p][EAC3 5.1][h264]-FLUX copy.mp4',
 'Curb':      'Curb Your Enthusiasm/Season 01/Curb Your Enthusiasm (2000) - S01E01 - The Pants Tent [WEBDL-1080p][AC3 2.0][h264]-ELEVATE.mp4',
 'ObiWan':    'Obi-Wan Kenobi/Season 01/Obi-Wan Kenobi (2022) - S01E01 - Part I [WEBDL-1080p][EAC3 Atmos 5.1][h264]-KOGi.mp4',
 'XMen97':    "X-Men '97 (2024) {tvdb-412432}/Season 01/X-Men '97 (2024) - S01E01 - To Me My X-Men [WEBDL-1080p][EAC3 5.1][h264]-TURG.mkv",
 'Rehearsal': 'The Rehearsal/Season 01/The Rehearsal (2022) - S01E01 - Orange Juice No Pulp [WEBDL-1080p][EAC3 5.1][h264]-NTb.mp4',
 'RedDwarf':  'Red Dwarf/Season 03/Red Dwarf (1988) - S03E01 - Backwards [Bluray-1080p][DTS 2.0][x264]-latency.mp4',
}
for name, rel in SRC.items():
    p = T + rel
    if not os.path.exists(p):
        print('MISSING', name); continue
    d = json.loads(subprocess.run(['ffprobe','-v','quiet','-print_format','json','-show_format',p],
                                  capture_output=True,text=True).stdout)
    dur = float(d['format']['duration'])
    ss = round(dur*0.40)
    out = f'clips/{name}.mp4'
    r = subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-y','-ss',str(ss),'-t','30',
                        '-i',p,'-map','0:v:0','-map_chapters','-1','-dn','-an','-sn','-c','copy',out],
                       capture_output=True,text=True)
    if r.returncode: print('FAIL',name,r.stderr[:200]); continue
    print(f'{name:10s} ss={ss:5d}s -> {os.path.getsize(out)/1e6:7.1f} MB')
