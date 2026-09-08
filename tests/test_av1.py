"""AV1 output — the third codec, and the one with the sharpest trade-offs.

AV1 is the most efficient of the three and the least widely playable, and on
Apple silicon there is no hardware encoder at all (M3+ decodes AV1; nothing
Apple makes encodes it). So the interesting cases here are the ones where AV1
must NOT behave like H.265: its own efficiency factors, its own rate control,
and a hardware path that exists only on recent PC silicon.

Run:  python3 -m pytest tests/test_av1.py
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vtc import encode, pipeline  # noqa: E402
from vtc.config import Encoder, RunConfig, SourceAction  # noqa: E402
from vtc.ffprobe import MediaInfo, probe  # noqa: E402
from vtc.model import OutCodec, Tier, av1_factor, codec_factor, target_kbps  # noqa: E402
from vtc.result import Mode  # noqa: E402

_HAVE_FF = shutil.which("ffmpeg") and shutil.which("ffprobe")


def _info(w=1920, h=1080, fps=30.0, kbps=30000, dur=15.0, pix="yuv420p"):
    return MediaInfo(path=Path("/x/f.mp4"), ok=True, vcodec="h264", width=w, height=h,
                     fps=fps, bit_rate=int(kbps * 1000), duration=dur, pix_fmt=pix)


# ── the quality model ────────────────────────────────────────────────────────
def test_av1_gets_a_smaller_target_than_h265_which_gets_less_than_h264():
    """A tier is one quality; the codec decides what it costs. Ordering the three
    is the whole reason the factors exist — and AV1 must not silently inherit
    H.264's 1.0, which would over-provision it by more than double."""
    px = 1920 * 1080
    h264 = codec_factor(OutCodec.H264, px)
    h265 = codec_factor(OutCodec.H265, px)
    av1 = codec_factor(OutCodec.AV1, px)
    assert av1 < h265 < h264 == 1.0, (av1, h265, h264)


def test_the_av1_advantage_grows_with_frame_size_like_hevc_does():
    assert av1_factor(1280 * 720) == av1_factor(1920 * 1080)      # both "HD"
    assert av1_factor(3840 * 2160) < av1_factor(1920 * 1080)
    assert av1_factor(7680 * 4320) < av1_factor(3840 * 2160)


def test_av1_factors_are_overridable_like_the_hevc_ones():
    with tempfile.TemporaryDirectory() as d:
        cfg = RunConfig(src=Path(d), out_codec=OutCodec.AV1, av1_factor_hd=0.30)
        assert cfg.av1_factors()[0] == 0.30
        tuned = target_kbps(cfg.tier, 1920 * 1080, 30.0, OutCodec.AV1, av1=cfg.av1_factors())
        stock = target_kbps(cfg.tier, 1920 * 1080, 30.0, OutCodec.AV1)
        assert tuned < stock


def test_an_av1_target_actually_reaches_the_decision():
    """The factors are useless if decide() doesn't pass them through."""
    with tempfile.TemporaryDirectory() as d:
        info = _info()
        h265 = pipeline.decide(RunConfig(src=Path(d), out_codec=OutCodec.H265), info)[2]
        av1 = pipeline.decide(RunConfig(src=Path(d), out_codec=OutCodec.AV1), info)[2]
        assert 0 < av1 < h265, (av1, h265)


