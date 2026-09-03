"""Utilities — the fixes that are not a re-encode.

Two tools, both of which look at a whole library and change nothing until asked:

  1. **Faststart** — move the index to the front so a file starts playing before
     it has finished downloading. A stream copy: seconds, not hours.
  2. **File health** — find the faults that break players (chapter markers past
     the real end, decode errors, structurally damaged containers), repair by
     remux where a remux can do it, and only re-encode as a last resort.

The detection here is deliberately *structural* rather than heuristic, because
the failure mode of a heuristic in this particular job is unusually bad: a file
wrongly reported as needing a fix gets rewritten on every single run, forever.
See `docs/library-file-fixes.md` for the provenance of each check and the
measured evidence behind the choices.
"""

from __future__ import annotations

import json
import re
import logging
import struct
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from . import mp4index
from .config import RunConfig
from .ffprobe import probe
from .winproc import NO_WINDOW, TEXT_UTF8

# Containers whose index position we can actually determine.
log = logging.getLogger("vtc.utilities")

_MP4_EXTS = {".mp4", ".m4v", ".mov", ".m4a"}
_MKV_EXTS = {".mkv", ".mka", ".webm"}

# Video codecs that cannot be stream-copied into MP4 (they force a transcode).
# From everything_2_faststart_mp4.sh's needs_transcode_video().
MP4_INCOMPATIBLE_VIDEO = {
    "wmv1", "wmv2", "wmv3", "vc1", "msmpeg4v1", "msmpeg4v2", "msmpeg4v3", "msmpeg4",
}

# Decode-error signatures that mean the bitstream itself is damaged, from
# fix_stash_video_errors.sh. Matched against ffmpeg's stderr.
_NAL_PATTERNS = re.compile(
    r"Invalid NAL|missing picture in access unit|Error splitting the input into NAL|"
    r"no frame!|corrupt|Invalid data found when processing input|"
    r"non-existing PPS|decode_slice_header error",
    re.I)


# ── faststart: is the index at the front? ─────────────────────────────────────
def _read_mp4_top_level(fh, file_size: int):
    """Yield (offset, size, type) for each top-level MP4 box.

    Reads only each box's 8-byte header and skips by its declared size, so the
    cost is a handful of small reads whatever the file weighs.
    """
    pos = 0
    while pos + 8 <= file_size:
        fh.seek(pos)
        head = fh.read(8)
        if len(head) < 8:
            return
        size, typ = struct.unpack(">I4s", head)
        hs = 8
        if size == 1:                      # 64-bit largesize follows the header
            ext = fh.read(8)
            if len(ext) < 8:
                return
            size = struct.unpack(">Q", ext)[0]
            hs = 16
        elif size == 0:                    # this box runs to end of file
            size = file_size - pos
        # A declared size smaller than the header it just claimed is nonsense.
        # Compared against the ACTUAL header length, not a flat 8: in the 64-bit
        # form a size of 12 would pass a `< 8` test and then walk backwards into
        # the middle of the box we are standing on.
        if size < hs:
            return
        yield pos, size, typ.decode("latin-1")
        pos += size


def is_faststart(path: Path) -> bool | None:
    """True if this file's index precedes its media data, None if unknowable.

    MP4: whichever of `moov` / `mdat` a top-level walk reaches first.
    MKV: whether `Cues` appears before the first `Cluster` inside the Segment.

    ⚠️ NOT a byte-scan of the first few MB. A faststart file's `moov` grows with
    the frame count and is routinely 4-8 MB, which pushes `mdat` outside the
    window and reports the file as needing a pass it does not need — measured at
    103 of 1,416 faststart files (7.3%) on a real library, worst on long
    episodic TV. Those files then get rewritten on every run, forever.
    """
    ext = path.suffix.lower()
    try:
        size = path.stat().st_size
        with path.open("rb") as fh:
            if ext in _MP4_EXTS:
                for _off, _sz, typ in _read_mp4_top_level(fh, size):
                    if typ == "moov":
                        return True
                    if typ == "mdat":
                        return False
                return None                # neither box found: not an MP4 we understand
            if ext in _MKV_EXTS:
                return _mkv_cues_first(fh, size)
    except (OSError, struct.error, ValueError):
        return None
    return None


