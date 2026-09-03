"""MP4 sample-index repair — the deep, lossless second attempt.

Vendored from `mp4-repair/repair_mp4.py` (Simon's own tool) and adapted rather
than rewritten: the SPS/PPS/slice-header parsing and picture-order-count
derivation are bit-level H.264 syntax, and paraphrasing that from a description
would be a good way to produce something that looks right and silently mistimes
every B-frame.

WHAT IT FIXES. A sample index (`stsz`/`stco`) that no longer describes the bytes
in `mdat` — usually because a span was overwritten by a differently-muxed copy of
the same title. The payload is normally intact and self-describing; only the map
to it is wrong. So the fix parses the elementary stream and rebuilds the index,
with **no re-encode at all**: the original sample bytes are carried across
untouched. That is why it is worth trying before spending a generation of quality
on a repair encode.

WHY IT REFUSES SOME FILES, since the two refusals have quite different causes:

  * **HEVC and anything not H.264.** The damage *detection* (`chain_ok`) is
    container-level and would work for any length-prefixed codec. The RE-TIMING
    is what is H.264-specific: recovered frames have to be put back into display
    order, which means parsing the slice header for the picture order count, and
    HEVC's NAL header, SPS and slice syntax are a different language. It would
    need a second parser, not a flag.
  * **Variable frame rate.** The rebuild writes a fresh `stts`, and a recovered
    frame's duration is not knowable from the elementary stream. With a constant
    rate every sample is one tick and the table is exact; with a variable one we
    would be inventing timings and calling them a repair.

Also refused: `pic_order_cnt_type 1`, an unusual NAL length size, negative
composition offsets, and a trailing segment that is not self-contained. These are
hard guard-rails, deliberately not best-effort — a repair that half-works on a
file the user still has is worse than a clear refusal.

THE TRAP IT EXISTS TO AVOID: do not route recovered video through a raw Annex-B
stream and `-c copy`. Annex-B carries no timestamps, so ffmpeg sets pts = dts,
flattening `ctts`, and every B-frame then displays in decode order — it decodes
cleanly and plays *worse* than the file you started with.
"""

from __future__ import annotations

import json
import logging
import mmap
import os
import struct
import subprocess
import tempfile

from .winproc import NO_WINDOW, TEXT_UTF8

log_ = logging.getLogger("vtc.mp4index")


class IndexRepairUnsupported(Exception):
    """This file is outside what an index rebuild can honestly do.

    Distinct from a failure: the caller should fall back to another remedy (or to
    telling the user plainly), not retry.
    """


class IndexRepairFailed(Exception):
    """The rebuild was attempted and did not work."""


def die(m):                       # the original's fatal path, as an exception
    raise IndexRepairUnsupported(m)


def log(m):
    log_.info("%s", m)




VCL   = (1, 5)                      # coded slice / IDR slice
NALT  = {1, 5, 6, 7, 8, 9}          # types that may legitimately start a run
AACFR = 1024                        # samples per AAC frame


# ---------------------------------------------------------------- ffprobe ---

def probe_streams(path):
    out = subprocess.run(["ffprobe","-v","error","-show_streams","-of","json",path],
                         capture_output=True, text=True, **TEXT_UTF8, **NO_WINDOW)
    if out.returncode: die("ffprobe failed on " + path)
    return json.loads(out.stdout)["streams"]

def probe_packets(path, sel):
    out = subprocess.run(["ffprobe","-v","error","-select_streams",sel,"-show_packets",
                          "-show_entries","packet=pts,dts,size,pos,flags","-of","json",path],
                         capture_output=True, text=True, **TEXT_UTF8, **NO_WINDOW)
    if out.returncode: die("ffprobe failed reading packets")
    pk = json.loads(out.stdout)["packets"]
    return [p for p in pk if p.get("pos") is not None]

# -------------------------------------------------------------- bitstream ---

