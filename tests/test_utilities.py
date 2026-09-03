"""Utilities — faststart and file health.

The failure mode to fear here is not missing a fault; it is inventing one. These
tools look at a whole library, so a file wrongly reported as needing a fix gets
rewritten on every single run, forever. That is exactly what the old
byte-scanning faststart check did — 103 of 1,416 healthy files on a real library
— so detection is structural, and the tests lean hardest on the negatives.

Run:  python3 -m pytest tests/test_utilities.py
"""

from __future__ import annotations

import os
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vtc import utilities as U  # noqa: E402
from vtc.config import RunConfig  # noqa: E402
from vtc.ffprobe import probe  # noqa: E402

_HAVE_FF = shutil.which("ffmpeg") and shutil.which("ffprobe")


def _cfg(d):
    return RunConfig(src=Path(d))


def _clip(path: Path, secs=4, audio=True, size="320x240"):
    args = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
            "-i", f"testsrc2=size={size}:rate=25"]
    if audio:
        args += ["-f", "lavfi", "-i", "sine=frequency=440", "-shortest"]
    args += ["-t", str(secs), "-c:v", "libx264", "-pix_fmt", "yuv420p"]
    if audio:
        args += ["-c:a", "aac"]
    subprocess.run([*args, str(path)], check=True, stdin=subprocess.DEVNULL)


def _box(typ: bytes, payload: bytes) -> bytes:
    return struct.pack(">I4s", 8 + len(payload), typ) + payload


# ── faststart detection: the atom walk, and why it is not a byte-scan ────────
def test_a_big_moov_at_the_front_is_still_faststart():
    """THE regression this whole approach exists for.

    A faststart file's moov grows with the frame count and is routinely 4-8 MB,
    which pushes mdat outside a 4 MB read window. The old byte-scan then reported
    a perfectly good file as needing the pass — measured at 103 of 1,416 files —
    and it would be rewritten on every run for the rest of its life.
    """
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "big.mp4"
        p.write_bytes(_box(b"ftyp", b"isom")
                      + _box(b"moov", b"\0" * (5 * 1024 * 1024))
                      + _box(b"mdat", b"\0" * 1000))
        assert U.is_faststart(p) is True

        head = p.read_bytes()[:4 * 1024 * 1024]
        assert head.find(b"mdat") == -1, "the premise: mdat is outside the window"


def test_the_walk_handles_the_64_bit_box_form():
    """A large mdat uses size==1 with the real length in the following 8 bytes.
    Misreading that walks to a nonsense offset and reports garbage."""
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "large.mp4"
        big = struct.pack(">I4sQ", 1, b"mdat", 16 + 100) + b"\0" * 100
        p.write_bytes(_box(b"ftyp", b"isom") + big + _box(b"moov", b"\0" * 8))
        assert U.is_faststart(p) is False          # mdat genuinely comes first


def test_a_zero_size_box_runs_to_the_end():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "zero.mp4"
        p.write_bytes(_box(b"ftyp", b"isom") + struct.pack(">I4s", 0, b"mdat") + b"\0" * 50)
        assert U.is_faststart(p) is False


def test_rubbish_is_unknown_rather_than_a_guess():
    """"I cannot tell" must never collapse into "needs fixing" — that is how a
    tool starts rewriting files it does not understand."""
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        (d / "empty.mp4").write_bytes(b"")
        (d / "junk.mp4").write_bytes(b"not a container at all")
        (d / "tiny.mp4").write_bytes(b"\x00\x00\x00\x02ab")     # size < 8: nonsense
        for name in ("empty.mp4", "junk.mp4", "tiny.mp4"):
            assert U.is_faststart(d / name) is None, name
        assert U.is_faststart(d / "does-not-exist.mp4") is None
        assert U.is_faststart(d / "unknown.xyz") is None        # not a container we read


def test_real_files_are_read_correctly_both_ways():
    if not _HAVE_FF:
        print("  skip (no ffmpeg)"); return
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        _clip(d / "plain.mp4")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(d / "plain.mp4"),
                        "-c", "copy", "-movflags", "+faststart", str(d / "fast.mp4")],
                       check=True, stdin=subprocess.DEVNULL)
        assert U.is_faststart(d / "plain.mp4") is False
        assert U.is_faststart(d / "fast.mp4") is True