def _ebml_num(fh, keep_marker: bool):
    """Read one EBML variable-length integer. Returns (value, byte_length, unknown).

    `unknown` is the EBML "size not known yet" form — every data bit set, which
    live and streamed muxers write. It has to be recognised at whatever width it
    was encoded (a 1-byte 0xFF as much as an 8-byte one): treating it as a real
    length walks a few bytes into the file and concludes nothing is there.
    """
    first = fh.read(1)
    if not first:
        return None, 0, False
    b = first[0]
    if b == 0:
        return None, 0, False
    length = 1
    mask = 0x80
    while not (b & mask):
        mask >>= 1
        length += 1
        if length > 8:
            return None, 0, False
    value = b if keep_marker else (b & (mask - 1))
    rest = fh.read(length - 1)
    if len(rest) < length - 1:
        return None, 0, False
    for byte in rest:
        value = (value << 8) | byte
    unknown = (not keep_marker) and value == (1 << (7 * length)) - 1
    return value, length, unknown


# Matroska element IDs, read WITH their marker bits (the form they are written in).
_EBML_SEGMENT = 0x18538067
_EBML_CUES = 0x1C53BB6B
_EBML_CLUSTER = 0x1F43B675


def _mkv_cues_first(fh, file_size: int) -> bool | None:
    """True if the Cues index precedes the first Cluster.

    MKV has no `moov`/`mdat`; the equivalent question is whether the seek index
    was written ahead of the media. Same reason for walking rather than
    scanning: guessing wrong here means rewriting the file on every run.
    """
    fh.seek(0)
    # Find the Segment, then walk its immediate children.
    pos = 0
    while pos < file_size:
        fh.seek(pos)
        eid, idlen, _ = _ebml_num(fh, keep_marker=True)
        if eid is None:
            return None
        size, szlen, unknown = _ebml_num(fh, keep_marker=False)
        if size is None:
            return None
        body = pos + idlen + szlen
        if eid == _EBML_SEGMENT:
            # A Segment of unknown length runs to the end of the file, which is
            # normal for anything muxed live rather than written in one pass.
            end = file_size if unknown else min(file_size, body + size)
            child = body
            while child < end:
                fh.seek(child)
                cid, cidlen, _ = _ebml_num(fh, keep_marker=True)
                if cid is None:
                    return None
                csize, cszlen, cunknown = _ebml_num(fh, keep_marker=False)
                if csize is None:
                    return None
                if cid == _EBML_CUES:
                    return True
                if cid == _EBML_CLUSTER:
                    return False
                if cunknown:
                    return None            # cannot walk past a child of unknown length
                child += cidlen + cszlen + csize
            # Walked the whole Segment without meeting either: there is no index
            # at all. The remux would create one, but say "unknown" rather than
            # claim we found an index sitting in the wrong place.
            return None
        if unknown:
            return None
        pos = body + size
    return None


# ── health checks ─────────────────────────────────────────────────────────────
@dataclass
class Fault:
    kind: str            # "chapters" | "nal" | "container" | "unreadable" | "codec"
    detail: str          # what to show the user
    fix: str             # "remux" | "reencode" | "none"


@dataclass
class FileReport:
    path: Path
    size: int = 0
    faststart: bool | None = None
    faults: list[Fault] = field(default_factory=list)
    # Set when converting this file to MP4 is impossible (its codec cannot be
    # stream-copied there). Not a fault — the file is fine as it is — but it is
    # why one of the container options will refuse it.
    blocks_mp4: str = ""
    # Decode errors across the WHOLE file, measured once damage was found. 0 means
    # "not measured", not "none" — the flag above says whether anything is wrong.
    decode_errors_before: int = 0

    @property
    def needs(self) -> bool:
        return bool(self.faults)

    @property
    def beyond_repair(self) -> bool:
        """Is this file past saving — i.e. worth offering to throw away?

        A deliberately narrow "yes": a half-downloaded file and one whose bytes
        cannot be read are gone, and the only useful thing left to do is get rid
        of them and fetch again.

        DRM is explicitly NOT included, and that distinction is the whole point of
        having this rather than just checking `fix == "none"`. An encrypted file
        is perfectly intact — it plays in whatever app it belongs to; we simply
        cannot open it. Offering to bin it because *we* had no luck would be
        destroying something that works.
        """
        kinds = {f.kind for f in self.faults}
        return bool(kinds & {"container", "unreadable"}) and "drm" not in kinds

    @property
    def fix(self) -> str:
        """The strongest remedy any fault here calls for."""
        if any(f.fix == "reencode" for f in self.faults):
            return "reencode"
        if any(f.fix == "remux" for f in self.faults):
            return "remux"
        return "none"