# ── rate control: AV1 is NOT capped-CRF ──────────────────────────────────────
def test_software_av1_uses_capped_crf_with_the_ceiling_asked_to_hold():
    """AV1 was on VBR because capped CRF "did not converge". It does — but only if
    --mbr-overshoot-pct is set, because SVT-AV1 defaults it to 50 and the ceiling is
    therefore specified to leak by half. Measured over 8 real sources x 5 tiers: at
    the default, 7 of 40 cases landed past the 1.10 gate (worst 1.32x); at 10, none
    (worst 0.97x). This is the regression guard for that default coming back."""
    with tempfile.TemporaryDirectory() as d:
        cfg = RunConfig(src=Path(d), out_codec=OutCodec.AV1)
        args = encode.build_video_args(cfg, _info(), Mode.SHRINK, 2400, None)
        assert "libsvtav1" in args
        assert "-crf" in args
        assert "-maxrate" in args and args[args.index("-maxrate") + 1] == "2400k"
        assert "-b:v" not in args, "VBR under-delivered the tier by ~25%"
        params = args[args.index("-svtav1-params") + 1]
        assert f"mbr-overshoot-pct={encode.AV1_MBR_OVERSHOOT_PCT}" in params
        # the psy string and the CRF band are one measurement — see AV1_PSY_PARAMS
        assert encode.AV1_PSY_PARAMS in params
        assert encode.AV1_MBR_OVERSHOOT_PCT < 50, "50 is the leaky encoder default"
        # -bufsize maps to SVT-AV1's --buf-sz, which is CBR-only: passing one here
        # would only make the argument list look like it were doing something.
        assert "-bufsize" not in args
        assert "-preset" in args


def test_the_av1_crf_band_is_its_own_measured_ladder():
    """AV1 must not borrow H.265's numbers. Its measured slope is 10.04 CRF per
    doubling of bitrate against x265's 5.21, so the same CRF means something
    different — and the tier has to reach the encoder as quality, not only as a
    ceiling, or every tier produces the same file."""
    seen = []
    for tier in Tier:
        _, crf265, crf_av1 = encode.crf_for_tier(tier, Mode.SHRINK)
        seen.append(crf_av1)
    assert all(b < a for a, b in zip(seen, seen[1:])), f"not monotonic: {seen}"
    assert seen[0] - seen[-1] >= 5, f"ladder too narrow: {seen}"


def test_h265_still_uses_capped_crf():
    """The AV1 change must not have moved the H.26x paths."""
    with tempfile.TemporaryDirectory() as d:
        args = encode.build_video_args(RunConfig(src=Path(d), out_codec=OutCodec.H265),
                                       _info(), Mode.SHRINK, 2400, None)
        assert "libx265" in args and "-crf" in args and "-maxrate" in args


def test_a_10bit_source_stays_10bit_through_av1():
    """AV1 Main covers 8 and 10 bit, so there is no reason to flatten a 10-bit
    master to 8 the way an H.264 output must."""
    with tempfile.TemporaryDirectory() as d:
        cfg = RunConfig(src=Path(d), out_codec=OutCodec.AV1)
        args = encode.build_video_args(cfg, _info(pix="yuv420p10le"), Mode.SHRINK, 2400, None)
        assert "yuv420p10le" in args


# ── hardware: PC-only, and probed rather than assumed ────────────────────────
def test_apple_is_not_offered_a_hardware_av1_encoder():
    """M3+ DECODES AV1; nothing Apple makes encodes it. Listing an
    av1_videotoolbox would make every Mac pay for a probe that can only fail."""
    assert not any("videotoolbox" in e for e in encode._HW_CANDIDATES[OutCodec.AV1])
    assert encode._HW_CANDIDATES[OutCodec.AV1] == ["av1_nvenc", "av1_qsv", "av1_amf"]


def test_hardware_av1_args_do_not_borrow_h26x_profiles():
    """AV1's profiles are not H.26x's — "high" means something else entirely, and
    passing it to av1_nvenc would be wrong rather than merely useless."""
    args = encode._hw_video_args(_info(), "av1_nvenc", 2400)
    assert args[:2] == ["-c:v", "av1_nvenc"]
    assert "-profile:v" not in args
    assert "-b:v" in args
    assert "-tag:v" not in args                      # hvc1 is an HEVC-in-MP4 thing


def test_missing_hardware_falls_through_to_software():
    """No AV1 card here, so this is the real path for every Mac: select_hw_encoder
    must return None rather than naming something that cannot encode."""
    if not _HAVE_FF:
        print("  skip test_missing_hardware_falls_through_to_software (no ffmpeg)")
        return
    with tempfile.TemporaryDirectory() as d:
        cfg = RunConfig(src=Path(d), out_codec=OutCodec.AV1, encoder=Encoder.HARDWARE)
        assert encode.select_hw_encoder(cfg) is None