class BitReader:
    """Reads RBSP: strips H.264 emulation-prevention bytes first."""
    def __init__(self, data):
        o, i = bytearray(), 0
        while i < len(data):
            if i + 2 < len(data) and data[i] == 0 and data[i+1] == 0 and data[i+2] == 3:
                o += data[i:i+2]; i += 3
            else:
                o.append(data[i]); i += 1
        self.d, self.p = bytes(o), 0
    def u1(self):
        b = (self.d[self.p >> 3] >> (7 - (self.p & 7))) & 1; self.p += 1; return b
    def u(self, n):
        v = 0
        for _ in range(n): v = (v << 1) | self.u1()
        return v
    def ue(self):
        z = 0
        while self.u1() == 0: z += 1
        return (1 << z) - 1 + self.u(z) if z else 0
    def se(self):
        k = self.ue()
        return (k + 1) // 2 if k & 1 else -(k // 2)

def parse_sps(nal):
    b = BitReader(nal[1:]); s = {}
    prof = b.u(8); b.u(8); b.u(8); b.ue()
    if prof in (100,110,122,244,44,83,86,118,128,138,139,134,135):
        cf = b.ue()
        if cf == 3: b.u1()
        b.ue(); b.ue(); b.u1()
        if b.u1():
            for i in range(8 if cf != 3 else 12):
                if b.u1():
                    last = nxt = 8
                    for _ in range(16 if i < 6 else 64):
                        if nxt: nxt = (last + b.se() + 256) % 256
                        last = nxt if nxt else last
    s['log2_max_frame_num'] = b.ue() + 4
    s['poc_type'] = b.ue()
    if s['poc_type'] == 0:
        s['log2_max_poc_lsb'] = b.ue() + 4
    elif s['poc_type'] == 1:
        b.u1(); b.se(); b.se()
        for _ in range(b.ue()): b.se()
    b.ue(); b.u1(); b.ue(); b.ue()
    s['frame_mbs_only'] = b.u1()
    return s

def parse_pps(nal):
    b = BitReader(nal[1:]); b.ue(); b.ue(); b.u1()
    return {'bottom_field_poc': b.u1()}

def parse_slice(nal, sps, pps):
    t, ref = nal[0] & 0x1f, (nal[0] >> 5) & 3
    b = BitReader(nal[1:])
    b.ue(); b.ue(); b.ue()
    b.u(sps['log2_max_frame_num'])
    field = 0
    if not sps['frame_mbs_only']:
        if b.u1(): field = 1; b.u1()
    if t == 5: b.ue()
    lsb = None
    if sps['poc_type'] == 0:
        lsb = b.u(sps['log2_max_poc_lsb'])
        if pps['bottom_field_poc'] and not field: b.se()
    return t, ref, lsb

def compute_ranks(units, sps, pps):
    """Display-order rank of each frame, from picture order count."""
    if sps['poc_type'] == 2:
        return list(range(len(units)))
    MAX = 1 << sps['log2_max_poc_lsb']
    prevMsb = prevLsb = 0
    pocs, idr = [], []
    for i, nal in enumerate(units):
        t, ref, lsb = parse_slice(nal, sps, pps)
        if t == 5:
            prevMsb = prevLsb = msb = 0; idr.append(i)
        elif lsb < prevLsb and (prevLsb - lsb) >= MAX // 2: msb = prevMsb + MAX
        elif lsb > prevLsb and (lsb - prevLsb) > MAX // 2:  msb = prevMsb - MAX
        else: msb = prevMsb
        pocs.append(msb + lsb)
        if ref: prevMsb, prevLsb = msb, lsb
    bounds = ([0] if not idr or idr[0] != 0 else []) + idr + [len(units)]
    ranks, base = [0]*len(units), 0
    for a, z in zip(bounds, bounds[1:]):
        for r, j in enumerate(sorted(range(a, z), key=lambda j: pocs[j])):
            ranks[j] = base + r
        base += z - a
    return ranks

