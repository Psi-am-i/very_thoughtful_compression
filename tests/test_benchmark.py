"""Benchmarking this machine — and then actually using the answer.

The point of the feature is that every timing the app quotes stops being a
developer's constant and becomes the user's own measurement. So the tests that
matter are the ones joining the two halves: that it measures real files sensibly,
and that what it learns is what the estimates then read.

Run:  python3 -m pytest tests/test_benchmark.py
"""

from __future__ import annotations

import os
import random
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vtc import bench, encode, webapp  # noqa: E402
from vtc.config import RunConfig  # noqa: E402
from vtc.model import OutCodec  # noqa: E402

_HAVE_FF = shutil.which("ffmpeg") and shutil.which("ffprobe")


def _lib(d: Path, n=6, size="640x360", secs=3):
    """A little library. Synthetic is fine HERE: these tests check the plumbing —
    selection, storage, wiring — never a number a user would see."""
    for i in range(n):
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
                        "-i", f"testsrc2=size={size}:rate=25", "-t", str(secs),
                        "-c:v", "libx264", "-b:v", "3000k", "-pix_fmt", "yuv420p",
                        str(d / f"ep{i:02d}.mp4")], check=True, stdin=subprocess.DEVNULL)


# ── choosing what to measure ─────────────────────────────────────────────────
def test_samples_are_drawn_at_random_not_off_the_top():
    """A library is alphabetical, so the first N files are one show — one kind of
    content, shot one way. Random draws are the whole reason two samples beat one."""
    if not _HAVE_FF:
        print("  skip (no ffmpeg)"); return
    with tempfile.TemporaryDirectory() as d:
        d = Path(d); _lib(d, n=8)
        cfg = RunConfig(src=d)
        a = bench.pick_samples(cfg, 3, rng=random.Random(1), min_bytes=0)
        b = bench.pick_samples(cfg, 3, rng=random.Random(2), min_bytes=0)
        assert len(a) == len(b) == 3
        assert a != b, "two different seeds drew the same files"


def test_tiny_files_are_left_out_of_the_sample():
    """Trailers, extras and sample.mkv are not the workload, and a 4 MB file would
    measure start-up cost rather than encoding."""
    if not _HAVE_FF:
        print("  skip (no ffmpeg)"); return
    with tempfile.TemporaryDirectory() as d:
        d = Path(d); _lib(d, n=3)
        big = d / "episode.mp4"
        big.write_bytes(b"\0" * 300_000_000)
        picked = bench.pick_samples(RunConfig(src=d), 2)
        assert picked == [big], picked


def test_a_library_of_only_small_files_is_still_measurable():
    """The size filter is a preference, not a refusal: someone whose whole library
    is 50 MB files must still be able to benchmark."""
    if not _HAVE_FF:
        print("  skip (no ffmpeg)"); return
    with tempfile.TemporaryDirectory() as d:
        d = Path(d); _lib(d, n=3)
        assert len(bench.pick_samples(RunConfig(src=d), 2)) == 2


def test_an_empty_library_yields_nothing_rather_than_raising():
    with tempfile.TemporaryDirectory() as d:
        b = bench.run_benchmark(RunConfig(src=Path(d)), samples=2)
        assert b.results == [] and b.samples == []


# ── what it measures ─────────────────────────────────────────────────────────
def test_it_only_offers_paths_this_machine_really_has():
    """Probed, not assumed — a listed-but-broken encoder must never reach a
    benchmark and from there an estimate."""
    if not _HAVE_FF:
        print("  skip (no ffmpeg)"); return
    with tempfile.TemporaryDirectory() as d:
        paths = bench.available_paths(RunConfig(src=Path(d)))
        assert paths, "no encode path at all?"
        for codec, path, enc in paths:
            if path == "hardware":
                assert encode._encoder_works("ffmpeg", enc), f"{enc} was offered but fails"
        # software H.264/H.265 are always there; AV1 only if libsvtav1 is built in
        assert (OutCodec.H264, "software", "libx264") in paths
        assert (OutCodec.H265, "software", "libx265") in paths


def test_a_real_benchmark_produces_usable_rates():
    if not _HAVE_FF:
        print("  skip (no ffmpeg)"); return
    with tempfile.TemporaryDirectory() as d:
        d = Path(d); _lib(d, n=3, secs=4)
        b = bench.run_benchmark(RunConfig(src=d), samples=1, seconds=3,
                                measure_quality=False)
        assert b.results, "nothing was measured"
        assert any(r.ok for r in b.results)
        for (codec, path), rate in b.rates().items():
            assert rate > 0, (codec, path)
        rows = b.summary()
        assert rows and all(r["at_1080p"] > 0 for r in rows)


