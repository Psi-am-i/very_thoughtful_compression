"""Tests for the abandoned-scratch sweep.

An encode writes to a temp in TMPROOT and is only moved into place once it
succeeds. run_file unlinks that temp on every path it controls — but a
force-quit, crash or power cut takes the process out before any of them run, and
nothing used to collect what was left. Interrupted runs therefore leaked a
part-encoded file, often several GB, into scratch permanently.

The sweep runs at app startup and is deliberately age-based rather than pid-based:
ffmpeg writes its output continuously, so anything an encode is still working on
has a fresh mtime — including a SECOND app instance's temp, which the sweep must
not delete out from under it.

Pure filesystem, no ffmpeg needed.

Run:  python tests/test_scratch_sweep.py   |   pytest tests/test_scratch_sweep.py
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vtc import pipeline  # noqa: E402


def _aged(path: Path, seconds_old: float) -> Path:
    path.write_text("x")
    os.utime(path, (time.time() - seconds_old, time.time() - seconds_old))
    return path


def test_sweep_takes_abandoned_temps_and_spares_live_ones(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "TMPROOT", tmp_path)
    abandoned = _aged(tmp_path / ".Some Movie (2019).4242.beef.mp4", 48 * 3600)
    # Another instance mid-encode: ffmpeg keeps its mtime current.
    in_flight = _aged(tmp_path / ".Other Movie (2021).999.f00d.mkv", 30)

    assert pipeline.sweep_stale_scratch() == 1
    assert not abandoned.exists()
    assert in_flight.exists(), "a temp still being written must survive the sweep"


def test_sweep_is_quiet_when_scratch_is_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "TMPROOT", tmp_path / "never-created")
    assert pipeline.sweep_stale_scratch() == 0


def test_sweep_leaves_directories_alone(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "TMPROOT", tmp_path)
    sub = tmp_path / "a-directory"
    sub.mkdir()
    os.utime(sub, (time.time() - 72 * 3600,) * 2)
    assert pipeline.sweep_stale_scratch() == 0
    assert sub.is_dir()


def test_filenames_with_dots_survive_the_round_trip(tmp_path, monkeypatch):
    """Real library names are full of dots, so the sweep must not try to parse them."""
    monkeypatch.setattr(pipeline, "TMPROOT", tmp_path)
    dotted = _aged(tmp_path / ".Movie.2019.1080p.BluRay.x264-GRP.777.a1b2.mp4", 48 * 3600)
    assert pipeline.sweep_stale_scratch() == 1
    assert not dotted.exists()


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
