"""The bitrate / quality model — the heart of the tool.

A tier is a fixed quality *density* (bits per pixel per frame), anchored to an
H.264 bitrate at 1080p/30fps. Targets scale with a file's actual pixels and frame
rate, so one tier covers SD -> 8K at any fps. The target is ABSOLUTE (a function
of the tier and the source's pixels/fps), never a fraction of the source's
current bitrate — which is what makes re-runs converge instead of shaving a file
smaller every pass.

See docs/quality-model.md for the full rationale. This module is pure (no I/O),
so it is trivially testable and shared by the CLI and the GUI.
"""

from __future__ import annotations

from enum import Enum

# ── Reference anchor: every tier's bpp is derived from H.264 @ 1080p / 30 fps ──
_REF_PIXELS = 1920 * 1080
_REF_FPS = 30
_REF_PIXEL_FPS = _REF_PIXELS * _REF_FPS

_PIXELS_1080P = 1920 * 1080
_PIXELS_4K = 3840 * 2160

# ── Tunables (mirror the bash constants; overridable by the caller) ───────────
BITRATE_FLOOR_KBPS = 1500          # never target below this (avoids garbage output)
TIER_OVER_TOLERANCE = 1.10         # re-encode only a source that is >10% over target

# ── How frame rate is priced ──────────────────────────────────────────────────
# The target used to be linear in fps, which asserts that a frame costs the same bits
# whatever the frame rate. MEASURED 2026-09-12 and false: a slower frame rate means
# bigger gaps between frames, larger residuals, and more bits per frame. So halving
# the frame rate does NOT halve the bits a second of video wants.
#
# bits/frame ~ fps**-a, so bitrate ~ fps**(1-a) = fps**FPS_PRICE_EXPONENT.
#
# ⚠️ THE ANCHOR IS NOT _REF_FPS, AND THAT IS DELIBERATE. The tier's bpp is *defined*
# arithmetically at 1080p/30fps, but the CRF bands were *fitted* on eight clips whose
# median is 23.988 fps — that is where the model is empirically calibrated, so that is
# where the curve has to pass through today's value. Anchoring at 30 instead would hand
# every 24 fps file (half the library) a 15% MORE generous target, loosening the bulk of
# the library while trying to tighten the tail. The anchor is the calibration point, not
# the definition point.
FPS_PRICE_ANCHOR = 24.0
#
# ⚠️ THIS CONSTANT IS SET AT THE HARDEST CASE, NOT THE AVERAGE, AND THE ASYMMETRY IS THE
# WHOLE REASON. `a` is not a constant of nature — it measures how much NEW information
# each extra frame carries, and it ran 0.678 (iPhone 4K60, 100% of frames distinct)
# to 1.047 (Mandy, 64%) and 1.041 (Fake or Fortune!, 25% — nominally 50 fps, carrying
# about 12 fps of actual content).
#
# ON THE SOFTWARE PATH the target is the -maxrate ceiling, and the two errors differ:
#   too generous -> CRF binds, the file lands at its natural rate, quality is the tier's.
#                   Cost: a file may stay under the gate and not be offered a shrink.
#   too tight    -> the ceiling binds and the file is squeezed below what its CRF asked
#                   for. Cost: quality, silently — a starved encode still probes valid,
#                   still matches play length, still is smaller, so nothing catches it.
# At the aggressive end (a=0.96) the all-distinct case is squeezed 32% below its CRF's
# wish. At a=0.678 it sits in the ordinary "dense source, ceiling-bound near target"
# regime the bands already intend.
#
# ⚠️ THAT ASYMMETRY DOES NOT EXIST ON THE DEFAULT PATH, AND THIS CONSTANT IS NOT
# PROTECTED BY IT THERE. `encoder` defaults to AUTO, i.e. hardware where available —
# VideoToolbox on any Mac — and hardware encoders have no CRF: _hw_video_args emits
# -b:v = the target, so THE TARGET IS THE DELIVERED BITRATE, not a ceiling something
# else may sit under. See the note in _hw_video_args: "the tier bitrate is the whole
# quality knob". There this exponent sets a 60 fps file's bitrate to 0.537x of what it
# used to get, flatly, with no CRF underneath to catch an over-tight number — both
# directions cost something and only the exponent being right protects it.
# So: the hardest case sets the number because on software it buys a real margin and on
# hardware it is simply the least aggressive reading of the measurement.
# The saving this declines to chase is real and measured (Fake or Fortune! at OK would go
# 3658 -> ~948 kbps) and is why the per-file novelty probe is on the roadmap — see
# docs/FUTURE-VERSIONS.md. Until then, under-claiming is the side to be wrong on.
#
# Measured on a 30s sample (the minimum; a shorter clip is one 250-frame keyint and the
# slower arm then carries double the I-frame share, which inflated an earlier 25s run to
# a=0.642). Harness: tools/calibration/fps_term_hardest.py.
FPS_PRICE_EXPONENT = 1.0 - 0.678   # bitrate ~ fps**0.322