# ------------------------------------------------------------- mp4 writing ---

def box(t, p): return struct.pack('>I', 8 + len(p)) + t + p
def fbox(t, ver, flags, p): return box(t, bytes([ver]) + struct.pack('>I', flags)[1:] + p)
MATRIX = struct.pack('>9i', 0x10000,0,0, 0,0x10000,0, 0,0,0x40000000)

def build_moov(samples, offsets, avcc, W, H, tick, mts, delay, mvts=1000):
    N = len(samples); dur = N * tick; mv = round(dur / mts * mvts)
    stts = fbox(b'stts',0,0, struct.pack('>I',1) + struct.pack('>II', N, tick))
    runs = []
    for s in samples:
        if runs and runs[-1][1] == s['ctts']: runs[-1][0] += 1
        else: runs.append([1, s['ctts']])
    ctts = fbox(b'ctts',0,0, struct.pack('>I',len(runs)) + b''.join(struct.pack('>II',c,o) for c,o in runs))
    kf   = [i+1 for i,s in enumerate(samples) if s['key']]
    stss = fbox(b'stss',0,0, struct.pack('>I',len(kf)) + b''.join(struct.pack('>I',k) for k in kf))
    stsc = fbox(b'stsc',0,0, struct.pack('>I',1) + struct.pack('>III',1,1,1))
    stsz = fbox(b'stsz',0,0, struct.pack('>II',0,N) + b''.join(struct.pack('>I',s['size']) for s in samples))
    co64 = fbox(b'co64',0,0, struct.pack('>I',N) + b''.join(struct.pack('>Q',o) for o in offsets))
    avc1 = box(b'avc1', b'\x00'*6 + struct.pack('>H',1) + b'\x00'*16 +
               struct.pack('>HH',W,H) + struct.pack('>II',0x00480000,0x00480000) +
               b'\x00'*4 + struct.pack('>H',1) + b'\x00'*32 + struct.pack('>H',0x18) +
               b'\xff\xff' + box(b'avcC', avcc))
    stbl = box(b'stbl', fbox(b'stsd',0,0,struct.pack('>I',1)+avc1) + stts + ctts + stss + stsc + stsz + co64)
    minf = box(b'minf', fbox(b'vmhd',0,1,struct.pack('>HHHH',0,0,0,0)) +
               box(b'dinf', box(b'dref', struct.pack('>II',0,1) + fbox(b'url ',0,1,b''))) + stbl)
    mdia = box(b'mdia', fbox(b'mdhd',0,0, struct.pack('>IIII',0,0,mts,dur)+struct.pack('>HH',0x55C4,0)) +
               fbox(b'hdlr',0,0, struct.pack('>I',0)+b'vide'+b'\x00'*12+b'VideoHandler\x00') + minf)
    tkhd = fbox(b'tkhd',0,3, struct.pack('>IIIII',0,0,1,0,mv) + b'\x00'*8 +
                struct.pack('>hhhh',0,0,0,0) + MATRIX + struct.pack('>II',W<<16,H<<16))
    elst = fbox(b'elst',0,0, struct.pack('>I',1) + struct.pack('>IiI', mv, delay*tick, 0x10000))
    mvhd = fbox(b'mvhd',0,0, struct.pack('>IIII',0,0,mvts,mv) + struct.pack('>IHH',0x10000,0x0100,0) +
                b'\x00'*8 + MATRIX + b'\x00'*24 + struct.pack('>I',2))
    return box(b'moov', mvhd + box(b'trak', tkhd + box(b'edts', elst) + mdia))

# ---------------------------------------------------------------- analysis ---

def chain_ok(mm, pos, size, nls=4):
    """True if the sample is a clean chain of length-prefixed NAL units."""
    off = 0
    while off + nls <= size:
        L = int.from_bytes(mm[pos+off:pos+off+nls], 'big')
        if not (1 <= L <= 2_000_000) or off + nls + L > size: return False
        off += nls + L
    return off == size

