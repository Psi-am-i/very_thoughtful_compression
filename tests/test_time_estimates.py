"""Predicted encode time — one arithmetic path, shared by every estimate.

The app quotes a time in three places: the "about N hours" before you commit, the
countdown during a run, and the modern re-encode review. They must agree, and
they must be right, because someone is deciding whether to give up a night on the
strength of them.

The unit is OUTPUT PIXEL-FRAMES per second, not minutes of video. Two things that
buys, both pinned below: a 60fps file costs twice a 30fps one of the same length,
and a frame-size cap makes the run genuinely faster — capping 4K at 1080p quarters
the work, and an estimate that priced the source frame quoted four times the truth.

Run:  python3 -m pytest tests/test_time_estimates.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vtc import pipeline  # noqa: E402
from vtc.config import RunConfig  # noqa: E402
from vtc.ffprobe import MediaInfo  # noqa: E402

_PX_1080P = 1920 * 1080


def _info(w=1920, h=1080, fps=30.0, dur=3600.0, rotation=0):
    return MediaInfo(path=Path("/x/f.mp4"), ok=True, vcodec="h264", width=w, height=h,
                     fps=fps, bit_rate=40_000_000, duration=dur, rotation=rotation)


def test_work_scales_with_pixels_and_frames_not_with_length_alone():
    hour_1080p30 = pipeline.encode_work(1920, 1080, 30, 3600)
    assert pipeline.encode_work(3840, 2160, 30, 3600) == 4 * hour_1080p30
    assert pipeline.encode_work(1920, 1080, 60, 3600) == 2 * hour_1080p30
    assert pipeline.encode_work(1920, 1080, 30, 7200) == 2 * hour_1080p30


def test_a_frame_cap_makes_the_estimate_faster():
    """The regression this test exists for: Frame Size shipped before the estimate
    knew about it, so a 4K library capped at 1080p was quoted 4x its real cost."""
    with tempfile.TemporaryDirectory() as d:
        info = _info(w=3840, h=2160)
        rate = 1.0e8
        full = pipeline.encode_seconds(RunConfig(src=Path(d)), info, rate)
        capped = pipeline.encode_seconds(RunConfig(src=Path(d), max_short_edge=1080), info, rate)
        assert full > 0 and capped > 0
        assert abs(full / capped - 4.0) < 0.01, (full, capped)


def test_a_rotated_source_is_priced_on_the_picture():
    """A portrait clip has the same pixel count either way round, so rotation must
    not change the estimate — but the CAP applies to the display frame, so a capped
    rotated file must be priced on what it will actually encode."""
    with tempfile.TemporaryDirectory() as d:
        cfg, rate = RunConfig(src=Path(d)), 1.0e8
        assert (pipeline.encode_seconds(cfg, _info(w=3840, h=2160), rate)
                == pipeline.encode_seconds(cfg, _info(w=3840, h=2160, rotation=90), rate))
        capped = RunConfig(src=Path(d), max_short_edge=1080)
        # portrait 2160x3840 capped on its short edge -> 1080x1920: a quarter again
        rotated = pipeline.encode_seconds(capped, _info(w=3840, h=2160, rotation=90), rate)
        flat = pipeline.encode_seconds(capped, _info(w=3840, h=2160), rate)
        assert abs(rotated - flat) < 1e-6


def test_it_refuses_to_guess_when_it_cannot_know():
    """0.0 means "not knowable" and the caller decides what to assume. Silently
    inventing a number here is how an estimate becomes a lie."""
    with tempfile.TemporaryDirectory() as d:
        cfg = RunConfig(src=Path(d))
        assert pipeline.encode_seconds(cfg, _info(dur=0.0), 1.0e8) == 0.0    # no length
        assert pipeline.encode_seconds(cfg, _info(fps=0.0), 1.0e8) == 0.0    # no frame rate
        assert pipeline.encode_seconds(cfg, _info(w=0, h=0), 1.0e8) == 0.0   # no geometry
        assert pipeline.encode_seconds(cfg, _info(), 0.0) == 0.0             # no rate


def test_the_fallback_constants_still_reproduce_the_old_model():
    """With nothing measured yet the app falls back to constants measured once on
    an M-series Mac, quoted "6.0x realtime at 1080p". At the reference 30fps the
    new arithmetic must land on exactly the old answer — the change is a better
    model, not a silent re-rating of everyone's library."""
    with tempfile.TemporaryDirectory() as d:
        HW_SPEED = 6.0
        rate = HW_SPEED * _PX_1080P * 30.0           # webapp's fallback conversion
        cfg = RunConfig(src=Path(d))
        for w, h in ((1920, 1080), (3840, 2160), (1280, 720)):
            old = 3600.0 / (HW_SPEED * (_PX_1080P / (w * h)))    # duration / scaled speed
            new = pipeline.encode_seconds(cfg, _info(w=w, h=h, fps=30.0), rate)
            assert abs(new - old) < 1e-6, (w, h, new, old)