def priced_fps(fps: float) -> float:
    """The frame rate as the target should price it, not as the file plays it.

    Identity at the anchor, below it above the line and above it below — a 60 fps file
    is priced as 32.2 rather than 60, a 12 fps file as 19.2 rather than 12. NOTE that
    this is only ever a bitrate calculation: VTC does not change a file's frame rate,
    which stays as the source's.
    """
    if fps <= 0:
        fps = float(_REF_FPS)
    return FPS_PRICE_ANCHOR * (fps / FPS_PRICE_ANCHOR) ** FPS_PRICE_EXPONENT

# H.265 bitrate vs H.264 at equal quality, by OUTPUT resolution. The HEVC
# advantage grows with frame size (validated against coding-efficiency studies:
# theoretical ~50%, practical ~25-40% at HD; 0.60 at HD hands H.265 more bitrate
# than the theoretical 50% to protect quality on a generic encoder).
HEVC_FACTOR_HD = 0.60              # <= 1080p  (40% saving)
HEVC_FACTOR_4K = 0.50              # <= 4K     (50% saving)
HEVC_FACTOR_8K = 0.45              # above 4K  (55% saving)

# AV1 the same way — bitrate vs H.264 at equal quality, by OUTPUT resolution.
# Set at 25% below the H.265 factors, which is the conservative end of the
# published 20-40% BD-rate advantage of SVT-AV1 over x265, and is only honest at
# a preset that earns it (see AV1_PRESET in encode.py — a fast preset gives that
# advantage back, so the two numbers have to move together).
#
# ⚠️ THESE ARE DERIVED FROM THE H.265 FACTORS, NOT MEASURED, AND THE ARITHMETIC
# SHOWS IT. Divide them through: 0.45/0.60 = 0.750, 0.38/0.50 = 0.760,
# 0.34/0.45 = 0.756. AV1's curve is H.265's scaled by a constant, so the model
# asserts AV1 needs ~25% fewer bits than H.265 AT EVERY RESOLUTION and contains no
# claim about resolution dependence at all — while "AV1 pulls ahead at 4K" is
# exactly the hypothesis anyone would want to test. Whatever replaces these has to
# be free to bend, not just to move; a single new constant would re-commit the
# same structural assumption.
#
# And it is TWO unmeasured hops, not one. The factors multiply the H.264 target,
# so a tier only means the same quality across codecs if H.264->H.265 AND
# H.265->AV1 are both right. The first is itself in doubt (see docs/
# measuring-quality.md §8: H.265 given exactly its prescribed 60% ranked BELOW
# H.264 by eye), so measuring the AV1 ratio alone would calibrate against an
# unvalidated anchor.
AV1_FACTOR_HD = 0.45               # <= 1080p  (55% saving vs H.264)
AV1_FACTOR_4K = 0.38               # <= 4K     (62% saving)
AV1_FACTOR_8K = 0.34               # above 4K  (66% saving)