def test_mkv_is_judged_on_its_cues_not_its_seekhead():
    """MKV's equivalent question is whether the Cues index precedes the clusters.

    Verified against a real 1.1 GB episode: a raw byte-search for the Cues ID
    finds a hit at offset 93 — inside the SeekHead, which stores that ID as a
    POINTER — while the real Cues element sits at byte 1,133,004,673, after 1,044
    clusters. A scanner is fooled; a structural walk is not.
    """
    if not _HAVE_FF:
        print("  skip (no ffmpeg)"); return
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        _clip(d / "src.mp4")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(d / "src.mp4"),
                        "-c", "copy", str(d / "back.mkv")], check=True, stdin=subprocess.DEVNULL)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(d / "src.mp4"),
                        "-c", "copy", "-cues_to_front", "1", str(d / "front.mkv")],
                       check=True, stdin=subprocess.DEVNULL)
        assert U.is_faststart(d / "back.mkv") is False
        assert U.is_faststart(d / "front.mkv") is True


# ── the remux ───────────────────────────────────────────────────────────────
def test_a_remux_moves_the_index_and_keeps_every_stream():
    if not _HAVE_FF:
        print("  skip (no ffmpeg)"); return
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        _clip(d / "f.mp4")
        before = probe(d / "f.mp4")
        res = U.remux_faststart(_cfg(d), d / "f.mp4")
        assert res.ok, res.error
        assert U.is_faststart(res.path) is True
        after = probe(res.path)
        assert after.vcodec == before.vcodec
        assert len(after.audio) == len(before.audio), "audio was dropped"
        assert not list(d.glob(".*part*")), "a temp file was left behind"


def test_a_container_change_carries_the_index_too():
    if not _HAVE_FF:
        print("  skip (no ffmpeg)"); return
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        _clip(d / "f.mp4")
        res = U.remux_faststart(_cfg(d), d / "f.mp4", container="mkv")
        assert res.ok, res.error
        assert res.path.suffix == ".mkv" and U.is_faststart(res.path) is True
        assert not (d / "f.mp4").exists(), "the original should have been replaced"


def test_a_failed_remux_leaves_the_original_alone():
    """The one thing a repair tool must never do is destroy the thing it was
    repairing. A file ffmpeg cannot read must come back untouched."""
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        junk = d / "junk.mp4"
        junk.write_bytes(b"definitely not a video" * 100)
        original = junk.read_bytes()
        res = U.remux_faststart(_cfg(d), junk)
        assert not res.ok and res.error
        assert junk.exists() and junk.read_bytes() == original
        assert not list(d.glob(".*part*")), "a temp file was left behind"


# ── health ──────────────────────────────────────────────────────────────────
def test_a_healthy_file_reports_nothing():
    """The most important test here. A tool that cries wolf over a clean library
    is worse than no tool."""
    if not _HAVE_FF:
        print("  skip (no ffmpeg)"); return
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        _clip(d / "ok.mp4", secs=5)
        rep = U.scan_health(_cfg(d), d / "ok.mp4", decode_seconds=5)
        assert not rep.needs, [f.detail for f in rep.faults]
        assert rep.fix == "none"


def test_chapters_past_the_end_are_found_and_are_a_remux():
    if not _HAVE_FF:
        print("  skip (no ffmpeg)"); return
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        _clip(d / "src.mp4", secs=4)
        meta = d / "c.txt"
        meta.write_text(";FFMETADATA1\n[CHAPTER]\nTIMEBASE=1/1000\nSTART=0\nEND=2000\n"
                        "title=A\n[CHAPTER]\nTIMEBASE=1/1000\nSTART=2000\nEND=90000\ntitle=B\n")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(d / "src.mp4"),
                        "-i", str(meta), "-map_metadata", "1", "-c", "copy",
                        str(d / "ch.mp4")], check=True, stdin=subprocess.DEVNULL)
        rep = U.scan_health(_cfg(d), d / "ch.mp4")
        assert rep.needs and rep.fix == "remux"
        assert any(f.kind == "chapters" for f in rep.faults), rep.faults