def test_capabilities_report_av1_separately_from_the_hardware_flag():
    """`available` gates the UI's hardware/software question for the ordinary
    codecs. Folding AV1 into it would make that question appear or vanish for
    reasons unrelated to the codec actually chosen."""
    if not _HAVE_FF:
        print("  skip test_capabilities_report_av1_separately (no ffmpeg)")
        return
    rep = encode.hardware_report("ffmpeg")
    assert "av1" in rep and "av1_software" in rep
    assert rep["available"] == bool(rep["h264"] or rep["h265"])


# ── the real thing ───────────────────────────────────────────────────────────
def test_a_real_av1_encode_converges():
    """The property the whole engine rests on: encode once, and the next run must
    leave the file alone instead of shaving it again."""
    if not _HAVE_FF or not encode._encoder_works("ffmpeg", encode.AV1_SOFTWARE):
        print("  skip test_a_real_av1_encode_converges (no libsvtav1)")
        return
    with tempfile.TemporaryDirectory() as d:
        src = Path(d)
        # 20 seconds, not 5: SVT-AV1's rate control needs a moment to settle, and
        # the overshoot is a fixed start-up cost that amortises away. MEASURED at
        # a 2100 kbps target on this content: 6s -> 1.19x over, 15s -> 1.06x,
        # 30s -> 1.04x, 60s -> 1.01x, 120s -> 1.02x. Real files are minutes long,
        # so this never bites in practice — but it DOES mean a 5-second sample
        # overshoots badly, which is why the tier previews must not be read as a
        # fair size comparison for AV1 (see docs/quality-model.md).
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
                        "-i", "testsrc2=size=1280x720:rate=30", "-t", "20",
                        "-c:v", "libx264", "-b:v", "20000k", "-pix_fmt", "yuv420p",
                        str(src / "f.mp4")], check=True, stdin=subprocess.DEVNULL)
        cfg = lambda: RunConfig(src=src, out_codec=OutCodec.AV1, encoder=Encoder.SOFTWARE,
                                source_action=SourceAction.KEEP, ledger_enabled=False)
        first = pipeline.run(cfg())[0]
        assert first.outcome.value == "shrink", first.outcome
        out = probe(src / "f.mp4")
        assert out.vcodec == "av1", out.vcodec
        # …and the produced bitrate must land inside the convergence gate
        assert out.effective_bps / 1000 <= first.detail.vid_kbps * 1.10, (
            "AV1 overshot its target — re-runs would never converge")
        assert pipeline.run(cfg())[0].outcome.value.startswith("skip")


def test_av1_asks_for_the_psychovisual_layer_svt_ships_disabled():
    """SVT-AV1 ships its whole Psychovisual Options section off while x264/x265
    enable psy-rd by default, so default-settings AV1 is not a like-for-like
    comparison. --enable-variance-boost is an ADDITIONAL layer on top of the
    aq-mode 2 SVT-AV1 already runs, and it won a blind matched-bitrate panel.

    Guards two things a future edit could break silently: that the flag is still
    asked for, and that it is left at its documented defaults — a hand-tuned
    variant won an earlier panel and is now unreproducible because its strengths
    were never written down (docs/measuring-quality.md §5)."""
    with tempfile.TemporaryDirectory() as d:
        cfg = RunConfig(src=Path(d), out_codec=OutCodec.AV1)
        args = encode.build_video_args(cfg, _info(), Mode.SHRINK, 2400, None)
        params = args[args.index("-svtav1-params") + 1]
        assert "enable-variance-boost=1" in params
        for pinned in ("variance-boost-strength", "variance-boost-curve",
                       "luminance-qp-bias", "sharpness"):
            assert pinned not in params, f"{pinned} set without a re-fitted band"
    print("  ok  av1 asks for variance boost, at documented defaults")