class OutCodec(str, Enum):
    H264 = "h264"
    H265 = "h265"
    AV1 = "av1"


class CodecCategory(str, Enum):
    H264 = "h264"        # core target: shrink if fat, else remux/leave
    MODERN = "modern"    # HEVC/AV1/VP9 — never transcoded, only remuxed into MP4
    LEGACY = "legacy"    # MP4-incompatible (MPEG-2/VC-1/Xvid/WMV) — transcode opt-in
    OTHER = "other"      # mezzanine/unknown (ProRes/DNxHD/FFV1/raw) — left untouched


class Tier(Enum):
    """Quality tiers, anchored to an H.264 bitrate (Mbps) at 1080p / 30fps."""

    # Raised so INSANE is essentially transparent (visually indistinguishable from
    # source on demanding footage) and the rest cascade down from it.
    OK = ("OK", 6.0)
    GOOD = ("GOOD", 8.0)
    EXCELLENT = ("EXCELLENT", 10.5)
    STELLAR = ("STELLAR", 13.0)
    INSANE = ("INSANE", 15.5)

    def __init__(self, label: str, ref_mbps: float) -> None:
        self.label = label
        self.ref_mbps = ref_mbps

    @property
    def bpp(self) -> float:
        """H.264 bits-per-pixel-per-frame implied by the 1080p30 anchor."""
        return self.ref_mbps * 1e6 / _REF_PIXEL_FPS

    @classmethod
    def from_name(cls, name: str) -> "Tier":
        try:
            return cls[name.strip().upper()]
        except KeyError:
            names = ", ".join(t.name for t in cls)
            raise ValueError(f"unknown tier {name!r} (choose from: {names})") from None


# Codec-name -> category. Mirrors classify_codec() in the bash script exactly.
_H264_CODECS = {"h264", "avc"}
_MODERN_CODECS = {"hevc", "av1", "vp9"}
_LEGACY_CODECS = {
    "mpeg2video", "mpeg4", "msmpeg4v1", "msmpeg4v2", "msmpeg4v3", "msmpeg4",
    "vc1", "wmv1", "wmv2", "wmv3", "flv1", "rv30", "rv40",
}


def classify_codec(codec_name: str | None) -> CodecCategory:
    """Sort a probed video codec name into a handling category."""
    c = (codec_name or "").strip().lower()
    if c in _H264_CODECS:
        return CodecCategory.H264
    if c in _MODERN_CODECS:
        return CodecCategory.MODERN
    if c in _LEGACY_CODECS:
        return CodecCategory.LEGACY
    return CodecCategory.OTHER


def capped_dims(width: int, height: int, max_short_edge: int) -> tuple[int, int] | None:
    """Output (width, height) when a frame-size cap actually bites, else None.

    The cap is on the frame's SHORT EDGE, which is what "1080p" names. For any
    landscape source — every film and TV episode — the short edge IS the height,
    so this is exactly "cap the rows of the frame". For a portrait source it is
    the width instead, because a phone video 1080 across is what everyone calls
    1080p; measuring its 1920 rows against a 1080 cap would squeeze it to 608
    wide, which is not what the setting promises. The aspect ratio is always
    preserved, and `width`/`height` are DISPLAY dimensions (see
    MediaInfo.display_width) — a rotated file is judged on the picture, not on
    the axis it happens to be stored along.

    None means "encode the frame exactly as it is": the cap is off, the geometry
    is unknown, or the source is already at or below it. Nothing is ever
    upscaled, so a cap can only ever remove pixels.

    Both returned dimensions are EVEN. H.264/H.265 in yuv420p subsample chroma
    2x2, so an odd dimension is either rejected outright or quietly padded — and
    an odd *cap* (a hand-typed 1081) would take the encoder with it, which is why
    the cap itself is rounded down before anything is scaled to it.

    Both axes scale by the same factor and the sample aspect ratio is passed
    through untouched, so an anamorphic source keeps its display shape.
    """
    if max_short_edge <= 0 or width <= 0 or height <= 0:
        return None
    cap = max_short_edge - (max_short_edge % 2)
    if cap < 2 or min(width, height) <= cap:
        return None
    def _even(n: float) -> int:
        return max(2, int(round(n / 2)) * 2)
    if height <= width:                               # landscape (and square)
        return (_even(width * cap / height), cap)
    return (cap, _even(height * cap / width))         # portrait: the width is capped