def scan_runs(mm, lo, hi):
    """Find maximal chains of valid length-prefixed NALs inside a damaged span."""
    def chain(p, maxn=None):
        n = 0
        while p + 4 <= hi:
            L = int.from_bytes(mm[p:p+4], 'big')
            if not (1 <= L <= 2_000_000) or p + 4 + L > hi: break
            b = mm[p+4]
            if (b & 0x80) or (b & 0x1f) not in NALT: break
            n += 1; p += 4 + L
            if maxn and n >= maxn: break
        return n, p
    runs, pos = [], lo
    while pos < hi - 8:
        n, _ = chain(pos, maxn=4)
        if n >= 4:
            _, end = chain(pos); runs.append((pos, end)); pos = end
        else:
            pos += 1
    return runs

def access_units(mm, runs):
    """Group recovered NALs into access units (one coded picture each)."""
    nals = []
    for s, e in runs:
        p = s
        while p < e:
            L = int.from_bytes(mm[p:p+4], 'big'); nals.append((p+4, L)); p += 4 + L
    aus, cur, has = [], [], False
    for pos, L in nals:
        if has: aus.append(cur); cur = []; has = False
        cur.append((pos, L))
        if (mm[pos] & 0x1f) in VCL: has = True
    if cur: aus.append(cur)
    return [au for au in aus if any((mm[p] & 0x1f) in VCL for p, _ in au)]

def complete_block(ranks):
    """A segment is self-contained if its display ranks are exactly 0..n-1."""
    return len(ranks) > 0 and max(ranks) == len(ranks) - 1 and len(set(ranks)) == len(ranks)