def chapters_past_duration(config: RunConfig, path: Path) -> Fault | None:
    """Chapter markers that run past the real end of the file.

    Players handle this badly — a chapter list that overshoots can send a seek
    beyond the media and stall or fail. No reference implementation existed for
    this in any of the sibling tools; see docs/library-file-fixes.md §1f.
    """
    try:
        r = subprocess.run(
            [config.ffprobe, "-v", "error", "-show_chapters", "-show_format",
             "-of", "json", str(path)],
            capture_output=True, text=True, stdin=subprocess.DEVNULL,
            timeout=60, **TEXT_UTF8, **NO_WINDOW)
        data = json.loads(r.stdout or "{}")
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    chapters = data.get("chapters") or []
    if not chapters:
        return None
    try:
        duration = float((data.get("format") or {}).get("duration") or 0.0)
    except (TypeError, ValueError):
        duration = 0.0
    if duration <= 0:
        return None
    # A marker a hair past the last frame is rounding, not damage. Only flag a
    # real overshoot, or the tool would "fix" most of a healthy library.
    slack = 1.0
    over = []
    for ch in chapters:
        try:
            end = float(ch.get("end_time") or 0.0)
            start = float(ch.get("start_time") or 0.0)
        except (TypeError, ValueError):
            continue
        if max(start, end) > duration + slack:
            over.append(max(start, end))
    if not over:
        return None
    worst = max(over) - duration
    n = len(over)
    return Fault(
        kind="chapters",
        detail=(f"{n} chapter marker{'s' if n != 1 else ''} past the real end"
                f" · runs {worst:.0f}s over"),
        fix="remux")


def container_truncated(config: RunConfig, path: Path) -> Fault | None:
    """An MP4 whose `mdat` does not end where the file does.

    That is structural damage — usually an interrupted download — and it is NOT
    the same as a desynchronised index: no remux repairs it, and the honest
    answer is to fetch the file again.
    """
    if path.suffix.lower() not in _MP4_EXTS:
        return None
    try:
        size = path.stat().st_size
        with path.open("rb") as fh:
            boxes = list(_read_mp4_top_level(fh, size))
    except (OSError, struct.error, ValueError):
        return None
    if not boxes:
        return None
    last_off, last_size, last_typ = boxes[-1]
    end = last_off + last_size
    if end > size + 1:
        short = end - size
        return Fault(kind="container",
                     detail=f"container is {_human(short)} shorter than it claims "
                            f"· truncated {last_typ}",
                     fix="none")
    return None