def hevc_factor(pixels: int, factors: tuple[float, float, float] | None = None) -> float:
    """H.265 efficiency factor for an output frame of `pixels` (w*h).

    `factors` overrides the (HD, 4K, 8K+) defaults — how the Advanced settings'
    three HEVC-factor boxes reach the arithmetic.
    """
    hd, uhd4k, uhd8k = factors or (HEVC_FACTOR_HD, HEVC_FACTOR_4K, HEVC_FACTOR_8K)
    if pixels <= _PIXELS_1080P:
        return hd
    if pixels <= _PIXELS_4K:
        return uhd4k
    return uhd8k


def av1_factor(pixels: int, factors: tuple[float, float, float] | None = None) -> float:
    """AV1 efficiency factor for an output frame of `pixels` (w*h)."""
    hd, uhd4k, uhd8k = factors or (AV1_FACTOR_HD, AV1_FACTOR_4K, AV1_FACTOR_8K)
    if pixels <= _PIXELS_1080P:
        return hd
    if pixels <= _PIXELS_4K:
        return uhd4k
    return uhd8k


def codec_factor(out_codec: OutCodec, pixels: int,
                 hevc: tuple[float, float, float] | None = None,
                 av1: tuple[float, float, float] | None = None) -> float:
    """Multiplier applied to the H.264 target for the chosen output codec."""
    if out_codec == OutCodec.H265:
        return hevc_factor(pixels, hevc)
    if out_codec == OutCodec.AV1:
        return av1_factor(pixels, av1)
    return 1.0


def source_bpp(src_bps: float, pixels: int, fps: float) -> float:
    """Bits per pixel per frame of a source (the density metric)."""
    if pixels <= 0 or fps <= 0:
        return 0.0
    return src_bps / (pixels * fps)


def target_kbps(
    tier: Tier,
    pixels: int,
    fps: float,
    out_codec: OutCodec,
    src_kbps: float | None = None,
    floor_kbps: int = BITRATE_FLOOR_KBPS,
    bpp: float | None = None,
    hevc: tuple[float, float, float] | None = None,
    av1: tuple[float, float, float] | None = None,
) -> int:
    """Absolute target bitrate (kbps) for a file at this resolution/fps/codec.

    target = tier_bpp * pixels * priced_fps(fps) * codec_factor, clamped to the floor
    and never above the source (we don't inflate). Returns an integer kbps.
    `priced_fps` is NOT `fps` — frame rate is priced on a measured curve rather than
    linearly; see FPS_PRICE_EXPONENT above.

    `bpp` overrides the tier's own density — that is how a user-edited tier (see
    RunConfig.tier_bpp) reaches the arithmetic without mutating the shared enum.
    """
    fps = fps if fps > 0 else float(_REF_FPS)
    density = tier.bpp if bpp is None else bpp
    raw = density * pixels * priced_fps(fps) * codec_factor(out_codec, pixels, hevc, av1) / 1000.0
    target = max(float(floor_kbps), raw)
    if src_kbps is not None and src_kbps > 0:
        target = min(target, src_kbps)
    return int(target)


def over_target(src_kbps: float, target_kbps_: int, tolerance: float = TIER_OVER_TOLERANCE) -> bool:
    """True if the source is far enough over target to be worth re-encoding.

    This is the convergence gate: a file at or within `tolerance` of its tier
    target is left alone, so re-runs don't keep shaving it down.
    """
    if target_kbps_ <= 0:
        return False
    return src_kbps > target_kbps_ * tolerance
