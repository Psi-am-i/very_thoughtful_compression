"""Model tests — reproduce the numbers validated against the bash implementation.

Runnable two ways:  pytest tests/   |   python tests/test_model.py
"""

from __future__ import annotations

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vtc.model import (  # noqa: E402
    FPS_PRICE_ANCHOR,
    CodecCategory,
    OutCodec,
    Tier,
    classify_codec,
    over_target,
    priced_fps,
    source_bpp,
    target_kbps,
)

_1080P = (1920, 1080)
_4K = (3840, 2160)


def _approx(a: float, b: float, tol: float = 1e-3) -> bool:
    return math.isclose(a, b, rel_tol=0, abs_tol=tol)


def test_tier_bpp_anchors():
    assert _approx(Tier.OK.bpp, 0.0965)
    assert _approx(Tier.GOOD.bpp, 0.1286)
    assert _approx(Tier.EXCELLENT.bpp, 0.1688)
    assert _approx(Tier.STELLAR.bpp, 0.2090)
    assert _approx(Tier.INSANE.bpp, 0.2492)


def test_classify_codec_matches_bash():
    assert classify_codec("h264") is CodecCategory.H264
    assert classify_codec("AVC") is CodecCategory.H264
    assert classify_codec("hevc") is CodecCategory.MODERN
    assert classify_codec("av1") is CodecCategory.MODERN
    assert classify_codec("vp9") is CodecCategory.MODERN
    assert classify_codec("mpeg2video") is CodecCategory.LEGACY
    assert classify_codec("mpeg4") is CodecCategory.LEGACY  # Xvid/DivX
    assert classify_codec("wmv3") is CodecCategory.LEGACY
    assert classify_codec("prores") is CodecCategory.OTHER
    assert classify_codec(None) is CodecCategory.OTHER


def test_the_fps_anchor_is_where_the_bands_were_fitted():
    """At 24 fps the target is exactly what it always was, by construction.

    The tier's bpp is *defined* at 1080p/30 fps, but the CRF bands were *fitted* on
    clips whose median is 23.988 fps, so 24 is where the frame-rate curve has to pass
    through the old linear value — otherwise re-pricing frame rate would silently move
    the targets of the ~89% of a real library that sits at 24/25 fps. These two numbers
    are the ones that must not drift.
    """
    assert target_kbps(Tier.EXCELLENT, _1080P[0] * _1080P[1], 24, OutCodec.H264) == 8400
    assert target_kbps(Tier.EXCELLENT, _4K[0] * _4K[1], 24, OutCodec.H264) == 33600
    assert priced_fps(FPS_PRICE_ANCHOR) == FPS_PRICE_ANCHOR


def test_frame_rate_is_priced_on_a_curve_not_linearly():
    """A frame costs MORE bits at a lower frame rate — measured, see FPS_PRICE_EXPONENT.

    So a second of 60 fps video does not want twice the bits of a second of 30 fps.
    Above the anchor the target is tighter than the old linear one, below it looser,
    because that is what the measurement says in each direction.
    """
    assert priced_fps(60) < 60 and priced_fps(60) > priced_fps(30)
    assert priced_fps(12) > 12
    # Doubling the frame rate multiplies the target by ~1.25, not by 2.
    assert 1.20 < priced_fps(60) / priced_fps(30) < 1.30
    # Monotonic: more frames per second is never priced as fewer bits per second.
    rates = [priced_fps(f) for f in (12, 24, 25, 30, 50, 60, 120)]
    assert rates == sorted(rates)
    # The two figures priced_fps' own docstring quotes. Asserted because that docstring
    # is what a later session reads instead of recomputing, and it has been wrong once:
    # it said 33.6 for 60 fps, which is the superseded a=0.633, not the shipped 0.678.
    assert round(priced_fps(60), 1) == 32.2
    assert round(priced_fps(12), 1) == 19.2


def test_targets_1080p30():
    """1080p30 is the tier's DEFINITION point but no longer its nominal bitrate.

    EXCELLENT is "10.5 Mbps at 1080p30" as a definition of its density; priced on the
    measured frame-rate curve a 30 fps file asks for 0.86x that, because 30 fps frames
    are cheaper than the 24 fps frames the bands were fitted on.
    """
    px = _1080P[0] * _1080P[1]
    assert target_kbps(Tier.EXCELLENT, px, 30, OutCodec.H264) == 9025
    assert target_kbps(Tier.GOOD, px, 30, OutCodec.H264) == 6876
    assert target_kbps(Tier.INSANE, px, 30, OutCodec.H264) == 13323
    # H.265 @1080p uses the 0.60 HD factor
    assert target_kbps(Tier.EXCELLENT, px, 30, OutCodec.H265) == 5415
    # The ratio between codecs is untouched by the frame-rate change.
    assert target_kbps(Tier.EXCELLENT, px, 30, OutCodec.H265) == int(
        target_kbps(Tier.EXCELLENT, px, 30, OutCodec.H264) * 0.60)


def test_targets_scale_with_resolution_and_codec():
    px4k = _4K[0] * _4K[1]
    # 4K EXCELLENT H.265 via the 0.50 factor
    assert target_kbps(Tier.EXCELLENT, px4k, 30, OutCodec.H265) == 18051
    # Same tier, H.264, 4K, at the anchor frame rate
    assert target_kbps(Tier.EXCELLENT, px4k, 24, OutCodec.H264) == 33600
    # Pixels are still priced linearly — 4x the pixels is 4x the target at equal fps.
    assert target_kbps(Tier.EXCELLENT, px4k, 24, OutCodec.H264) == 4 * target_kbps(
        Tier.EXCELLENT, _1080P[0] * _1080P[1], 24, OutCodec.H264)


def test_convergence_gate():
    px = _1080P[0] * _1080P[1]
    tgt = target_kbps(Tier.EXCELLENT, px, 24, OutCodec.H264)  # 8400, at the fps anchor
    # The 3.5->2.2->1.2 staircase must NOT happen: only a source well over target encodes.
    assert over_target(14000, tgt) is True       # fat original -> encode once
    assert over_target(8400, tgt) is False       # at target -> leave alone
    assert over_target(8000, tgt) is False       # yesterday's mid-state -> leave alone
    assert over_target(3200, tgt) is False       # already lean -> leave alone
    # Just over the 10% tolerance boundary:
    assert over_target(int(8400 * 1.10) + 5, tgt) is True
    assert over_target(int(8400 * 1.10) - 5, tgt) is False


def test_never_inflate_source():
    px = _1080P[0] * _1080P[1]
    # A lean source: encode target is clamped to the source, never above it.
    assert target_kbps(Tier.EXCELLENT, px, 30, OutCodec.H264, src_kbps=5000) == 5000


def test_floor():
    # A tiny frame would compute below the floor; it is clamped up.
    assert target_kbps(Tier.OK, 160 * 120, 24, OutCodec.H265) == 1500


def test_source_bpp():
    px = _1080P[0] * _1080P[1]
    # 12.5 Mbps 1080p30 -> ~0.20 bpp (the fat test clip)
    assert _approx(source_bpp(12_500_000, px, 30), 0.2011, tol=1e-3)


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"  ok  {fn.__name__}")
    print(f"\n{len(fns)} model tests passed.")


if __name__ == "__main__":
    _run_all()
