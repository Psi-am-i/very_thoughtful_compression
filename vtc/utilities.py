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
    for check in (drm_protected, container_truncated, chapters_past_duration):
        fault = check(config, path)
        if fault:
            rep.faults.append(fault)
    # Only decode when asked: it is the one check that reads the media itself.
    if decode_seconds != 0:
        fault = decode_errors(config, path, seconds=max(0, decode_seconds))
        if fault:
            rep.faults.append(fault)
    return rep


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


def remux_faststart(config: RunConfig, path: Path, container: str = "keep") -> FixResult:
    """Move the index to the front, optionally changing container on the way.

    The result is walked again before it replaces the source, so a pass that
    silently failed to move the index is caught rather than shipped. Nothing is
    replaced unless ffmpeg succeeded, the output is non-empty, AND the index
    actually moved.
    """
    res = FixResult(path=path, action="remux")
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
    # Three rungs, cheapest and least destructive first. A re-encode is the last
    # of them because it is the only one that spends quality.
    #
    # 1 · remux — seconds, lossless, and a container-level fault can present as a
    #     decode error, so it is worth trying even when the symptom looks deeper.
    attempt = remux_faststart(config, report.path)
    if attempt.ok and decode_errors(config, attempt.path, seconds=20) is None:
        attempt.note = "repaired by remux — no re-encode needed"
        return attempt
    target = attempt.path if attempt.ok else report.path
    # 2 · rebuild the sample index — still no re-encode: the original sample bytes
    #     are carried across and only the map to them is rewritten. Only H.264 CFR
    #     MP4s qualify, and the module says so rather than half-trying.
    rebuilt = rebuild_index(config, target)
    if rebuilt is not None:
        if rebuilt.ok:
            return rebuilt
        log.info("index rebuild did not take on %s: %s", target.name, rebuilt.error)
    # 3 · re-encode at the file's own bitrate.
    return repair_reencode(config, target)


def rebuild_index(config: RunConfig, path: Path) -> FixResult | None:
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
        if report.get("residual_errors"):
            res.error = (f"still {report['residual_errors']} decode error(s) after the "
                         f"rebuild — the damage is deeper than the index")
            return res
        res.after = out.stat().st_size
        dest = path.with_suffix(".mp4")
        out.replace(dest)
        if dest != path:
            path.unlink(missing_ok=True)
        res.path, res.ok = dest, True
        res.note = (f"index rebuilt losslessly · {report['frames_out']} of "
                    f"{report['frames_in']} frames kept, no re-encode")
        return res
    finally:
        if out.exists() and not res.ok:
            out.unlink(missing_ok=True)