def test_the_readable_speed_does_not_depend_on_what_was_sampled():
    """× realtime averaged over a 1080p episode and a DVD one is a fact about the
    draw, not the machine. at_1080p normalises it, so two benchmarks of the same
    machine are comparable however the sampling fell out."""
    b = bench.Benchmark()
    rate = 1920 * 1080 * 30 * 4.0                       # exactly 4x realtime at 1080p30
    for dur, w, h, fps in ((60.0, 1920, 1080, 30.0), (60.0, 720, 576, 25.0)):
        work = w * h * fps * dur
        b.results.append(bench.PathResult(
            codec="h265", path="software", encoder="libx265",
            seconds=work / rate, rate=rate, realtime=dur / (work / rate),
            kbps=1000, target_kbps=2000))
    row = b.summary()[0]
    assert abs(row["at_1080p"] - 4.0) < 0.01, row["at_1080p"]
    assert abs(row["of_target"] - 0.5) < 1e-9           # spent half its allowance


def test_a_failed_path_is_recorded_and_the_rest_carry_on():
    """A benchmark that dies on one bad encoder is worth nothing."""
    r = bench.PathResult(codec="av1", path="hardware", encoder="av1_nvenc",
                         error="No such encoder")
    assert not r.ok
    b = bench.Benchmark(results=[r])
    assert b.summary() == [] and b.rates() == {}


# ── and then actually using it ───────────────────────────────────────────────
def test_the_benchmark_becomes_the_number_the_app_quotes(monkeypatch, tmp_path):
    """The join that makes the feature worth having: what the benchmark learned is
    what the estimates read, ranked above a preview sample and the shipped
    constants, and below a real run of the library."""
    settings = tmp_path / "settings.json"
    monkeypatch.setattr(webapp, "_settings_path", lambda: settings)
    monkeypatch.setattr(webapp.encode, "select_hw_encoder", lambda c: "hevc_videotoolbox")
    api = webapp.Api.__new__(webapp.Api)
    api._adv = {}
    cfg = RunConfig(src=tmp_path, out_codec=OutCodec.H265)
    key = webapp._rate_key(cfg, hw=True)

    assert api._encode_rate(cfg) == (None, "")                 # nothing known yet
    api._adv[webapp._SAMPLE_RATES_KEY] = {key: 100.0}
    assert api._encode_rate(cfg) == (100.0, "a sample encode")
    api._adv[webapp._BENCH_KEY] = {key: 200.0}
    assert api._encode_rate(cfg) == (200.0, "your benchmark")  # beats the preview clip
    api._adv[webapp._RATES_KEY] = {key: 300.0}
    assert api._encode_rate(cfg) == (300.0, "your last run")   # a real run beats both


def test_a_benchmark_still_counts_when_the_run_uses_more_jobs(monkeypatch, tmp_path):
    """The benchmark encodes sequentially, so it files everything under j1. Someone
    running four at a time would otherwise get nothing from it and fall back to a
    developer's constants — worse than their own machine measured differently."""
    monkeypatch.setattr(webapp, "_settings_path", lambda: tmp_path / "s.json")
    monkeypatch.setattr(webapp.encode, "select_hw_encoder", lambda c: "hevc_videotoolbox")
    api = webapp.Api.__new__(webapp.Api)
    one = RunConfig(src=tmp_path, out_codec=OutCodec.H265, jobs=1)
    four = RunConfig(src=tmp_path, out_codec=OutCodec.H265, jobs=4)
    api._adv = {webapp._BENCH_KEY: {webapp._rate_key(one, hw=True): 250.0}}
    assert webapp._rate_key(four, hw=True) != webapp._rate_key(one, hw=True)
    assert api._encode_rate(four) == (250.0, "your benchmark")


def test_the_app_knows_whether_it_has_ever_been_benchmarked(monkeypatch, tmp_path):
    """What the first-run offer keys off: an app that has measured nothing should
    say so and offer to, not quietly quote constants as if they were the user's."""
    monkeypatch.setattr(webapp, "_settings_path", lambda: tmp_path / "s.json")
    api = webapp.Api.__new__(webapp.Api)
    api._adv = {}
    assert api.benchmark_state()["done"] is False
    api._adv[webapp._BENCH_META] = {"at": "2026-09-03T00:00:00", "rows": []}
    assert api.benchmark_state()["done"] is True
