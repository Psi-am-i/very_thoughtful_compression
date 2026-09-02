"""Bloated modern sources — the opt-in that re-encodes H.265 / VP9 / AV1.

The engine's standing rule is that modern codecs are never transcoded: they are
already efficient, so a re-encode usually buys a second lossy generation and very
little space. But a bad hardware encoder at a silly bitrate is real (drone and
action-cam footage), and those files ARE worth reclaiming.

So the door opens narrowly: off by default, gated far more strictly than H.264
(2x its tier target, not 1.10x), limited to codecs worth the hours, and budgeted
per run worst-first so a night's encoding goes on the fattest files. These tests
pin every one of those limits — the failure mode to fear is an option that
quietly queues 800 overnight encodes.

Run:  python3 -m pytest tests/test_modern_reencode.py
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
from vtc.result import Mode, Outcome  # noqa: E402


def _info(path="/x/clip.mp4", vcodec="hevc", kbps=40000, w=3840, h=2160,
          fps=24.0, dur=3600.0):
    return MediaInfo(path=Path(path), ok=True, vcodec=vcodec, width=w, height=h,
                     fps=fps, bit_rate=int(kbps * 1000), duration=dur, pix_fmt="yuv420p")


def _cfg(d, **kw):
    return RunConfig(src=Path(d), **kw)


# ── off by default ───────────────────────────────────────────────────────────
def test_modern_sources_are_still_left_alone_by_default():
    """The standing rule must not move just because the option exists."""
    with tempfile.TemporaryDirectory() as d:
        for codec in ("hevc", "vp9", "av1"):
            info = _info(vcodec=codec, kbps=90000)          # absurdly bloated
            mode, outcome, _ = pipeline.decide(_cfg(d), info)
            assert mode is None and outcome is Outcome.SKIP_MODERN, codec


def test_a_modern_file_in_a_foreign_container_still_only_remuxes_by_default():
    with tempfile.TemporaryDirectory() as d:
        info = _info(path="/x/clip.mkv", kbps=90000)
        mode, _, _ = pipeline.decide(_cfg(d), info)
        assert mode is Mode.REMUX


# ── the gate ─────────────────────────────────────────────────────────────────
def test_a_truly_bloated_modern_file_is_re_encoded_when_asked():
    with tempfile.TemporaryDirectory() as d:
        cfg = _cfg(d, reencode_modern=True)
        info = _info(kbps=90000)                            # ~5x its 4K target
        mode, outcome, target = pipeline.decide(cfg, info)
        assert mode is Mode.SHRINK and outcome is None
        assert 0 < target < 90000


def test_a_merely_over_target_modern_file_is_not_worth_the_hours():
    """1.10x is the H.264 bar. Modern has to clear 2x, or you spend a night to
    save a sliver and a generation of quality."""
    with tempfile.TemporaryDirectory() as d:
        cfg = _cfg(d, reencode_modern=True)
        target_4k = pipeline.decide(_cfg(d, reencode_modern=True), _info(kbps=90000))[2]
        assert target_4k > 0
        # sit the source just 30% over its target — over the H.264 bar, under 2x
        info = _info(kbps=int(target_4k * 1.3))
        mode, outcome, _ = pipeline.decide(cfg, info)
        assert mode is None and outcome is Outcome.SKIP_MODERN
        # …and just over 2x qualifies
        info = _info(kbps=int(target_4k * 2.2))
        assert pipeline.decide(cfg, info)[0] is Mode.SHRINK


def test_the_bar_is_configurable():
    with tempfile.TemporaryDirectory() as d:
        info = _info(kbps=30000)                    # ~1.8x its 4K tier target
        strict = _cfg(d, reencode_modern=True, modern_over_tolerance=4.0)
        lenient = _cfg(d, reencode_modern=True, modern_over_tolerance=1.2)
        assert pipeline.decide(strict, info)[1] is Outcome.SKIP_MODERN
        assert pipeline.decide(lenient, info)[0] is Mode.SHRINK


def test_av1_is_excluded_unless_explicitly_added():
    """AV1 is the most efficient of the three, AV1->H.265 is usually an efficiency
    downgrade, and AV1->AV1 in software is punishing. Turning the feature on must
    not quietly pull it in."""
    with tempfile.TemporaryDirectory() as d:
        info = _info(vcodec="av1", kbps=90000)
        assert pipeline.decide(_cfg(d, reencode_modern=True), info)[1] is Outcome.SKIP_MODERN
        opted_in = _cfg(d, reencode_modern=True, modern_codecs=("hevc", "vp9", "av1"))
        assert pipeline.decide(opted_in, info)[0] is Mode.SHRINK


def test_hevc_and_vp9_are_both_eligible_by_default():
    with tempfile.TemporaryDirectory() as d:
        cfg = _cfg(d, reencode_modern=True)
        for codec in ("hevc", "vp9"):
            assert pipeline.decide(cfg, _info(vcodec=codec, kbps=90000))[0] is Mode.SHRINK, codec


def test_the_savings_bar_still_applies():
    """Both gates, as on the H.264 path: far over target AND able to clear the
    minimum saving. A file that cannot get meaningfully smaller is not touched."""
    with tempfile.TemporaryDirectory() as d:
        cfg = _cfg(d, reencode_modern=True, min_saving_ratio=0.10)   # must be 90% smaller
        assert pipeline.decide(cfg, _info(kbps=90000))[1] is Outcome.SKIP_MODERN


def test_the_tool_can_never_eat_its_own_output():
    """Our own files are HEVC, so this option could in principle re-encode them.
    The second-generation guard fires first and must keep firing."""
    with tempfile.TemporaryDirectory() as d:
        info = _info(kbps=90000)
        info.vtc = {"VTC_MODE": "shrink"}
        mode, outcome, _ = pipeline.decide(_cfg(d, reencode_modern=True), info)
        assert mode is None and outcome is Outcome.SKIP_SECOND_GEN


# ── the per-run budget ───────────────────────────────────────────────────────
def _rows(d, cfg, sizes_kbps):
    """Real files on disk (so .stat() works), one PlanRow each."""
    rows = []
    for i, kbps in enumerate(sizes_kbps):
        f = Path(d) / f"clip{i:02d}.mp4"
        # size must be consistent with the bitrate for the saving prediction
        f.write_bytes(b"\0" * int(kbps * 1000 / 8 * 10))     # 10 seconds' worth
        info = _info(path=str(f), kbps=kbps, dur=10.0)
        rows.append(pipeline.PlanRow(f, info, *pipeline.decide(cfg, info)))
    return rows


def test_the_budget_takes_the_fattest_files_first():
    """The point of a budget is to buy back the most disk for the hours available,
    so the shortlist is ranked by predicted bytes SAVED — not by percentage, and
    not by file size."""
    with tempfile.TemporaryDirectory() as d:
        cfg = _cfg(d, reencode_modern=True, modern_max_files=3)
        rows = _rows(d, cfg, [30000, 90000, 40000, 120000, 60000])
        picked = pipeline.pick_modern_shortlist(cfg, rows)
        assert len(picked) == 3
        names = sorted(Path(p).name for p in picked)
        # clip03 (120 Mbps), clip01 (90), clip04 (60) — the three fattest
        assert names == ["clip01.mp4", "clip03.mp4", "clip04.mp4"], names


def test_no_budget_means_no_shortlist_not_an_empty_one():
    """An empty shortlist reads as "no limit in force". A budget of 0 must
    therefore produce no shortlist at all, never a set that blocks everything."""
    with tempfile.TemporaryDirectory() as d:
        cfg = _cfg(d, reencode_modern=True, modern_max_files=0)
        rows = _rows(d, cfg, [90000, 120000])
        assert pipeline.pick_modern_shortlist(cfg, rows) == frozenset()
        assert cfg.picked_for_modern(rows[0].path) is True


def test_a_shortlist_actually_holds_the_rest_back():
    with tempfile.TemporaryDirectory() as d:
        cfg = _cfg(d, reencode_modern=True, modern_max_files=1)
        rows = _rows(d, cfg, [90000, 120000])
        cfg.modern_files = pipeline.pick_modern_shortlist(cfg, rows)
        picked = [r for r in rows if cfg.picked_for_modern(r.path)]
        held = [r for r in rows if not cfg.picked_for_modern(r.path)]
        assert len(picked) == 1 and len(held) == 1
        assert pipeline.decide(cfg, picked[0].info)[0] is Mode.SHRINK
        # The one held back is DEFERRED, not settled — a distinct outcome, because
        # the ledger must not record it as done or it would never come back.
        assert pipeline.decide(cfg, held[0].info)[1] is Outcome.DEFER_MODERN


def test_the_budget_ignores_files_that_were_never_candidates():
    """H.264 work is not modern work and must not eat the modern budget."""
    with tempfile.TemporaryDirectory() as d:
        cfg = _cfg(d, reencode_modern=True, modern_max_files=5)
        f = Path(d) / "h264.mp4"
        f.write_bytes(b"\0" * 50_000_000)
        info = _info(path=str(f), vcodec="h264", kbps=40000, dur=10.0)
        rows = [pipeline.PlanRow(f, info, *pipeline.decide(cfg, info))]
        assert rows[0].mode is Mode.SHRINK                   # it IS being re-encoded
        assert pipeline.pick_modern_shortlist(cfg, rows) == frozenset()


# ── the ledger ───────────────────────────────────────────────────────────────
def test_turning_the_option_on_re_evaluates_the_library():
    """Every previous run recorded these files as "modern, left alone". Without a
    signature change the option would appear to do nothing on a scanned library."""
    with tempfile.TemporaryDirectory() as d:
        off = _cfg(d)
        on = _cfg(d, reencode_modern=True)
        looser = _cfg(d, reencode_modern=True, modern_over_tolerance=1.5)
        more = _cfg(d, reencode_modern=True, modern_codecs=("hevc", "vp9", "av1"))
        assert "mod" not in off.settings_signature()
        assert on.settings_signature() != off.settings_signature()
        assert looser.settings_signature() != on.settings_signature()
        assert more.settings_signature() != on.settings_signature()


def test_a_deferred_file_is_not_recorded_as_done():
    """The budget is a drip, not a ceiling. A file held back must come round again
    on the next run — which it only does if the ledger never recorded it. This is
    the difference between "25 per run until the library is clean" and "25 files,
    ever"."""
    import shutil
    import subprocess
    if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        print("  skip test_a_deferred_file_is_not_recorded_as_done (no ffmpeg)")
        return
    from vtc.config import Encoder, SourceAction
    with tempfile.TemporaryDirectory() as d:
        src = Path(d)
        for name, kbps in [("a.mp4", 30000), ("b.mp4", 60000), ("c.mp4", 45000)]:
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
                            "-i", "testsrc2=size=1920x1080:rate=24", "-t", "2",
                            "-c:v", "libx265", "-b:v", f"{kbps}k", "-tag:v", "hvc1",
                            "-pix_fmt", "yuv420p", str(src / name)],
                           check=True, stdin=subprocess.DEVNULL)
        done = []
        for _ in range(3):
            cfg = RunConfig(src=src, encoder=Encoder.HARDWARE, ledger_enabled=True,
                            source_action=SourceAction.ARCHIVE,
                            reencode_modern=True, modern_max_files=1)
            done += [r.path.name for r in pipeline.run(cfg) if r.outcome is Outcome.SHRINK]
        # one per run, fattest first, and every file eventually reached
        assert done == ["b.mp4", "c.mp4", "a.mp4"], done