def decode_errors(config: RunConfig, path: Path, seconds: int = 0) -> Fault | None:
    """Decode the stream and see whether ffmpeg complains.

    `seconds` bounds the work: a whole-library census that decodes every file end
    to end is an overnight job in itself, so the scan reads a bounded window by
    default. That is a real limitation and it is stated rather than hidden — a
    bounded read can only find damage inside the window it read.
    """
    args = [config.ffmpeg, "-v", "error", "-xerror" if False else "-nostdin"]
    args += ["-i", str(path)]
    if seconds > 0:
        args += ["-t", str(seconds)]
    args += ["-f", "null", "-"]
    try:
        r = subprocess.run(args, capture_output=True, text=True,
                           stdin=subprocess.DEVNULL, timeout=1800,
                           **TEXT_UTF8, **NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return None
    err = (r.stderr or "").strip()
    if not err and r.returncode == 0:
        return None
    if _NAL_PATTERNS.search(err):
        lines = [ln for ln in err.splitlines() if _NAL_PATTERNS.search(ln)]
        # Prefer a VIDEO complaint: a damaged file usually upsets both decoders,
        # and leading with the audio one describes the symptom, not the fault.
        pick = next((ln for ln in lines if "vist" in ln or "video" in ln.lower()
                     or "h264" in ln.lower() or "hevc" in ln.lower()), lines[0] if lines else "")
        return Fault(kind="nal",
                     detail="decode errors in the stream · " + _tidy(pick),
                     fix="reencode")
    if r.returncode != 0:
        return Fault(kind="nal",
                     detail=f"ffmpeg could not decode this file cleanly (exit {r.returncode})",
                     fix="reencode")
    return None


# Boxes that only appear in an encrypted MP4. `encv`/`enca` are the sample entries
# for encrypted video/audio, `sinf` the protection scheme, `pssh` the DRM system
# header, `senc` the per-sample initialisation vectors.
_DRM_BOXES = (b"pssh", b"sinf", b"senc", b"encv", b"enca")


def drm_protected(config: RunConfig, path: Path) -> Fault | None:
    """Is this file encrypted?

    Worth its own check because DRM presents exactly like corruption — ffmpeg
    cannot decode it and reports errors — and every remedy this tool has is
    useless against it. Telling someone "this is encrypted" ends the matter;
    letting them run a repair, then a re-encode, wastes an hour to arrive at the
    same place. Cheap: a header read, no decoding.
    """
    if path.suffix.lower() not in _MP4_EXTS:
        return None
    try:
        size = path.stat().st_size
        with path.open("rb") as fh:
            for off, box_size, typ in _read_mp4_top_level(fh, size):
                if typ != "moov":
                    continue
                # Read the moov and look for protection boxes anywhere inside it.
                fh.seek(off)
                blob = fh.read(min(box_size, 32 * 1024 * 1024))
                hit = next((b for b in _DRM_BOXES if b in blob), None)
                if hit:
                    return Fault(
                        kind="drm",
                        detail="encrypted (DRM) — no repair or re-encode can read this",
                        fix="none")
                return None
    except (OSError, struct.error, ValueError):
        return None
    return None


def mp4_incompatible_codec(config: RunConfig, path: Path) -> Fault | None:
    """A video codec that cannot be copied into MP4 — only relevant when the user
    has asked to change container, which is why it is reported and not fixed."""
    info = probe(path, config.ffprobe)
    if info.ok and (info.vcodec or "").lower() in MP4_INCOMPATIBLE_VIDEO:
        return Fault(kind="codec",
                     detail=f"{info.vcodec} cannot be copied into MP4 · "
                            f"changing container would mean re-encoding",
                     fix="none")
    return None


def _tidy(line: str) -> str:
    """ffmpeg's own bookkeeping out of a message meant for a person.

    Its errors are prefixed with things like `[aist#0:1/aac @ 0x7eac18480]`,
    which is a component address and tells the reader nothing at all.
    """
    line = re.sub(r"\[[^\]]*@ 0x[0-9a-f]+\]\s*", "", line)
    line = re.sub(r"^\[[^\]]+\]\s*", "", line.strip())
    return line.strip()[:90] or "the decoder reported an error"


def _human(n: int) -> str:
    for unit, div in (("GB", 1e9), ("MB", 1e6), ("KB", 1e3)):
        if n >= div:
            return f"{n / div:.1f} {unit}"
    return f"{n} B"


def index_desync(config: RunConfig, path: Path) -> Fault | None:
    """Does the sample index still describe the bytes in `mdat`?

    Cheap enough to be a default check and it catches what a bounded decode
    cannot. Measured on a real 1.67 GB, 11.6-minute file with damage 519 seconds
    in: this found 2,445 bad samples in 0.4s, while a 30-second decode census
    took 1.2s and reported the file healthy — because the damage was nowhere near
    the window it read. It validates each sample as a length-prefix chain from
    the 4-byte headers alone and never touches the payload, so the cost is
    bounded by the sample COUNT rather than the file size.
    """
    if path.suffix.lower() not in _MP4_EXTS:
        return None
    try:
        d = mp4index.diagnose(path, ffmpeg=config.ffmpeg, ffprobe=config.ffprobe)
    except mp4index.IndexRepairUnsupported:
        return None            # not a file this check understands; say nothing
    except Exception:          # noqa: BLE001 — a health check must never be the thing that breaks
        return None
    if not d["damaged"]:
        return None
    span = ""
    if d["regions"]:
        r = d["regions"][0]
        span = (f" · {_mmss(r['from_s'])}–{_mmss(r['to_s'])}"
                + (f" and {len(d['regions']) - 1} more" if len(d["regions"]) > 1 else ""))
    return Fault(
        kind="nal",
        detail=(f"sample index does not match the media · {d['damaged']} of "
                f"{d['samples']} frames ({d['percent']:.1f}%){span}"),
        fix="reencode")        # the ladder tries the lossless rebuild first


def _mmss(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    return f"{m}:{s:02d}"


def scan_health(config: RunConfig, path: Path, decode_seconds: int = 0) -> FileReport:
    """Every fault we can find in one file, cheapest check first."""
    rep = FileReport(path=path)
    try:
        rep.size = path.stat().st_size
    except OSError:
        pass
    info = probe(path, config.ffprobe)
    if not info.ok or not info.vcodec:
        # Even an unprobeable file is worth a structural look: "the download
        # stopped 300 MB short" tells someone what to do about it, where
        # "unreadable" only tells them something is wrong.
        # DRM first: an encrypted file looks exactly like a corrupt one, and
        # "encrypted" is an answer where "unreadable" only invites an hour of
        # futile repair attempts.
        rep.faults.append(drm_protected(config, path)
                          or container_truncated(config, path)
                          or Fault(kind="unreadable",
                                   detail=_tidy(info.error or "") or "no video stream found",
                                   fix="none"))
        return rep
    # DRM and truncation are TERMINAL: they explain everything else and nothing
    # here can act on them, so they stop the scan rather than being listed
    # alongside their own symptoms. A truncated file's index necessarily points at
    # bytes that are not there — reporting that as separate "index damage" would
    # offer a repair for a file that cannot be repaired.
    for check in (drm_protected, container_truncated):
        fault = check(config, path)
        if fault:
            rep.faults.append(fault)
            return rep
    for check in (index_desync, chapters_past_duration):
        fault = check(config, path)
        if fault:
            rep.faults.append(fault)
    # Once a cheap check has found something, it pays to look at the whole file.
    # A sampled census is a scanning compromise; on a file already known to be
    # damaged it is the wrong economy — the point now is to know the full extent,
    # and to have a real "before" number to judge any repair against.
    if rep.needs and any(f.kind == "nal" for f in rep.faults):
        full = decode_errors(config, path, seconds=0)
        if full:
            rep.decode_errors_before = _error_count(config, path)
    elif decode_seconds != 0:
        fault = decode_errors(config, path, seconds=max(0, decode_seconds))
        if fault:
            rep.faults.append(fault)
            # Same escalation: a bounded window found damage, so measure it properly.
            rep.decode_errors_before = _error_count(config, path)
    return rep


def _error_count(config: RunConfig, path: Path) -> int:
    """How many decode errors the WHOLE file produces.

    The honest "before" figure. A repair is only worth accepting if it improves
    on something measured, and a bounded sample cannot supply that number.
    """
    try:
        r = subprocess.run(
            [config.ffmpeg, "-v", "error", "-nostdin", "-i", str(path), "-f", "null", "-"],
            capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=7200,
            **TEXT_UTF8, **NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return 0
    return len([ln for ln in (r.stderr or "").splitlines()
                if "h264 @" in ln or "hevc @" in ln or "aac @" in ln])


def scan_faststart(config: RunConfig, path: Path) -> FileReport:
    rep = FileReport(path=path)
    try:
        rep.size = path.stat().st_size
    except OSError:
        pass
    rep.faststart = is_faststart(path)
    if rep.faststart is False:
        rep.faults.append(Fault(kind="faststart",
                                detail="index sits after the media · needs the pass",
                                fix="remux"))
    # Informational, and only if they ask to convert: a file whose codec cannot go
    # into MP4 will refuse the "To MP4" option, and it is better to know that from
    # the scan than from a failure part-way through a batch.
    if path.suffix.lower() not in _MP4_EXTS:
        clash = mp4_incompatible_codec(config, path)
        if clash:
            rep.blocks_mp4 = clash.detail
    elif rep.faststart is False:
        # Only worth asking for a file we would otherwise offer to remux: say up
        # front that this one must be repaired first, rather than letting the fix
        # refuse it halfway through a batch.
        desync = index_desync(config, path)
        if desync:
            rep.faults.append(Fault(
                kind="nal",
                detail=desync.detail + " — repair this before remuxing, or the "
                                       "recoverable frames are lost",
                fix="reencode"))
    return rep


# ── the fixes ─────────────────────────────────────────────────────────────────
@dataclass
class FixResult:
    path: Path
    ok: bool = False
    action: str = ""          # what was done: "remux" | "reencode" | ""
    before: int = 0
    after: int = 0
    error: str = ""
    note: str = ""


def _temp_beside(path: Path, tag: str, ext: str) -> Path:
    """A hidden temp beside the source, so the rename is atomic (same filesystem)
    and no half-written file is ever left where a scan would pick it up."""
    return path.with_name(f".{path.stem}.{tag}.part{ext}")


def _run(config: RunConfig, args: list[str], timeout: int = 3600):
    try:
        return subprocess.run([config.ffmpeg, "-v", "error", "-nostdin", "-y", *args],
                              capture_output=True, text=True, stdin=subprocess.DEVNULL,
                              timeout=timeout, **TEXT_UTF8, **NO_WINDOW)
    except (OSError, subprocess.SubprocessError) as e:
        class _Fail:
            returncode = 1
            stderr = str(e)[:200]
        return _Fail()


def remux_faststart(config: RunConfig, path: Path, container: str = "keep",
                    check_index: bool = True) -> FixResult:
    """Move the index to the front, optionally changing container on the way.

    The result is walked again before it replaces the source, so a pass that
    silently failed to move the index is caught rather than shipped. Nothing is
    replaced unless ffmpeg succeeded, the output is non-empty, AND the index
    actually moved.

    ⚠️ IT REFUSES A FILE WHOSE INDEX IS DESYNCHRONISED, and that guard is the
    whole reason this function is not as harmless as it sounds. A remux copies
    samples out ACCORDING TO THE INDEX; when the index is wrong, the bytes it
    points at are the wrong bytes, and the recoverable payload sitting in the
    damaged span is scrambled on the way out. Measured on a real file: rebuilding
    the index recovered 20,915 of 20,927 frames with no errors, and doing the same
    after "just a remux" recovered 18,493 with 21 — a lossless-sounding step had
    destroyed 2,400 intact frames, permanently.

    This matters far beyond the repair ladder: running the Faststart tool across a
    library would otherwise quietly do that to every damaged file it met. The check
    costs about 0.4s on a 1.67 GB file because it reads only sample headers.
    `check_index=False` is for callers that have just established the index is
    sound and do not want to pay for the answer twice.
    """
    res = FixResult(path=path, action="remux")
    if check_index:
        desync = index_desync(config, path)
        if desync:
            res.error = ("the sample index does not match the media — remuxing would "
                         "destroy what a repair could still recover. Run the file-health "
                         "repair on it first")
            return res
    try:
        res.before = path.stat().st_size
    except OSError:
        pass
    src_ext = path.suffix.lower()
    want = {"keep": src_ext, "mp4": ".mp4", "mkv": ".mkv"}.get(container, src_ext)
    to_mkv = want in _MKV_EXTS
    # Some codecs simply cannot be stream-copied into MP4. Catch that HERE rather
    # than letting ffmpeg fail thirty seconds in with a message about muxers: this
    # tool's whole promise is "a stream copy, seconds not hours", and the honest
    # answer is that this particular file cannot have it.
    if not to_mkv:
        clash = mp4_incompatible_codec(config, path)
        if clash:
            res.error = clash.detail
            return res
    if to_mkv:
        args = ["-i", str(path), "-map", "0", "-c", "copy", "-cues_to_front", "1"]
    else:
        # Every map is optional so an audio-only or subtitle-less file doesn't fail.
        args = ["-i", str(path), "-map", "0:v?", "-map", "0:a?", "-map", "0:s?",
                "-c", "copy", "-movflags", "+faststart", "-f", "mp4"]
    tmp = _temp_beside(path, "faststart", want)
    r = _run(config, [*args, str(tmp)])
    try:
        if r.returncode != 0 or not tmp.exists() or tmp.stat().st_size == 0:
            res.error = (getattr(r, "stderr", "") or "remux failed").strip().splitlines()[-1][:160]
            return res
        # Did it actually work? A remux that "succeeded" without moving the index
        # would otherwise be repeated on every future run.
        if is_faststart(tmp) is False:
            res.error = "the remux ran but the index is still at the back"
            return res
        res.after = tmp.stat().st_size
        dest = path.with_suffix(want) if want != src_ext else path
        if dest != path and dest.exists():
            res.error = f"{dest.name} already exists"
            return res
        tmp.replace(dest)
        if dest != path:
            path.unlink(missing_ok=True)
            res.note = f"{src_ext.lstrip('.')} → {want.lstrip('.')}"
        res.path, res.ok = dest, True
        return res
    finally:
        if tmp.exists() and not res.ok:
            tmp.unlink(missing_ok=True)


def _source_kbps(config: RunConfig, path: Path) -> int:
    """The file's own bitrate, which is the cap a repair re-encode aims at.

    A repair is not a shrink: the point is to rewrite a damaged bitstream at the
    quality it already has, so the target is the source's own rate rather than a
    tier. Falls back to the video stream, then to a cap, exactly as the sibling
    tool did.
    """
    info = probe(path, config.ffprobe)
    for candidate in (info.container_bit_rate, info.bit_rate):
        if candidate and candidate > 0:
            return max(500, int(candidate / 1000))
    return 8000


def repair_reencode(config: RunConfig, path: Path) -> FixResult:
    """Last resort: re-encode at the file's OWN bitrate to rebuild the bitstream.

    Capped-CRF at the source's rate — the same shape as the quality path, but the
    ceiling is what the file already had rather than a tier target, so a repair
    never doubles as a shrink. Audio is copied if it can be, re-encoded if not.
    """
    res = FixResult(path=path, action="reencode")
    try:
        res.before = path.stat().st_size
    except OSError:
        pass
    kbps = _source_kbps(config, path)
    base = ["-i", str(path), "-c:v", "libx264", "-crf", "18",
            "-maxrate", f"{kbps}k", "-bufsize", f"{kbps * 2}k",
            "-movflags", "+faststart"]
    tmp = _temp_beside(path, "repair", ".mp4")
    attempts = ([*base, "-c:a", "copy"], [*base, "-c:a", "aac", "-b:a", "384k"])
    try:
        for n, args in enumerate(attempts):
            r = _run(config, [*args, str(tmp)])
            if r.returncode == 0 and tmp.exists() and tmp.stat().st_size > 0:
                res.after = tmp.stat().st_size
                dest = path.with_suffix(".mp4")
                if dest != path and dest.exists() and dest != tmp:
                    res.error = f"{dest.name} already exists"
                    return res
                tmp.replace(dest)
                if dest != path:
                    path.unlink(missing_ok=True)
                res.path, res.ok = dest, True
                res.note = f"re-encoded at {kbps} kbps" + (" · audio to AAC" if n else "")
                return res
            tmp.unlink(missing_ok=True)
        res.error = "could not be re-encoded — the file is probably damaged beyond repair"
        return res
    finally:
        if tmp.exists() and not res.ok:
            tmp.unlink(missing_ok=True)


def fix_health(config: RunConfig, report: FileReport, allow_reencode: bool = False) -> FixResult:
    """Apply the least invasive repair that can address this file's faults.

    The ladder is deliberate: a remux is lossless and quick, a re-encode spends a
    generation of quality, and some damage is not repairable at all — for which
    the honest answer is to say so rather than to burn an hour proving it.
    """
    if report.fix == "none":
        kinds = ", ".join(sorted({f.kind for f in report.faults})) or "nothing"
        return FixResult(path=report.path, ok=False,
                         error=f"nothing here can be repaired automatically ({kinds})")
    if report.fix == "remux":
        return remux_faststart(config, report.path)
    if not allow_reencode:
        return FixResult(path=report.path, ok=False, action="",
                         error="needs a re-encode, which was not enabled")
    # The rung is chosen by the FAULT, not by cost alone — because trying the
    # cheap one first can destroy the evidence the better one needs.
    #
    # ⚠️ A REMUX MUST NOT PRECEDE AN INDEX REBUILD. The rebuild works by scanning
    # the raw bytes of the damaged span for valid NAL chains. A remux rewrites
    # those samples according to the BROKEN index, so the recoverable payload is
    # scrambled on the way out. Measured on a real file: rebuilding the pristine
    # file recovered 20,915 of 20,927 frames with 0 residual errors; rebuilding
    # the same file after a remux recovered 18,493 with 21 — the remux had thrown
    # away 2,400 frames that were sitting there intact.
    index_fault = any(f.kind == "nal" for f in report.faults)
    if index_fault:
        rebuilt = rebuild_index(config, report.path,
                                before_errors=report.decode_errors_before)
        if rebuilt is not None and rebuilt.ok:
            return rebuilt
        if rebuilt is not None:
            log.info("index rebuild did not take on %s: %s", report.path.name, rebuilt.error)

    # A remux is the right first move for everything else: it is seconds and
    # lossless, and a container-level fault often presents as a decode error.
    # The scan has already looked at the index, so don't pay for it twice: on the
    # index-fault path the rebuild above has just run, and on the other paths the
    # scan found nothing wrong with it.
    attempt = remux_faststart(config, report.path, check_index=index_fault)
    if attempt.ok and _still_broken(config, attempt.path) is None:
        attempt.note = "repaired by remux — no re-encode needed"
        return attempt
    target = attempt.path if attempt.ok else report.path
    if not index_fault:
        # Not tried yet, and still worth a go before spending an hour and a
        # generation of quality.
        rebuilt = rebuild_index(config, target, before_errors=report.decode_errors_before)
        if rebuilt is not None and rebuilt.ok:
            return rebuilt
    # Last: re-encode at the file's own bitrate.
    return repair_reencode(config, target)


def _still_broken(config: RunConfig, path: Path) -> Fault | None:
    """Did that repair actually work? Structurally, not by sampling.

    Checking with a bounded decode is how a repair comes to report success it has
    not earned: a remux copies a broken sample index straight through, and a
    twenty-second window at the head of the file sees a clean stream and says so.
    Caught on a real 1.67 GB file whose damage begins at 8:39 — the remux
    "succeeded", the check agreed, and 2,445 damaged frames were still there.
    The index check reads the whole file's headers and cannot be fooled that way.
    """
    return index_desync(config, path) or decode_errors(config, path, seconds=20)


def rebuild_index(config: RunConfig, path: Path, before_errors: int = 0) -> FixResult | None:
    """Rebuild a desynchronised sample index. None if this file cannot qualify.

    Lossless: the payload is intact and self-describing, so only the container's
    map to it is rewritten. Worth attempting before a repair re-encode for exactly
    that reason — it costs minutes and no quality, where the re-encode costs an
    hour and a generation.
    """
    if path.suffix.lower() not in _MP4_EXTS:
        return None
    res = FixResult(path=path, action="reindex")
    try:
        res.before = path.stat().st_size
    except OSError:
        pass
    out = _temp_beside(path, "reindex", ".mp4")
    try:
        report = mp4index.repair(path, out, ffmpeg=config.ffmpeg, ffprobe=config.ffprobe)
    except mp4index.IndexRepairUnsupported as e:
        out.unlink(missing_ok=True)
        return None                      # not a failure — this file was never eligible
    except Exception as e:               # noqa: BLE001
        out.unlink(missing_ok=True)
        res.error = str(e)[:160]
        return res
    try:
        if not out.exists() or out.stat().st_size == 0:
            res.error = "the rebuild produced nothing"
            return res
        # The index it just wrote must actually describe the media — a rebuild that
        # decodes cleanly but left a desynchronised table is a false success one
        # rung further down.
        if index_desync(config, out):
            res.error = "the rebuilt index still does not match the media"
            return res
        residual = report.get("residual_errors") or 0
        if residual and before_errors and residual >= before_errors:
            res.error = (f"the rebuild left {residual} decode error(s), no better than "
                         f"the {before_errors} it started with")
            return res
        if residual and not before_errors:
            res.error = f"the rebuild left {residual} decode error(s)"
            return res
        res.after = out.stat().st_size
        dest = path.with_suffix(".mp4")
        out.replace(dest)
        if dest != path:
            path.unlink(missing_ok=True)
        res.path, res.ok = dest, True
        # A REBUILD BEATS A RE-ENCODE EVEN WHEN IT IS IMPERFECT, and that is not a
        # compromise — a re-encode does not repair damaged frames. It encodes
        # whatever the decoder managed to produce, errors included, at the cost of
        # an hour and a generation of quality. So a rebuild that takes a file from
        # thousands of errors to a handful is strictly the better outcome, and the
        # residual is reported rather than used as grounds to spend that hour.
        lost = report["frames_in"] - report["frames_out"]
        res.note = (f"index rebuilt losslessly · {report['frames_out']} of "
                    f"{report['frames_in']} frames kept"
                    + (f", {lost} lost to the damaged span" if lost else "")
                    + ", no re-encode"
                    + (f" · {before_errors} decode errors → {residual}"
                       if before_errors else ""))
        return res
    finally:
        if out.exists() and not res.ok:
            out.unlink(missing_ok=True)
