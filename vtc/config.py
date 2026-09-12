"""Run configuration — the UI-agnostic settings object.

The CLI builds a RunConfig from argparse; the GUI will build the same object from
widgets. Nothing here does I/O; `pipeline` consumes it.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from .model import (
    AV1_FACTOR_4K,
    AV1_FACTOR_8K,
    AV1_FACTOR_HD,
    BITRATE_FLOOR_KBPS,
    FPS_PRICE_EXPONENT,
    HEVC_FACTOR_4K,
    HEVC_FACTOR_8K,
    HEVC_FACTOR_HD,
    TIER_OVER_TOLERANCE,
    OutCodec,
    Tier,
)

VIDEO_EXTS = ("mkv", "mp4", "mov", "avi", "webm", "m4v", "ts", "wmv", "flv")


class OutputMode(str, Enum):
    INPLACE = "inplace"        # replace the source in place
    SEPARATE = "separate"      # write to a separate folder


class SourceAction(str, Enum):
    ARCHIVE = "archive"        # move original to an archive folder
    DELETE = "delete"          # remove original after a successful replace
    KEEP = "keep"              # leave the original where it is


class Encoder(str, Enum):
    AUTO = "auto"              # hardware if available, else software
    HARDWARE = "hardware"      # VideoToolbox (macOS) / other HW where wired up
    SOFTWARE = "software"      # libx264 / libx265 capped-CRF


class AudioPolicy(str, Enum):
    PASSTHROUGH = "passthrough"   # copy tracks; convert only if the container can't hold them
    AAC = "aac"                   # re-encode all audio to AAC
    AC3 = "ac3"                   # re-encode to AC-3 (Dolby Digital) — home-theatre multichannel
    FLAC = "flac"                 # lossless — forces MKV


class Container(str, Enum):
    AUTO = "auto"   # MP4, but MKV when it must (lossless audio, image subs to keep, many tracks)
    MP4 = "mp4"
    MKV = "mkv"


# Audio codecs an MP4 container carries cleanly (others get converted on passthrough).
MP4_AUDIO_CODECS = {"aac", "ac3", "eac3", "mp3", "alac", "mp4als"}


@dataclass
class RunConfig:
    src: Path

    # Quality
    out_codec: OutCodec = OutCodec.H265
    tier: Tier = Tier.EXCELLENT
    min_saving_ratio: float = 0.75          # keep a shrink only if output <= this * source

    # Frame size. A cap on the OUTPUT HEIGHT in vertical pixels (0 = off), with
    # the aspect ratio preserved and nothing ever upscaled — a source already at
    # or below the cap is encoded at its own size. It is a QUALITY setting, not a
    # cosmetic one: a tier is a bits-per-pixel density, so a smaller frame earns a
    # proportionally smaller target and the same quality costs far fewer bytes.
    max_short_edge: int = 0

    # Compatibility / non-MP4 policy
    remux_to_mp4: bool = True               # rehome MP4-friendly codecs into MP4 losslessly
    compat_transcode: bool = True           # transcode MP4-incompatible legacy codecs
    keep_source_container: bool = False     # shrink non-MP4 files but KEEP their container
    leave_non_mp4: bool = False             # don't touch non-MP4 containers at all

    # Subtitles: which tracks survive. Two INDEPENDENT filters, applied together,
    # so "English forced subs" is expressible (the old single sub_mode of
    # all|forced|hoh|lang made language and kind mutually exclusive).
    #   sub_langs — ISO codes to keep; empty means every language
    #   sub_kinds — any of "normal" / "forced" / "hoh"; empty means every kind
    sub_langs: tuple[str, ...] = ()
    sub_kinds: tuple[str, ...] = ()

    # Audio & container
    audio_policy: AudioPolicy = AudioPolicy.PASSTHROUGH
    audio_bitrate_stereo: int = 256         # kbps, AAC/AC-3, <=2 channels
    audio_bitrate_multichannel: int = 448   # kbps, AAC/AC-3, >2 channels
    container: Container = Container.AUTO
    keep_image_subs: bool = True            # prefer MKV over dropping PGS/DVD subtitle tracks
    keep_mkv_for_audio: bool = True         # prefer MKV over a lossy AAC conversion when the source
                                            #   audio (DTS/TrueHD/PCM) can't go into MP4 cleanly
    mkv_if_text_subs: bool = False          # "avoid sidecar .srt": force MKV only when a file has a
                                            #   subtitle MP4 can't embed (so it isn't dropped/sidecar'd)
    mkv_if_tracks_over: int = 0             # 0 = off; force MKV when audio+sub tracks exceed this

    # Re-encoding something this tool already encoded costs a second lossy
    # generation, which nothing can give back. Off by default: it is a decision
    # worth making on purpose, not by not noticing. (A remux does not count —
    # that is a stream copy.)
    allow_second_generation: bool = False

    # ── Bloated modern sources (H.265 / VP9 / AV1) ───────────────────────────
    # Normally these are never re-encoded: they are already efficient, so the
    # usual result is a second lossy generation for very little space. But a bad
    # hardware encoder at a silly bitrate is a real thing (drone and action-cam
    # footage especially), and those files ARE worth reclaiming. This opens the
    # door deliberately, and narrowly.
    reencode_modern: bool = False
    # Far stricter than tier_over_tolerance, and for a reason: at 2x over target
    # the win is ~50% and clearly worth the hours; at 1.2x you would spend a night
    # to save 15% and a generation of quality.
    modern_over_tolerance: float = 2.0
    # Which modern codecs are eligible. AV1 is deliberately absent: it is the most
    # efficient of the three (so a genuinely bloated AV1 file is rare), AV1 -> H.265
    # is usually an efficiency DOWNGRADE, and AV1 -> AV1 in software is punishing
    # with no VideoToolbox AV1 encoder on most machines. Add it on purpose or not
    # at all.
    modern_codecs: tuple[str, ...] = ("hevc", "vp9")
    # How many to actually do this run, worst-first by predicted saving. 0 = all.
    #
    # This is the USER'S answer to "142 files qualify and it will take 46 hours —
    # how many now?", not a safety default the engine picks for them: both front
    # ends put the count, the size and the time in front of them and ask. The
    # engine simply does what it is told. Whatever is left over is deferred rather
    # than dismissed (Outcome.DEFER_MODERN), so the next run continues down the
    # same worst-first list.
    modern_max_files: int = 0
    # The shortlist this run actually picked (resolved paths, like software_files).
    # Empty means "no shortlist in force" — the gate alone decides. pipeline.run()
    # fills it in when a budget applies.
    modern_files: frozenset[str] = frozenset()

    # Execution
    encoder: Encoder = Encoder.AUTO
    jobs: int = 1
    # Files the user picked out for the slow, better encoder, whatever `encoder`
    # says for the run as a whole. Resolved absolute paths as strings — a set, so
    # a 20,000-file library costs one hash lookup per file. Choosing the encoder
    # is a ONE-SHOT decision per file (the original is replaced), which is why it
    # is worth spending attention on the handful that deserve it.
    software_files: frozenset[str] = frozenset()

    # Destination
    output_mode: OutputMode = OutputMode.INPLACE
    output_dir: Path | None = None
    output_flat: bool = False               # separate mode: flatten vs mirror tree
    source_action: SourceAction = SourceAction.ARCHIVE
    archive_dir: Path | None = None         # defaults to <src>/originals

    # Resume ledger ("processing history")
    ledger_enabled: bool = True
    ledger_file: Path | None = None         # defaults to <src>/.vtc_processed.log

    # Ignore rules — files the scan pretends it never saw. They are filtered at
    # discovery, so an ignored file is absent from the count, the estimate, the
    # queue and the report alike; it is never probed, decided or touched.
    #   0 / empty = that rule is off.
    ignore_under_bytes: int = 0             # skip files SMALLER than this
    ignore_over_bytes: int = 0              # skip files LARGER than this
    ignore_exts: tuple[str, ...] = ()       # extensions to skip, with or without the dot
    ignore_name_contains: tuple[str, ...] = ()   # skip if the filename contains any of these

    # Tools
    ffmpeg: str = "ffmpeg"
    ffprobe: str = "ffprobe"

    # Tunables (defaults mirror the model / bash)
    # tier_bpp: per-tier density overrides, keyed by Tier.name ("EXCELLENT" -> 0.12).
    # Empty means "use the tier's built-in bpp"; a tier absent from the dict keeps
    # its default, so one edited tier doesn't drag the others with it.
    tier_bpp: dict[str, float] = field(default_factory=dict)
    bitrate_floor_kbps: int = BITRATE_FLOOR_KBPS
    tier_over_tolerance: float = TIER_OVER_TOLERANCE
    hevc_factor_hd: float = HEVC_FACTOR_HD
    hevc_factor_4k: float = HEVC_FACTOR_4K
    hevc_factor_8k: float = HEVC_FACTOR_8K
    av1_factor_hd: float = AV1_FACTOR_HD
    av1_factor_4k: float = AV1_FACTOR_4K
    av1_factor_8k: float = AV1_FACTOR_8K

    video_exts: tuple[str, ...] = VIDEO_EXTS

    def bpp_for(self, tier: Tier | None = None) -> float:
        """The quality density actually in force for `tier` (default: the run's).

        A user override from Advanced settings wins; anything non-positive or
        unparseable falls back to the tier's own anchored bpp.
        """
        t = tier or self.tier
        try:
            v = float(self.tier_bpp.get(t.name, 0.0))
        except (TypeError, ValueError):
            return t.bpp
        return v if v > 0 else t.bpp

    def forces_software(self, path: Path) -> bool:
        """True if this particular file was picked out for the software encoder.

        Matched on the resolved path so a relative/symlinked scan can't miss it.
        Never raises — an unresolvable path simply isn't a match.
        """
        if not self.software_files:
            return False
        try:
            return str(path.resolve()) in self.software_files
        except OSError:
            return str(path) in self.software_files

    def modern_eligible(self, vcodec: str | None) -> bool:
        """True if a bloated source in this codec may be re-encoded at all.

        Both the master switch and the per-codec list must agree — turning the
        feature on must not silently pull in AV1, which is on nobody's list of
        codecs worth spending a night re-encoding.
        """
        if not self.reencode_modern:
            return False
        return (vcodec or "").strip().lower() in {c.lower() for c in self.modern_codecs}

    def picked_for_modern(self, path: Path) -> bool:
        """True if this file is on the run's modern shortlist (or there isn't one).

        An EMPTY shortlist means no budget was applied, not "nothing allowed" —
        the gate in decide() is then the only thing standing in the way.
        """
        if not self.modern_files:
            return True
        try:
            return str(path.resolve()) in self.modern_files
        except OSError:
            return str(path) in self.modern_files

    def hevc_factors(self) -> tuple[float, float, float]:
        """The (HD, 4K, 8K+) H.265 efficiency factors this run should use."""
        return (self.hevc_factor_hd, self.hevc_factor_4k, self.hevc_factor_8k)

    def av1_factors(self) -> tuple[float, float, float]:
        """The (HD, 4K, 8K+) AV1 efficiency factors this run should use."""
        return (self.av1_factor_hd, self.av1_factor_4k, self.av1_factor_8k)

    def ignore_reason(self, name: str, size: int | None = None) -> str | None:
        """Why this file is ignored, or None to process it.

        `name` is the filename (not the whole path — a rule matching a parent
        directory's name would ignore whole trees by accident). `size` may be
        None when it could not be stat()ed, in which case the size rules simply
        don't apply rather than guessing.
        """
        low = name.lower()
        if size is not None:
            if self.ignore_under_bytes > 0 and size < self.ignore_under_bytes:
                return "smaller than the ignore-under size"
            if self.ignore_over_bytes > 0 and size > self.ignore_over_bytes:
                return "larger than the ignore-over size"
        if self.ignore_exts:
            ext = Path(low).suffix.lstrip(".")
            for raw in self.ignore_exts:
                if ext and ext == str(raw).strip().lstrip(".").lower():
                    return f"extension .{ext} is on the ignore list"
        for frag in self.ignore_name_contains:
            f = str(frag).strip().lower()
            if f and f in low:
                return f"filename contains {frag!r}"
        return None

    @property
    def has_ignore_rules(self) -> bool:
        return bool(self.ignore_under_bytes or self.ignore_over_bytes
                    or self.ignore_exts or self.ignore_name_contains)

    @property
    def needs_size_to_ignore(self) -> bool:
        """True when a rule depends on file size (so the scan must stat)."""
        return bool(self.ignore_under_bytes or self.ignore_over_bytes)

    def resolved_archive_dir(self) -> Path:
        return self.archive_dir or (self.src / "originals")

    def resolved_ledger_file(self) -> Path | None:
        if not self.ledger_enabled:
            return None
        return self.ledger_file or (self.src / ".vtc_processed.log")

    def settings_signature(self) -> str:
        """Ledger signature — a change in any of these re-evaluates every file."""
        parts = [
            self.tier.name,
            self.out_codec.value,
            f"rmx{int(self.remux_to_mp4)}",
            f"xc{int(self.compat_transcode)}",
            self.output_mode.value,
        ]
        # A retuned tier means a different target for every file, so history from
        # the old density must not count as done. Appended ONLY when the tier is
        # actually overridden, so a default run still matches ledgers written
        # before per-tier bpp existed.
        bpp = self.bpp_for()
        if abs(bpp - self.tier.bpp) > 1e-9:
            parts.append(f"bpp{bpp:.5f}")
        # The encoder changes the OUTPUT, not the decision. This is NOT about
        # re-encoding a file we already replaced — that would be generation loss,
        # and the engine refuses it anyway: our own output is HEVC, which classifies
        # as MODERN and is never transcoded (verified with the ledger disabled).
        #
        # It matters in the one mode where the SOURCES SURVIVE: separate output
        # directory + KEEP originals. There, deleting the output folder and running
        # again with the other encoder is a legitimate redo from intact sources —
        # and without this it was silently refused as "already done".
        # Appended only for an explicit choice, so a default (AUTO) run still
        # matches ledgers written before this existed.
        if self.encoder is not Encoder.AUTO:
            parts.append(f"enc{self.encoder.value}")
        # A frame-size cap changes the OUTPUT, and changing the cap must re-evaluate
        # the library: a file finished at 1080p is NOT done for a later 720p run, and
        # a file left alone at 4K becomes a candidate the moment a cap appears.
        # Appended only when a cap is set, so an uncapped run still matches ledgers
        # written before frame size existed.
        if self.max_short_edge > 0:
            parts.append(f"maxh{self.max_short_edge}")
        # Turning modern re-encoding on (or loosening its bar) makes candidates of
        # files every previous run recorded as "modern, left alone". They have to be
        # re-evaluated or the option would appear to do nothing on a library that
        # has been scanned before. Appended only when it is on.
        if self.reencode_modern:
            parts.append(f"mod{self.modern_over_tolerance:.2f}"
                         f"+{'.'.join(sorted(c.lower() for c in self.modern_codecs))}")
        # The TARGET FORMULA ITSELF is an input to every decision, so a change to it
        # has to invalidate history — otherwise the change cannot reach the library it
        # was measured on. A file judged "already at tier" is written to the ledger as
        # done (pipeline.py), and its key is signature+path+size+mtime; a file that was
        # LEFT ALONE has identical size and mtime, so on the next run ledger.has() hits
        # and returns RESUME before the file is even probed. The population a re-priced
        # fps term exists to reach — the high-frame-rate files previously skipped as
        # at-tier — is exactly the population that would never be re-evaluated.
        #
        # ⚠️ UNCONDITIONAL, unlike the three tokens above. Those are appended only when
        # the setting is explicitly chosen, precisely so a default run still matches
        # ledgers written before the setting existed. Here that reasoning inverts:
        # invalidating older ledgers IS the point, and the cost is a re-probe of the
        # library, not a re-encode — only files genuinely over the new target encode.
        # Interpolating the constant rather than a hand-bumped version number means a
        # later re-fit of the exponent invalidates history on its own, without anyone
        # having to remember to.
        parts.append(f"fps{FPS_PRICE_EXPONENT:.3f}")
        return "|".join(parts)

    def validate(self) -> list[str]:
        """Return a list of human-readable problems (empty = ok)."""
        errs: list[str] = []
        if not self.src.is_dir():
            errs.append(f"scan directory does not exist: {self.src}")
        if self.output_mode == OutputMode.SEPARATE and self.output_dir is None:
            errs.append("separate output mode requires output_dir")
        if self.jobs < 1:
            errs.append("jobs must be >= 1")
        if shutil.which(self.ffmpeg) is None:
            errs.append(f"ffmpeg not found: {self.ffmpeg!r}")
        if shutil.which(self.ffprobe) is None:
            errs.append(f"ffprobe not found: {self.ffprobe!r}")
        return errs
