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


# ── the review: what the user is shown before committing a night ─────────────
def _probed(d, kbps_list, w=1920, h=1080, fps=24.0, dur=3600.0, vcodec="hevc"):
    """(info, size) pairs with sizes consistent with the bitrates."""
    out = []
    for i, kbps in enumerate(kbps_list):
        f = Path(d) / f"clip{i:02d}.mp4"
        size = int(kbps * 1000 / 8 * dur)
        f.write_bytes(b"")                          # the review stats nothing; size is passed in
        out.append((_info(path=str(f), vcodec=vcodec, kbps=kbps, w=w, h=h, fps=fps, dur=dur), size))
    return out


def test_the_review_reports_the_whole_queue_not_the_budget():
    """The budget is the question being asked, so the review must ignore any
    budget already set — otherwise "how many shall I do?" would be answered with
    the number already chosen."""
    with tempfile.TemporaryDirectory() as d:
        cfg = _cfg(d, reencode_modern=True, modern_max_files=2)
        r = pipeline.modern_review(cfg, _probed(d, [90000, 80000, 70000, 60000, 50000]))
        assert r["files"] == 5, r["files"]


def test_the_review_ranks_worst_first_and_totals_honestly():
    with tempfile.TemporaryDirectory() as d:
        cfg = _cfg(d, reencode_modern=True)
        r = pipeline.modern_review(cfg, _probed(d, [50000, 90000, 70000]))
        saved = [row["saved_bytes"] for row in r["top"]]
        assert saved == sorted(saved, reverse=True), saved
        assert r["saved_bytes"] == sum(saved)
        assert r["bytes"] == sum(row["bytes"] for row in r["top"])
        # "over" is how many times over its tier target each file is
        assert all(row["over"] > 2.0 for row in r["top"]), r["top"]


def test_the_review_excludes_what_would_not_be_touched():
    with tempfile.TemporaryDirectory() as d:
        cfg = _cfg(d, reencode_modern=True)
        # a lean file, and an h264 file — neither is a modern re-encode candidate
        probed = _probed(d, [2000]) + _probed(d, [90000], vcodec="h264")
        assert pipeline.modern_review(cfg, probed)["files"] == 0


def test_the_review_is_empty_when_the_option_is_off():
    with tempfile.TemporaryDirectory() as d:
        assert pipeline.modern_review(_cfg(d), _probed(d, [90000]))["files"] == 0


def test_the_cumulative_columns_price_a_top_x_choice():
    """"Top 25" has to show its own cost, so the review carries running totals
    down the ranked list — element i covers the first i+1 files."""
    with tempfile.TemporaryDirectory() as d:
        cfg = _cfg(d, reencode_modern=True)
        r = pipeline.modern_review(cfg, _probed(d, [90000, 70000, 50000]))
        assert len(r["saved_cum"]) == 3 and len(r["work_cum"]) == 3
        assert r["saved_cum"][0] == r["top"][0]["saved_bytes"]
        assert r["saved_cum"][-1] == r["saved_bytes"]
        assert r["work_cum"] == sorted(r["work_cum"])          # monotonic
        assert abs(r["work_cum"][-1] - r["work"]) < 1.0


def test_work_is_measured_in_output_pixels_not_minutes():
    """A 4K file is ~4x the work of a 1080p file of the same length, and a frame
    cap must therefore predict a faster run. Estimating from play length alone
    would tell someone 46 hours when it is nearer 12."""
    hd = pipeline.encode_work(1920, 1080, 24, 3600)
    uhd = pipeline.encode_work(3840, 2160, 24, 3600)
    assert abs(uhd / hd - 4.0) < 0.01
    assert pipeline.encode_work(1920, 1080, 48, 3600) == 2 * hd     # fps counts too
    for bad in ((0, 1080, 24, 60), (1920, 0, 24, 60), (1920, 1080, 0, 60), (1920, 1080, 24, 0)):
        assert pipeline.encode_work(*bad) == 0.0                   # never guesses


def test_a_frame_cap_lowers_the_predicted_work():
    """The two settings compose: capping 4K at 1080p quarters the pixels the
    encoder has to produce, so the review must price the run accordingly."""
    with tempfile.TemporaryDirectory() as d:
        probed = _probed(d, [90000], w=3840, h=2160)
        full = pipeline.modern_review(_cfg(d, reencode_modern=True), probed)
        capped = pipeline.modern_review(
            _cfg(d, reencode_modern=True, max_short_edge=1080), probed)
        assert abs(full["work"] / capped["work"] - 4.0) < 0.01


# ── the time estimate ────────────────────────────────────────────────────────
def test_the_rate_is_measured_from_real_encodes_only():
    """"About 46 hours" is only worth saying if it came from somewhere real. A
    remux is a stream copy and nearly instant — letting it into the average would
    turn the estimate into a promise no encode could keep."""
    from vtc import webapp
    from vtc.result import FileDetail, FileResult, Outcome

    def result(mode, elapsed, w=1920, h=1080, fps=24.0, dur=3600.0):
        return FileResult(Path("/x/f.mp4"), Outcome.SHRINK, elapsed_s=elapsed,
                          detail=FileDetail(mode=mode, out_width=w, out_height=h,
                                            fps=fps, duration=dur))

    one_hour_1080p24 = 1920 * 1080 * 24 * 3600
    assert webapp._observed_rate([result("shrink", 3600.0)]) == one_hour_1080p24 / 3600.0
    # a remux alongside it must not count at all
    mixed = [result("shrink", 3600.0), result("remux", 0.2)]
    assert webapp._observed_rate(mixed) == one_hour_1080p24 / 3600.0
    # nothing measurable -> no rate, so the caller says so instead of guessing
    assert webapp._observed_rate([]) is None
    assert webapp._observed_rate([result("remux", 0.2)]) is None
    assert webapp._observed_rate([result("shrink", 0.0)]) is None


def test_a_new_measurement_is_folded_in_not_slammed_in():
    """One run on unusual content is evidence, not the machine's speed."""
    from vtc import webapp
    assert webapp._blend_rate(None, 100.0) == 100.0        # nothing stored yet
    assert webapp._blend_rate(0, 100.0) == 100.0
    assert webapp._blend_rate("nonsense", 100.0) == 100.0
    blended = webapp._blend_rate(100.0, 200.0)
    assert 100.0 < blended < 200.0, blended                # moves toward, doesn't jump


def test_the_estimate_says_where_its_number_came_from():
    """A measured run beats a 5-second preview clip, and when there is neither the
    app must admit it rather than invent an number."""
    from vtc import webapp
    from vtc.config import OutCodec

    api = webapp.Api.__new__(webapp.Api)                   # no window, no pywebview
    with tempfile.TemporaryDirectory() as d:
        cfg = _cfg(d, out_codec=OutCodec.H265)
        key = webapp._rate_key(cfg, hw=bool(__import__("vtc.encode", fromlist=["x"])
                                            .select_hw_encoder(cfg)))
        api._adv = {}
        assert api._encode_rate(cfg) == (None, "")
        api._adv = {webapp._SAMPLE_RATES_KEY: {key: 5.0}}
        assert api._encode_rate(cfg) == (5.0, "a sample encode")
        # a real run always wins over the rough sample
        api._adv = {webapp._SAMPLE_RATES_KEY: {key: 5.0},
                    webapp._RATES_KEY: {key: 9.0}}
        assert api._encode_rate(cfg) == (9.0, "your last run")