def test_a_measured_rate_changes_the_answer_proportionally():
    with tempfile.TemporaryDirectory() as d:
        cfg, info = RunConfig(src=Path(d)), _info()
        slow = pipeline.encode_seconds(cfg, info, 1.0e8)
        fast = pipeline.encode_seconds(cfg, info, 2.0e8)
        assert abs(slow / fast - 2.0) < 1e-9


def test_the_review_and_the_clock_agree():
    """The two estimates are the same arithmetic or they are worth nothing: a
    review promising 10 hours and a clock counting 40 is a bug someone only finds
    at 3am."""
    with tempfile.TemporaryDirectory() as d:
        cfg = RunConfig(src=Path(d), reencode_modern=True, max_short_edge=1080)
        infos = []
        for i, kbps in enumerate((90000, 70000)):
            f = Path(d) / f"c{i}.mp4"
            f.write_bytes(b"")
            infos.append((MediaInfo(path=f, ok=True, vcodec="hevc", width=3840, height=2160,
                                    fps=24.0, bit_rate=kbps * 1000, duration=3600.0),
                          int(kbps * 1000 / 8 * 3600)))
        rate = 1.0e8
        review = pipeline.modern_review(cfg, infos)
        per_file = sum(pipeline.encode_seconds(cfg, i, rate) for i, _s in infos)
        assert abs(review["work"] / rate - per_file) < 1e-6


def test_every_file_result_is_actually_timed():
    """`elapsed_s` must be populated, because it is the ONLY measurement of how
    fast this machine encodes and every future estimate is built on it.

    It was declared on FileResult and never assigned by anything — always 0.0 —
    so _observed_rate could never return a rate, the app fell back to constants
    measured on one developer's Mac forever, and nothing anywhere said so. A dead
    measurement is worse than no measurement: it looks like it is working.
    """
    import shutil
    import subprocess
    if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        print("  skip test_every_file_result_is_actually_timed (no ffmpeg)")
        return
    from vtc import webapp
    from vtc.config import Encoder, SourceAction
    with tempfile.TemporaryDirectory() as d:
        src = Path(d)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
                        "-i", "testsrc2=size=1280x720:rate=30", "-t", "2",
                        "-c:v", "libx264", "-b:v", "12000k", "-pix_fmt", "yuv420p",
                        str(src / "clip.mp4")], check=True, stdin=subprocess.DEVNULL)
        cfg = RunConfig(src=src, encoder=Encoder.HARDWARE, ledger_enabled=False,
                        source_action=SourceAction.KEEP)
        results = pipeline.run(cfg)
        assert results and results[0].elapsed_s > 0, "process_file is not being timed"
        # …and that timing must turn into a usable rate, which is the whole point
        assert webapp._observed_rate(results) is not None, "no rate from a real encode"


def test_the_rate_is_kept_per_concurrency():
    """A rate is measured PER STREAM, so four encodes at once are each slower than
    one alone. Reusing a jobs=1 measurement for a jobs=4 run would claim a
    fourfold speed-up that contention never delivers."""
    from vtc import webapp
    with tempfile.TemporaryDirectory() as d:
        one = RunConfig(src=Path(d), jobs=1)
        four = RunConfig(src=Path(d), jobs=4)
        assert webapp._rate_key(one, hw=True) != webapp._rate_key(four, hw=True)
        assert webapp._rate_key(one, hw=True) != webapp._rate_key(one, hw=False)