def trim_to_block(pkts, tick, drop_first=0):
    """Trim frames off the end until the segment's display order is self-contained."""
    n = len(pkts) - drop_first
    while n > 0:
        seg = pkts[:n]; m = min(p['pts'] for p in seg)
        if complete_block([(p['pts'] - m)//tick for p in seg]): return n
        n -= 1
    return 0

# -------------------------------------------------------------------- main ---



# ── the API: diagnose, then optionally repair ────────────────────────────────
class _Loaded:
    """The shared prologue of both operations: probe, guard-rails, damage map.

    Diagnosis and repair must agree about what is wrong, so they read the file
    exactly once, the same way.
    """

    def __init__(self, path, ffmpeg="ffmpeg", ffprobe="ffprobe"):
        self.path, self.ffmpeg, self.ffprobe = str(path), ffmpeg, ffprobe
        if not os.path.exists(self.path):
            die("no such file: " + self.path)
        streams = probe_streams(self.path)
        self.vs = next((s for s in streams if s["codec_type"] == "video"), None)
        self.aus = next((s for s in streams if s["codec_type"] == "audio"), None)
        if not self.vs:
            die("no video stream")
        if self.vs["codec_name"] != "h264":
            die(f"only H.264 can be re-indexed (this is {self.vs['codec_name']}) — the "
                f"re-timing step parses H.264 slice headers, which HEVC does not share")
        self.W, self.H = int(self.vs["width"]), int(self.vs["height"])
        self.mts = int(self.vs["time_base"].split("/")[1])
        self.vp = probe_packets(self.path, "v:0")
        for p in self.vp:
            p["pts"], p["dts"] = int(p["pts"]), int(p["dts"])
            p["size"], p["pos"] = int(p["size"]), int(p["pos"])
        self.vp.sort(key=lambda p: p["pos"])
        if len(self.vp) < 2:
            die("too few video packets to analyse")
        d = sorted(self.vp[i + 1]["dts"] - self.vp[i]["dts"] for i in range(len(self.vp) - 1))
        self.tick = d[len(d) // 2]
        if d[0] != d[-1]:
            die("variable frame rate is not supported — a rebuilt index would have to "
                "invent each recovered frame's duration")
        self.f = open(self.path, "rb")
        self.mm = mmap.mmap(self.f.fileno(), 0, prot=mmap.PROT_READ)
        # Find avcC inside the moov box, wherever moov happens to live. The
        # original looked only in the first megabyte, which works on a faststart
        # file and fails on every other — and "index at the back" is precisely the
        # shape of file that needs repairing, so that limit bit exactly the wrong
        # population.
        i, region = self._find_avcc()
        if i < 0:
            die("no avcC configuration record found")
        self.avcc = region[i + 4: i - 4 + int.from_bytes(region[i - 4:i], "big")]
        if (self.avcc[4] & 3) + 1 != 4:
            die("unsupported NAL length size")
        sl = int.from_bytes(self.avcc[6:8], "big")
        spsn = self.avcc[8:8 + sl]
        pl = int.from_bytes(self.avcc[8 + sl + 1:8 + sl + 3], "big")
        ppsn = self.avcc[8 + sl + 3:8 + sl + 3 + pl]
        self.sps, self.pps = parse_sps(spsn), parse_pps(ppsn)
        if self.sps["poc_type"] == 1:
            die("pic_order_cnt_type 1 is not supported")
        # The damage map: which samples are not clean length-prefix chains.
        bad = [not chain_ok(self.mm, p["pos"], p["size"]) for p in self.vp]
        self.nbad = sum(bad)
        self.regions = []
        i = 0
        while i < len(self.vp):
            if bad[i]:
                j = i
                while j + 1 < len(self.vp) and bad[j + 1]:
                    j += 1
                lo = self.vp[i]["pos"]
                hi = (self.vp[j + 1]["pos"] if j + 1 < len(self.vp)
                      else self.vp[j]["pos"] + self.vp[j]["size"])
                self.regions.append((i, j, lo, hi))
                i = j + 1
            else:
                i += 1

    def _find_avcc(self):
        """(offset, bytes) of the avcC record, searched inside moov if we can find
        it and across the whole file if we cannot."""
        size = len(self.mm)
        pos = 0
        while pos + 8 <= size:
            head = self.mm[pos:pos + 8]
            if len(head) < 8:
                break
            box_size, typ = struct.unpack(">I4s", head)
            skip = 8
            if box_size == 1:
                box_size = int.from_bytes(self.mm[pos + 8:pos + 16], "big")
                skip = 16
            elif box_size == 0:
                box_size = size - pos
            if box_size < 8:
                break
            if typ == b"moov":
                region = self.mm[pos:pos + box_size]
                return region.find(b"avcC"), region
            pos += box_size
        whole = self.mm[:]
        return whole.find(b"avcC"), whole

    def close(self):
        try:
            self.mm.close()
        finally:
            self.f.close()


def diagnose(path, ffmpeg="ffmpeg", ffprobe="ffprobe") -> dict:
    """Localise index damage without writing anything.

    Raises IndexRepairUnsupported when this file is outside what a rebuild can
    honestly do — which the caller should treat as "try something else", not as
    a failure of the file.
    """
    L = _Loaded(path, ffmpeg, ffprobe)
    try:
        spans = []
        for i0, i1, lo, hi in L.regions:
            spans.append({
                "frames": [i0, i1],
                "from_s": L.vp[i0]["pts"] / L.mts,
                "to_s": L.vp[i1]["pts"] / L.mts,
                "bytes": hi - lo,
            })
        return {"samples": len(L.vp), "damaged": L.nbad,
                "percent": (100.0 * L.nbad / len(L.vp)) if L.vp else 0.0,
                "regions": spans}
    finally:
        L.close()


def repair(path, out_path, ffmpeg="ffmpeg", ffprobe="ffprobe",
           keep_audio_gap: bool = False) -> dict:
    """Rebuild the sample index around the ORIGINAL sample bytes. No re-encode.

    Writes to `out_path` and never touches the input, so a failed attempt costs
    disk and time but nothing else.
    """
    L = _Loaded(path, ffmpeg, ffprobe)
    tmp = None
    try:
        if L.nbad == 0:
            raise IndexRepairUnsupported("the index already matches the media — nothing to rebuild")
        if os.path.abspath(str(out_path)) == os.path.abspath(L.path):
            die("output would overwrite the input")
        mm, vp, tick, mts = L.mm, L.vp, L.tick, L.mts
        recovered = []
        for i0, i1, lo, hi in L.regions:
            runs = scan_runs(mm, lo, hi)
            recovered.append(access_units(mm, runs))

        samples, base, prev = [], 0, 0
        resume_frame = []
        for r, (i0, i1, lo, hi) in enumerate(L.regions):
            good = vp[prev:i0]
            keep = trim_to_block(good, tick, drop_first=1)
            seg = good[:keep]
            m = min(p["pts"] for p in seg) if seg else 0
            for p in seg:
                samples.append({"src": (p["pos"], p["size"]), "size": p["size"],
                                "rank": base + (p["pts"] - m) // tick, "key": "K" in p["flags"]})
            base += len(seg)
            au = recovered[r]
            vcl = []
            for u in au:
                v = next((x for x in u if (mm[x[0]] & 0x1F) in VCL), None)
                vcl.append(bytes(mm[v[0]:v[0] + v[1]]))
            rr = compute_ranks(vcl, L.sps, L.pps)
            for j, u in enumerate(au):
                samples.append({"nals": u, "size": sum(4 + Ln for _, Ln in u),
                                "rank": base + rr[j],
                                "key": any((mm[p] & 0x1F) == 5 for p, _ in u)})
            base += len(au)
            prev = i1 + 1
            resume_frame.append(len(samples))
        tail = vp[prev:]
        if tail:
            m = min(p["pts"] for p in tail)
            ranks = [(p["pts"] - m) // tick for p in tail]
            if not complete_block(ranks):
                die("trailing segment is not self-contained")
            for p, r in zip(tail, ranks):
                samples.append({"src": (p["pos"], p["size"]), "size": p["size"],
                                "rank": base + r, "key": "K" in p["flags"]})
            base += len(tail)

        delay = max(1, max(i - s["rank"] for i, s in enumerate(samples)))
        for i, s in enumerate(samples):
            s["ctts"] = (s["rank"] + delay - i) * tick
        if any(s["ctts"] < 0 for s in samples):
            die("negative composition offset")

        tmp = tempfile.mkdtemp(prefix="vtc-mp4index-")
        vpath = os.path.join(tmp, "video.mp4")
        with open(vpath, "wb") as o:
            o.write(box(b"ftyp", b"isom" + struct.pack(">I", 512) + b"isomavc1mp41"))
            ms = o.tell()
            o.write(struct.pack(">I", 1) + b"mdat" + struct.pack(">Q", 0))
            offs = []
            for s in samples:
                offs.append(o.tell())
                if "src" in s:
                    o.write(mm[s["src"][0]:s["src"][0] + s["src"][1]])
                else:
                    for p, Ln in s["nals"]:
                        o.write(struct.pack(">I", Ln))
                        o.write(mm[p:p + Ln])
            me = o.tell()
            o.write(build_moov(samples, offs, L.avcc, L.W, L.H, tick, mts, delay))
            o.seek(ms + 8)
            o.write(struct.pack(">Q", me - ms))

        apath = _rebuild_audio(L, tmp, resume_frame, keep_audio_gap)
        cmd = [L.ffmpeg, "-v", "error", "-i", vpath]
        if apath:
            cmd += ["-i", apath, "-map", "0:v", "-map", "1:a", "-bsf:a", "aac_adtstoasc"]
        cmd += ["-c", "copy", "-movflags", "+faststart", str(out_path), "-y"]
        r = subprocess.run(cmd, capture_output=True, text=True, stdin=subprocess.DEVNULL,
                           **TEXT_UTF8, **NO_WINDOW)
        if r.returncode != 0 or not os.path.exists(out_path):
            raise IndexRepairFailed((r.stderr or "mux failed").strip().splitlines()[-1][:160])

        # A clean decode is the only claim worth making about a repair.
        v = subprocess.run([L.ffmpeg, "-v", "error", "-i", str(out_path), "-f", "null", "-"],
                           capture_output=True, text=True, stdin=subprocess.DEVNULL,
                           **TEXT_UTF8, **NO_WINDOW)
        errs = [ln for ln in (v.stderr or "").splitlines() if "h264 @" in ln or "aac @" in ln]
        return {"frames_in": len(vp), "frames_out": len(samples),
                "damaged": L.nbad, "residual_errors": len(errs)}
    finally:
        L.close()
        if tmp:
            import shutil as _sh
            _sh.rmtree(tmp, ignore_errors=True)


def _rebuild_audio(L, tmp, resume_frame, keep_audio_gap):
    """AAC has no sync words, so audio inside a damaged span cannot be recovered.

    Exactly enough silence is substituted for the next real frame to land where
    the picture resumes; otherwise the sound drifts against the picture for the
    rest of the file, which is a worse outcome than a moment of silence.
    """
    if not (L.aus and L.aus["codec_name"] == "aac"):
        return None
    rate = int(L.aus["sample_rate"])
    ap_ = probe_packets(L.path, "a:0")
    for p in ap_:
        p["size"], p["pos"] = int(p["size"]), int(p["pos"])
    ap_.sort(key=lambda p: p["pos"])
    raw = os.path.join(tmp, "a.aac")
    subprocess.run([L.ffmpeg, "-v", "error", "-i", L.path, "-vn", "-c:a", "copy",
                    "-f", "adts", raw, "-y"], check=True, stdin=subprocess.DEVNULL,
                   capture_output=True, **TEXT_UTF8, **NO_WINDOW)

    def adts_frames(path):
        d = open(path, "rb").read()
        o, i = [], 0
        while i + 7 <= len(d):
            if d[i] != 0xFF or (d[i + 1] & 0xF0) != 0xF0:
                break
            Ln = ((d[i + 3] & 3) << 11) | (d[i + 4] << 3) | ((d[i + 5] >> 5) & 7)
            if Ln < 7 or i + Ln > len(d):
                break
            o.append(d[i:i + Ln])
            i += Ln
        return o

    A = adts_frames(raw)
    if len(A) != len(ap_):
        die("audio frame count mismatch")
    lost = [any(lo <= p["pos"] < hi for _, _, lo, hi in L.regions) for p in ap_]
    nlost = sum(lost)
    sil = os.path.join(tmp, "s.aac")
    br = max(96, int(L.aus.get("bit_rate") or 128000) // 1000)
    subprocess.run([L.ffmpeg, "-v", "error", "-f", "lavfi",
                    "-i", "anullsrc=r=%d:cl=stereo" % rate,
                    "-t", "%.3f" % ((nlost + 64) * AACFR / rate + 2),
                    "-c:a", "aac", "-b:a", "%dk" % br, "-f", "adts", sil, "-y"],
                   check=True, stdin=subprocess.DEVNULL, capture_output=True,
                   **TEXT_UTF8, **NO_WINDOW)
    SIL = adts_frames(sil)[2:]
    new, i, g = [], 0, 0
    while i < len(A):
        if not lost[i]:
            new.append(A[i])
            i += 1
            continue
        j = i
        while j < len(A) and lost[j]:
            j += 1
        if not keep_audio_gap and g < len(resume_frame):
            t = resume_frame[g] * L.tick / L.mts
            new.extend(SIL[:max(0, round(t * rate / AACFR) - len(new))])
        i = j
        g += 1
    apath = os.path.join(tmp, "audio.aac")
    open(apath, "wb").write(b"".join(new))
    return apath