def test_ordinary_chapters_are_not_flagged():
    """Rounding at the last frame is not damage. Flagging it would put most of a
    healthy library on the repair list."""
    if not _HAVE_FF:
        print("  skip (no ffmpeg)"); return
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        _clip(d / "src.mp4", secs=4)
        meta = d / "c.txt"
        meta.write_text(";FFMETADATA1\n[CHAPTER]\nTIMEBASE=1/1000\nSTART=0\nEND=2000\n"
                        "title=A\n[CHAPTER]\nTIMEBASE=1/1000\nSTART=2000\nEND=4000\ntitle=B\n")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(d / "src.mp4"),
                        "-i", str(meta), "-map_metadata", "1", "-c", "copy",
                        str(d / "ch.mp4")], check=True, stdin=subprocess.DEVNULL)
        assert U.chapters_past_duration(_cfg(d), d / "ch.mp4") is None


def test_a_truncated_container_is_reported_but_not_repairable():
    """A half-downloaded file is gone, not broken. Offering to "fix" it would
    spend an hour proving that."""
    if not _HAVE_FF:
        print("  skip (no ffmpeg)"); return
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        # A faststart file, because that is what a real interrupted download looks
        # like: the index arrived first, so the file still probes — it is simply
        # short. (Truncate a non-faststart file and you lose the index too, which
        # is a different fault: unreadable rather than truncated.)
        _clip(d / "src.mp4", secs=5)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(d / "src.mp4"),
                        "-c", "copy", "-movflags", "+faststart", str(d / "full.mp4")],
                       check=True, stdin=subprocess.DEVNULL)
        whole = (d / "full.mp4").read_bytes()
        (d / "cut.mp4").write_bytes(whole[: int(len(whole) * 0.7)])
        rep = U.scan_health(_cfg(d), d / "cut.mp4")
        assert rep.needs
        assert any(f.kind == "container" for f in rep.faults), rep.faults
        assert rep.fix == "none", "a truncated download must not be offered a repair"
        assert "shorter than it claims" in rep.faults[0].detail


def test_bitstream_damage_asks_for_a_re_encode():
    if not _HAVE_FF:
        print("  skip (no ffmpeg)"); return
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        _clip(d / "good.mp4", secs=6)
        raw = bytearray((d / "good.mp4").read_bytes())
        for i in range(len(raw) // 3, len(raw) // 3 + 4000):
            raw[i] ^= 0xFF
        (d / "bad.mp4").write_bytes(bytes(raw))
        rep = U.scan_health(_cfg(d), d / "bad.mp4", decode_seconds=10)
        assert rep.needs and rep.fix == "reencode", [f.detail for f in rep.faults]
        assert "@ 0x" not in rep.faults[0].detail, "ffmpeg's internals leaked into the message"


def test_an_unreadable_file_is_named_as_such():
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        (d / "x.mp4").write_bytes(b"nope")
        rep = U.scan_health(_cfg(d), d / "x.mp4")
        assert rep.needs and rep.fix == "none"
        assert rep.faults[0].kind == "unreadable"


def test_a_re_encode_is_never_run_unless_it_was_enabled():
    """The re-encode is opt-in because it costs a generation of quality and an
    hour. A scan that quietly escalated to one would be a betrayal."""
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        rep = U.FileReport(path=d / "f.mp4",
                           faults=[U.Fault(kind="nal", detail="damaged", fix="reencode")])
        res = U.fix_health(_cfg(d), rep, allow_reencode=False)
        assert not res.ok and "not enabled" in res.error


def test_a_repair_re_encode_caps_at_the_file_s_own_bitrate():
    """A repair is not a shrink: it rewrites a damaged stream at the quality it
    already had, so the ceiling is the source's own rate, not a tier."""
    if not _HAVE_FF:
        print("  skip (no ffmpeg)"); return
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        # A fat clip, so the result is the file's own rate rather than the floor.
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
                        "-i", "testsrc2=size=1280x720:rate=25", "-t", "4",
                        "-c:v", "libx264", "-b:v", "6000k", "-pix_fmt", "yuv420p",
                        str(d / "f.mp4")], check=True, stdin=subprocess.DEVNULL)
        kbps = U._source_kbps(_cfg(d), d / "f.mp4")
        info = probe(d / "f.mp4")
        assert abs(kbps - info.effective_bps / 1000) < info.effective_bps / 1000 * 0.25, kbps

        # …and a floor, so a tiny or unmeasurable file cannot produce a cap so low
        # that the "repair" is guaranteed to look worse than the damage.
        (d / "unknown.mp4").write_bytes(b"nope")
        assert U._source_kbps(_cfg(d), d / "unknown.mp4") == 8000
