"""Frame size — the walkthrough's "cap the library at 1080p" answer, end to end.

The cap is on HEIGHT (vertical pixels, the "p" in 1080p), the aspect ratio is
kept and nothing is ever upscaled. It is a QUALITY control, not a cosmetic one:
a tier is a bits-per-pixel density, so a smaller frame must earn a smaller
target — which is what makes the setting save space at the same quality. These
tests pin that arithmetic, the ffmpeg filter that carries it out, the ledger
signature that stops a re-cap being mistaken for work already done, and the
report line that says what actually happened.

Run:  python3 -m pytest tests/test_frame_size.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vtc import encode, pipeline, webapp  # noqa: E402
from vtc.config import Container, Encoder, OutputMode, RunConfig  # noqa: E402
from vtc.ffprobe import MediaInfo  # noqa: E402
from vtc.model import OutCodec, Tier, capped_dims  # noqa: E402
from vtc.result import Mode, Outcome  # noqa: E402


def _info(w=3840, h=2160, kbps=20000, vcodec="h264", fps=24.0, dur=3600.0):
    return MediaInfo(path=Path("/x/film.mp4"), ok=True, vcodec=vcodec,
                     width=w, height=h, fps=fps, bit_rate=int(kbps * 1000),
                     duration=dur, pix_fmt="yuv420p")


# ── capped_dims: the one piece of arithmetic everything else trusts ───────────
def test_cap_off_or_unknown_geometry_never_scales():
    assert capped_dims(3840, 2160, 0) is None          # cap off
    assert capped_dims(3840, 2160, -1) is None
    assert capped_dims(0, 0, 1080) is None             # geometry unknown: don't guess


def test_cap_never_upscales():
    assert capped_dims(1280, 720, 1080) is None        # shorter than the cap
    assert capped_dims(1920, 1080, 1080) is None       # exactly at it


def test_cap_scales_and_keeps_aspect():
    assert capped_dims(3840, 2160, 1080) == (1920, 1080)
    assert capped_dims(3840, 1600, 720) == (1728, 720)     # 2.40:1 scope, aspect held
    w, h = capped_dims(1920, 1080, 720)
    assert (w, h) == (1280, 720)
    assert abs(w / h - 1920 / 1080) < 0.01


def test_cap_and_scaled_width_are_always_even():
    # yuv420p subsamples chroma 2x2, so an odd dimension is rejected or padded.
    assert capped_dims(3840, 2160, 1081) == (1920, 1080)   # odd cap rounded down
    assert capped_dims(1920, 1080, 1081) is None           # …and then it doesn't bite
    for src_w in (1919, 1921, 1437):
        w, _ = capped_dims(src_w, 1080, 720)
        assert w % 2 == 0, src_w


# ── the target: priced against the frame we are about to write ───────────────
def test_target_scales_down_with_the_frame():
    """4K -> 1080p is a quarter of the pixels, so a quarter of the bitrate buys
    the same bits-per-pixel — the entire reason the setting saves space."""
    with tempfile.TemporaryDirectory() as d:
        info = _info(kbps=40000)          # fat enough to be worth encoding either way
        uncapped = RunConfig(src=Path(d))
        capped = RunConfig(src=Path(d), max_height=1080)
        _, _, t_full = pipeline.decide(uncapped, info)
        _, _, t_capped = pipeline.decide(capped, info)
        assert t_full > 0 and t_capped > 0
        # A quarter of the pixels, but 4K and HD carry different HEVC factors
        # (0.50 vs 0.60), so the ratio is 4 * 0.50/0.60 — not a flat 4.
        assert abs(t_full / t_capped - 4 * 0.50 / 0.60) < 0.02, (t_full, t_capped)


def test_cap_makes_an_at_tier_4k_file_worth_shrinking():
    """A 4K file sitting at its own tier target is left alone — until a 1080p cap
    reprices it, at which point it is far over target and worth the encode."""
    with tempfile.TemporaryDirectory() as d:
        info = _info(kbps=17000)                       # inside the 4K at-tier band
        mode, outcome, _ = pipeline.decide(RunConfig(src=Path(d)), info)
        assert mode is None and outcome is Outcome.SKIP_AT_TIER
        mode, outcome, target = pipeline.decide(
            RunConfig(src=Path(d), max_height=1080), info)
        assert mode is Mode.SHRINK and outcome is None and 0 < target < 17000


def test_a_lean_source_is_still_left_alone_under_a_cap():
    """A cap is not a licence to re-encode something that cannot get smaller: a
    4K file already below the 1080p target would only lose a generation."""
    with tempfile.TemporaryDirectory() as d:
        info = _info(kbps=900)
        mode, outcome, _ = pipeline.decide(RunConfig(src=Path(d), max_height=1080), info)
        assert mode is None and outcome is not None


def test_a_remux_is_never_rescaled():
    """REMUX is a stream copy. It cannot rescale however the cap is set, and the
    decision must not pretend otherwise."""
    with tempfile.TemporaryDirectory() as d:
        info = _info(w=3840, h=2160, kbps=2000, vcodec="hevc")
        info.path = Path("/x/film.mkv")
        cfg = RunConfig(src=Path(d), max_height=720)
        mode, _, _ = pipeline.decide(cfg, info)
        assert mode is Mode.REMUX
        assert encode.build_video_args(cfg, info, Mode.REMUX, 0, None)[:2] == ["-c:v", "copy"]


def test_legacy_rescue_target_shrinks_with_the_frame():
    """The legacy path targets 85% of source. Capped, it must scale with the
    pixels too — and must never be floored back up above the source."""
    with tempfile.TemporaryDirectory() as d:
        info = _info(vcodec="mpeg2video", kbps=20000)
        _, _, full = pipeline.decide(RunConfig(src=Path(d)), info)
        _, _, capped = pipeline.decide(RunConfig(src=Path(d), max_height=1080), info)
        assert full == 17000                                   # 85% of 20 Mbps
        assert abs(capped - full / 4) <= 1                     # a quarter of the frame
        # A small legacy file must not be inflated by the bitrate floor.
        small = _info(vcodec="mpeg2video", kbps=800)
        _, _, t = pipeline.decide(RunConfig(src=Path(d), max_height=1080), small)
        assert 0 < t < 800


# ── the ffmpeg side ──────────────────────────────────────────────────────────
def test_scale_filter_is_added_for_re_encodes_only():
    with tempfile.TemporaryDirectory() as d:
        info, cfg = _info(), RunConfig(src=Path(d), max_height=1080)
        args = encode.build_video_args(cfg, info, Mode.SHRINK, 2400, None)
        assert args[0] == "-vf" and args[1] == "scale=1920:1080:flags=lanczos"
        assert "libx265" in args
        # Hardware ABR path carries it too — the filter runs before the encoder.
        hw = encode.build_video_args(cfg, info, Mode.SHRINK, 2400, "hevc_videotoolbox")
        assert hw[:2] == ["-vf", "scale=1920:1080:flags=lanczos"]
        assert "hevc_videotoolbox" in hw
        # H.264 output likewise.
        c264 = RunConfig(src=Path(d), max_height=720, out_codec=OutCodec.H264)
        assert encode.build_video_args(c264, info, Mode.SHRINK, 2400, None)[:2] == [
            "-vf", "scale=1280:720:flags=lanczos"]


def test_no_filter_when_the_cap_does_not_bite():
    with tempfile.TemporaryDirectory() as d:
        info = _info(w=1280, h=720)
        for cfg in (RunConfig(src=Path(d)), RunConfig(src=Path(d), max_height=1080)):
            assert "-vf" not in encode.build_video_args(cfg, info, Mode.SHRINK, 2400, None)


# ── the ledger: a changed cap is not work already done ───────────────────────
def test_cap_is_part_of_the_ledger_signature():
    with tempfile.TemporaryDirectory() as d:
        plain = RunConfig(src=Path(d))
        at1080 = RunConfig(src=Path(d), max_height=1080)
        at720 = RunConfig(src=Path(d), max_height=720)
        # An uncapped run still matches ledgers written before frame size existed.
        assert "maxh" not in plain.settings_signature()
        assert at1080.settings_signature() != plain.settings_signature()
        assert at1080.settings_signature() != at720.settings_signature()


# ── the report: say what was actually written ────────────────────────────────
def test_detail_reports_the_output_frame_and_its_real_density():
    with tempfile.TemporaryDirectory() as d:
        info = _info()
        cfg = RunConfig(src=Path(d), max_height=1080)
        detail = pipeline._build_detail(
            cfg, info, Mode.SHRINK, 2400, Container.MP4, ".mp4", info.path,
            encode.EncodeResult(ok=True, out_path=info.path, out_bytes=1_000_000))
        assert (detail.width, detail.height) == (3840, 2160)
        assert (detail.out_width, detail.out_height) == (1920, 1080)
        # bpp against the frame written, not the one read (4x apart here).
        assert abs(detail.bpp - 2400 * 1000 / (1920 * 1080 * 24.0)) < 1e-6
        assert "2160p→1080p" in detail.caption()


def test_caption_stays_silent_when_the_frame_is_unchanged():
    with tempfile.TemporaryDirectory() as d:
        info = _info(w=1920, h=1080)
        detail = pipeline._build_detail(
            RunConfig(src=Path(d), max_height=1080), info, Mode.SHRINK, 2400,
            Container.MP4, ".mp4", info.path,
            encode.EncodeResult(ok=True, out_path=info.path, out_bytes=1_000_000))
        assert (detail.out_width, detail.out_height) == (1920, 1080)
        assert "→1080p" not in detail.caption()


# ── the GUI answer -> RunConfig mapping ──────────────────────────────────────
def test_walkthrough_answer_maps_to_a_height_cap():
    assert webapp._max_height({"resize": 0}, {}) == 0        # Leave alone
    assert webapp._max_height({"resize": 1}, {}) == 2160
    assert webapp._max_height({"resize": 2}, {}) == 1440
    assert webapp._max_height({"resize": 3}, {}) == 1080
    assert webapp._max_height({"resize": 4}, {}) == 720
    assert webapp._max_height({"resize": 5}, {"resizeCustom": 900}) == 900
    assert webapp._max_height({"resize": 5}, {"resizeCustom": "900"}) == 900


def test_missing_or_junk_answers_leave_the_frame_alone():
    """A session saved before the question existed must resume unchanged, not
    quietly start rescaling the library it is halfway through."""
    assert webapp._max_height({}, {}) == 0
    assert webapp._max_height({"resize": None}, {}) == 0
    assert webapp._max_height({"resize": "nonsense"}, {}) == 0
    assert webapp._max_height({"resize": 5}, {}) == 0              # custom, nothing typed
    assert webapp._max_height({"resize": 5}, {"resizeCustom": ""}) == 0
    assert webapp._max_height({"resize": 5}, {"resizeCustom": "abc"}) == 0
    assert webapp._max_height({"resize": 5}, {"resizeCustom": 12}) == 120     # clamped up
    assert webapp._max_height({"resize": 5}, {"resizeCustom": 99999}) == 8192  # clamped down
    assert webapp._max_height({"resize": 99}, {}) == 0              # unknown index


def test_build_config_threads_the_cap_and_the_preview_mirror_agrees():
    with tempfile.TemporaryDirectory() as d:
        src = Path(d)
        base = {"codec": 1, "quality": 2, "saving": 1, "encoder": 0, "dest": 0}
        assert webapp.build_config(src, {**base, "resize": 3}).max_height == 1080
        assert webapp.build_config(src, {**base}).max_height == 0
        # The answer beats the mirrored settings value, which exists only so the
        # tier previews encode at the frame size the run will produce.
        cfg = webapp.build_config(src, {**base, "resize": 4, "adv": {"resizeHeight": 2160}})
        assert cfg.max_height == 720
        # …and a path that only has the settings dict still gets the same cap.
        mirror = RunConfig(src=src)
        webapp._apply_advanced(mirror, {"resizeHeight": 720})
        assert mirror.max_height == 720


# ── rotated (portrait) sources ───────────────────────────────────────────────
# A phone shoots landscape and stamps a display matrix rather than rewriting the
# pixels, so a portrait clip is STORED 3840x2160 and WATCHED 2160x3840. ffmpeg
# autorotates before any -vf we add, so sizing the scale filter from the stored
# frame forces a portrait picture into a landscape one and squashes it. Caught
# only because someone asked what happens to vertical video.
def test_rotation_is_read_and_the_display_frame_derived():
    info = _info(w=3840, h=2160)
    assert (info.display_width, info.display_height) == (3840, 2160)   # unrotated
    assert not info.transposed
    for deg in (90, -90, 270, -270):
        info.rotation = deg
        assert info.transposed, deg
        assert (info.display_width, info.display_height) == (2160, 3840), deg
    for deg in (0, 180, -180, 360):
        info.rotation = deg
        assert not info.transposed, deg
        assert (info.display_width, info.display_height) == (3840, 2160), deg


def test_a_portrait_source_is_capped_on_what_you_watch():
    with tempfile.TemporaryDirectory() as d:
        cfg = RunConfig(src=Path(d), max_height=1080)
        info = _info(w=3840, h=2160)                     # stored landscape…
        info.rotation = 90                               # …watched portrait
        # 2160x3840 capped at 1080 rows keeps the aspect: 608x1080, NOT 1920x1080.
        args = encode.build_video_args(cfg, info, Mode.SHRINK, 2400, None)
        assert args[:2] == ["-vf", "scale=608:1080:flags=lanczos"], args[:2]
        w, h = 608, 1080
        assert abs(w / h - 2160 / 3840) < 0.01, "aspect ratio not preserved"


def test_a_short_landscape_file_is_not_capped_just_because_it_is_wide():
    """The mirror image: 3840 wide but only 2160 tall is untouched by a 2160 cap,
    and a stored-portrait file must not be judged on its stored long axis."""
    with tempfile.TemporaryDirectory() as d:
        cfg = RunConfig(src=Path(d), max_height=2160)
        info = _info(w=3840, h=2160)
        assert "-vf" not in encode.build_video_args(cfg, info, Mode.SHRINK, 2400, None)
        info.rotation = 90                               # watched 2160x3840
        assert encode.build_video_args(cfg, info, Mode.SHRINK, 2400, None)[:2] == [
            "-vf", "scale=1216:2160:flags=lanczos"]   # 1215 rounded to an even width


def test_the_report_describes_the_picture_not_the_storage():
    with tempfile.TemporaryDirectory() as d:
        info = _info(w=3840, h=2160)
        info.rotation = 90
        detail = pipeline._build_detail(
            RunConfig(src=Path(d), max_height=1080), info, Mode.SHRINK, 2400,
            Container.MP4, ".mp4", info.path,
            encode.EncodeResult(ok=True, out_path=info.path, out_bytes=1_000_000))
        assert (detail.width, detail.height) == (2160, 3840)      # as watched
        assert (detail.out_width, detail.out_height) == (608, 1080)
        assert "3840p→1080p" in detail.caption()
