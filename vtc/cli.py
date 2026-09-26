"""Command-line front-end — the parity replacement for the bash script.

Three ways to drive it:
  * flags:        vtc /media/tv --codec h265 --tier excellent
  * interactive:  vtc            (or `vtc -i`) — guided prompts with defaults
  * preview:      vtc /media/tv --dry-run — decide every file, encode nothing

Builds a RunConfig, prints a run header, streams a line per file, and prints the
end-of-run report. The GUI (Stage 2) drives the same pipeline.run.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from dataclasses import replace

from . import __version__, pipeline, report
from .config import AudioPolicy, Container, Encoder, OutputMode, RunConfig, SourceAction
from .model import OutCodec, Tier, av1_factor, hevc_factor
from .pipeline import PlanRow
from .report import human_bytes
from .result import FileResult, Outcome

_OUTCOME_LINE = {
    Outcome.SHRINK: "DONE   shrink",
    Outcome.TRANSCODE: "DONE   transcode",
    Outcome.REMUX: "DONE   remux",
    Outcome.SKIP_AT_TIER: "SKIP   already at tier",
    Outcome.SKIP_UNDER_TIER: "SKIP   below your quality tier",
    Outcome.SKIP_MODERN: "SKIP   already H.265/AV1/VP9",
    Outcome.DEFER_MODERN: "QUEUE  bloated — waiting for a later run's budget",
    Outcome.SKIP_EXISTING: "SKIP   output already exists",
    Outcome.SKIP_MIN_SAVING: "SKIP   saving too small, kept original",
    Outcome.SKIP_INCOMPATIBLE: "SKIP   incompatible codec (transcode off)",
    Outcome.SKIP_CODEC: "SKIP   unsupported codec",
    Outcome.SKIP_SECOND_GEN: "SKIP   already encoded by this tool",
    Outcome.RESUME: "RESUME already done",
    Outcome.ERROR: "ERROR  encode failed",
}


# ── Argument parsing ──────────────────────────────────────────────────────────
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="vtc",
        description="Very Thoughtful Compression — codec-aware, quality-density video re-encoder.",
        epilog="Run with no directory (or -i) for guided prompts. Use --dry-run to preview.",
    )
    p.add_argument("src", type=Path, nargs="?",
                   help="directory to scan, or — with --faststart/--health — a single "
                        "file to look at (omit for interactive mode)")
    p.add_argument("--version", action="version", version=f"vtc {__version__}")
    p.add_argument("-i", "--interactive", action="store_true", help="ask for settings with prompts")
    p.add_argument("--dry-run", action="store_true", help="show what would happen; encode nothing")
    p.add_argument("--faststart", action="store_true",
                   help="report which files have their index at the back (so they cannot "
                        "start playing until fully downloaded). Add --fix to remux them")
    p.add_argument("--health", action="store_true",
                   help="report files with faults that break players — chapter markers "
                        "past the real end, damaged containers, decode errors. "
                        "Add --fix to repair what a remux can fix")
    p.add_argument("--fix", action="store_true",
                   help="with --faststart/--health: actually apply the repair. Without "
                        "it, both are report-only and change nothing")
    p.add_argument("--deep", action="store_true",
                   help="with --health: decode each file to look for bitstream damage. "
                        "Much slower — it reads the media, not just the headers")
    p.add_argument("--benchmark", action="store_true",
                   help="measure THIS machine on real files from the library and print "
                        "what each encoder actually does — speed and size at the same "
                        "quality tier. Encodes short samples; changes nothing")
    p.add_argument("--benchmark-samples", type=int, default=2, metavar="N",
                   help="how many files to sample for --benchmark (default: 2 — one can "
                        "be a static interview or a confetti cannon)")
    p.add_argument("--benchmark-seconds", type=int, default=30, metavar="S",
                   help="length of each sample for --benchmark (default: 30)")
    q = p.add_argument_group("quality")
    q.add_argument("--codec", choices=["h265", "h264", "av1"], default="h265",
                   help="output codec (default: h265). av1 is the most efficient but the "
                        "least widely playable — check your players before converting a library")
    q.add_argument("--tier", choices=["ok", "good", "excellent", "stellar", "insane"], default="excellent",
                   help="quality tier (default: excellent)")
    q.add_argument("--min-saving", type=float, default=0.25, metavar="FRACTION",
                   help="minimum size saving to keep a shrink, e.g. 0.25 = 25%% (default: 0.25)")
    q.add_argument("--bpp", type=float, default=None, metavar="BPP",
                   help="override the chosen tier's quality density (bits per pixel per frame), "
                        "e.g. 0.12; default is the tier's own anchored value")
    q.add_argument("--max-height", type=int, default=0, metavar="ROWS",
                   help="cap the output frame at this \"p\" size, e.g. 1080. Measured on the "
                        "SHORT edge the way 1080p always is — the height for landscape video, "
                        "the width for a portrait clip. The aspect ratio is kept and nothing "
                        "is upscaled (default: 0 = no cap)")
    m = p.add_argument_group(
        "bloated modern sources (H.265 / VP9 / AV1 — normally never re-encoded)")
    m.add_argument("--reencode-modern", action="store_true",
                   help="allow re-encoding modern sources that are FAR over tier target "
                        "(a bad hardware encoder at a silly bitrate). Off by default: it "
                        "costs a second lossy generation and hours per file")
    m.add_argument("--modern-over", type=float, default=2.0, metavar="X",
                   help="how many times its tier target a modern source must be before it "
                        "qualifies (default: 2.0 — at 2x the win is ~50%% and worth the time)")
    m.add_argument("--modern-codec", action="append", default=[], metavar="CODEC",
                   help="eligible source codec, repeatable (default: hevc, vp9). AV1 is "
                        "excluded on purpose — it is already the most efficient, and "
                        "re-encoding it is usually a downgrade for a lot of hours")
    m.add_argument("--modern-max", type=int, default=0, metavar="N",
                   help="how many modern files to re-encode THIS run, worst first by "
                        "predicted saving (default: 0 = all of them). Use --dry-run first "
                        "to see how many qualify and what it would cost; whatever you "
                        "leave is queued, not dismissed — the next run carries on down "
                        "the same list")
    g_ign = p.add_argument_group("ignore rules (files the scan pretends it never saw)")
    g_ign.add_argument("--ignore-under", type=float, default=None, metavar="MB",
                       help="ignore files smaller than this many MB")
    g_ign.add_argument("--ignore-over", type=float, default=None, metavar="MB",
                       help="ignore files larger than this many MB")
    g_ign.add_argument("--ignore-ext", action="append", default=[], metavar="EXT",
                       help="ignore this extension (e.g. .avi); repeatable")
    g_ign.add_argument("--ignore-name", action="append", default=[], metavar="TEXT",
                       help="ignore files whose name contains TEXT; repeatable")
    c = p.add_argument_group("compatibility")
    c.add_argument("--no-remux", action="store_true", help="do not rehome MP4-friendly codecs into MP4")
    c.add_argument("--no-transcode", action="store_true", help="leave MP4-incompatible legacy codecs untouched")
    c.add_argument("--container", choices=["auto", "mp4", "mkv"], default="auto",
                   help="output container (default: auto — MP4, MKV when it must)")
    c.add_argument("--audio", choices=["passthrough", "aac", "ac3", "flac"], default="passthrough",
                   help="audio policy (default: passthrough; flac forces MKV)")
    c.add_argument("--drop-image-subs", action="store_true",
                   help="drop image subs (PGS/DVD) instead of preferring MKV to keep them")
    e = p.add_argument_group("execution")
    e.add_argument("--encoder", choices=["auto", "hardware", "software"], default="auto",
                   help="encoder backend (default: auto)")
    e.add_argument("--jobs", type=int, default=1, help="parallel encode jobs (default: 1)")
    e.add_argument("--software-file", action="append", default=[], metavar="PATH",
                   help="encode just this file in software, even on a hardware run; "
                        "repeatable (the GUI offers this as a tick-list)")
    d = p.add_argument_group("destination")
    d.add_argument("--output", metavar="DIR", default=None,
                   help="write outputs to DIR (mirroring the tree); default is in-place")
    d.add_argument("--flat", action="store_true", help="with --output, flatten instead of mirroring")
    d.add_argument("--originals", choices=["archive", "delete", "keep"], default="archive",
                   help="what to do with replaced originals (default: archive)")
    d.add_argument("--archive-dir", type=Path, default=None, help="archive location (default: <src>/originals)")
    g = p.add_argument_group("misc")
    g.add_argument("--no-ledger", action="store_true", help="disable the resume ledger")
    g.add_argument("--ledger-file", type=Path, default=None, help="ledger path (default: <src>/.vtc_processed.log)")
    g.add_argument("--clear-history", action="store_true",
                   help="empty the resume ledger (processing history) before running")
    g.add_argument("--ffmpeg", default="ffmpeg", help="ffmpeg binary")
    g.add_argument("--ffprobe", default="ffprobe", help="ffprobe binary")
    return p


def config_from_args(a: argparse.Namespace) -> RunConfig:
    tier = Tier.from_name(a.tier)
    return RunConfig(
        src=a.src,
        out_codec=OutCodec(a.codec),
        tier=tier,
        tier_bpp={tier.name: a.bpp} if a.bpp else {},
        ignore_under_bytes=int((a.ignore_under or 0) * 1e6),
        ignore_over_bytes=int((a.ignore_over or 0) * 1e6),
        ignore_exts=tuple(e.strip().lstrip(".").lower() for e in a.ignore_ext if e.strip()),
        ignore_name_contains=tuple(n for n in a.ignore_name if n.strip()),
        min_saving_ratio=1.0 - a.min_saving,
        max_short_edge=max(0, a.max_height or 0),
        reencode_modern=a.reencode_modern,
        modern_over_tolerance=max(1.0, a.modern_over),
        modern_codecs=(tuple(c.strip().lower() for c in a.modern_codec if c.strip())
                       or ("hevc", "vp9")),
        modern_max_files=max(0, a.modern_max),
        remux_to_mp4=not a.no_remux,
        compat_transcode=not a.no_transcode,
        container=Container(a.container),
        audio_policy=AudioPolicy(a.audio),
        keep_image_subs=not a.drop_image_subs,
        encoder=Encoder(a.encoder),
        jobs=a.jobs,
        software_files=frozenset(str(Path(p).expanduser().resolve()) for p in a.software_file),
        output_mode=OutputMode.SEPARATE if a.output else OutputMode.INPLACE,
        output_dir=Path(a.output) if a.output else None,
        output_flat=a.flat,
        source_action=SourceAction(a.originals),
        archive_dir=a.archive_dir,
        ledger_enabled=not a.no_ledger,
        ledger_file=a.ledger_file,
        ffmpeg=a.ffmpeg,
        ffprobe=a.ffprobe,
    )


# ── Interactive prompts (retires the bash prompt UX) ──────────────────────────
def _menu(title: str, options: list[str], default: int) -> int:
    print(f"\n{title}")
    for i, opt in enumerate(options, 1):
        mark = "  [default]" if i == default else ""
        print(f"  {i}) {opt}{mark}")
    try:
        raw = input(f"  Choice [1-{len(options)}]: ").strip()
    except EOFError:
        raw = ""
    if not raw:
        return default
    try:
        n = int(raw)
        return n if 1 <= n <= len(options) else default
    except ValueError:
        return default


def _ask(prompt: str, default: str) -> str:
    try:
        raw = input(f"{prompt} [{default}]: ").strip()
    except EOFError:
        raw = ""
    return raw or default


def _yesno(prompt: str, default: bool = True) -> bool:
    d = "Y/n" if default else "y/N"
    try:
        raw = input(f"{prompt} [{d}]: ").strip().lower()
    except EOFError:
        raw = ""
    if not raw:
        return default
    return raw.startswith("y")


def interactive_config(src: Path | None) -> RunConfig:
    if src is None:
        src = Path(_ask("\nDirectory to scan (recursively)", str(Path.cwd()))).expanduser()

    codec = [OutCodec.H265, OutCodec.H264][_menu(
        "Output codec?",
        ["H.265 / HEVC — ~40-55% smaller, modern players",
         "H.264 / AVC  — universal playback, larger"], default=1) - 1]

    # The Mbps in each label is DERIVED from the tier, never typed in. Hand-written
    # figures here drifted from Tier for long enough that the menu offered
    # "EXCELLENT — ~6.8 Mbps" and the banner on the very next screen (which does derive
    # from tier.bpp) answered "density of 10.5 Mbps".
    _TIER_BLURB = {
        Tier.OK: "space-first",
        Tier.GOOD: "solid streaming",
        Tier.EXCELLENT: "matches top streaming, with headroom",
        Tier.STELLAR: "above streaming, approaching Blu-ray",
        Tier.INSANE: "near-transparent for streaming sources",
    }
    _tiers = [Tier.OK, Tier.GOOD, Tier.EXCELLENT, Tier.STELLAR, Tier.INSANE]
    tier = _tiers[_menu(
        "Quality tier? (Mbps are a density, quoted H.264 @1080p30; scales with "
        "resolution, and with frame rate on a measured curve)",
        [f"{t.label:<9s} — ~{t.ref_mbps:.1f} Mbps ({_TIER_BLURB[t]})" for t in _tiers],
        default=3) - 1]

    min_saving = {1: 0.25, 2: 0.15, 3: 0.35, 4: 0.0}[_menu(
        "Minimum size saving to keep a re-encode?",
        ["25%", "15%", "35%", "none — keep any successful encode"], default=1)]

    remux = _yesno("\nRemux MP4-friendly codecs (in MKV/WebM) into MP4 losslessly?", True)
    transcode = _yesno("Transcode MP4-incompatible legacy codecs (MPEG-2/VC-1/WMV)?", True)

    enc = [Encoder.AUTO, Encoder.HARDWARE, Encoder.SOFTWARE][_menu(
        "Encoder?",
        ["auto     — hardware if available",
         "hardware — VideoToolbox (fast, bitrate-targeted)",
         "software — libx264/libx265 (slower, capped-CRF, best quality)"], default=1) - 1]

    jobs = {1: 1, 2: 2, 3: 4}[_menu("Parallel encode jobs?", ["1", "2", "4"], default=1)]

    out_mode = _menu("Where should outputs be written?",
                     ["Replace in place", "A separate folder"], default=1)
    output_dir = None
    if out_mode == 2:
        output_dir = Path(_ask("  Output folder", str(src / "converted"))).expanduser()

    originals = [SourceAction.ARCHIVE, SourceAction.DELETE, SourceAction.KEEP][_menu(
        "What should happen to replaced originals?",
        ["Archive (move to a folder)", "Delete", "Leave in place"], default=1) - 1]

    return RunConfig(
        src=src, out_codec=codec, tier=tier, min_saving_ratio=1.0 - min_saving,
        remux_to_mp4=remux, compat_transcode=transcode, encoder=enc, jobs=jobs,
        output_mode=OutputMode.SEPARATE if output_dir else OutputMode.INPLACE,
        output_dir=output_dir, source_action=originals,
    )


# ── Output ────────────────────────────────────────────────────────────────────
def _ignore_line(cfg: RunConfig) -> list[str]:
    """One line naming the ignore rules in force, or nothing when there are none."""
    bits = []
    if cfg.ignore_under_bytes:
        bits.append(f"under {cfg.ignore_under_bytes / 1e6:g} MB")
    if cfg.ignore_over_bytes:
        bits.append(f"over {cfg.ignore_over_bytes / 1e6:g} MB")
    if cfg.ignore_exts:
        bits.append("ext " + ", ".join("." + e for e in cfg.ignore_exts))
    if cfg.ignore_name_contains:
        bits.append("name contains " + ", ".join(repr(n) for n in cfg.ignore_name_contains))
    return [f"IGNORE:    {'  ·  '.join(bits)}"] if bits else []


def _print_header(cfg: RunConfig, dry: bool = False) -> None:
    tier = cfg.tier
    # A retuned tier is no longer described by its anchor, so derive the 1080p30
    # reference Mbps back out of whatever density is actually in force.
    bpp = cfg.bpp_for()
    ref_mbps = bpp * 1920 * 1080 * 30 / 1e6
    tuned = "" if abs(bpp - tier.bpp) < 1e-9 else f" [retuned to {bpp:.4f} bpp]"
    h265 = ""
    if cfg.out_codec is OutCodec.H265:
        h265 = f"  (~{ref_mbps * hevc_factor(1920 * 1080, cfg.hevc_factors()):.1f} Mbps H.265 @1080p)"
    elif cfg.out_codec is OutCodec.AV1:
        h265 = f"  (~{ref_mbps * av1_factor(1920 * 1080, cfg.av1_factors()):.1f} Mbps AV1 @1080p)"
    out = str(cfg.output_dir) if cfg.output_mode is OutputMode.SEPARATE else "in place"
    banner = "DRY RUN — nothing will be encoded" if dry else None
    lines = [
        "",
        *( [banner, ""] if banner else [] ),
        f"SRC:       {cfg.src}",
        f"CODEC:     {cfg.out_codec.value.upper()}",
        # The Mbps is the tier's reference DENSITY, quoted at 1080p30 as it always has
        # been — but a 1080p30 file no longer receives exactly it, because frame rate is
        # priced on a measured curve rather than multiplied straight in (a frame costs
        # more bits at a lower frame rate). Saying "scales with fps" would now overstate
        # what happens: doubling the frame rate does not double the target.
        f"TIER:      {tier.label} — density of {ref_mbps:.1f} Mbps H.264 @1080p30{h265}; "
        f"scales with resolution, and with frame rate on a measured curve{tuned}",
        # A frame-size cap silently rewrites what every target means, so it is
        # stated up front rather than left to be inferred from the results.
        *([f"FRAME:     capped at {cfg.max_short_edge}p on the short edge — aspect kept, "
           f"smaller sources untouched"]
          if cfg.max_short_edge > 0 else []),
        # An opt-in that re-encodes already-efficient files is not something to
        # discover from the results, so it is stated before the run starts.
        *([f"MODERN:    re-encoding {'/'.join(cfg.modern_codecs)} sources over "
           f"{cfg.modern_over_tolerance:g}x tier target"
           + (f", {cfg.modern_max_files} file(s) this run (worst first)"
              if cfg.modern_max_files else ", ALL of them this run")]
          if cfg.reencode_modern else []),
        *_ignore_line(cfg),
        *([f"SOFTWARE:  {len(cfg.software_files)} file(s) picked out for the software encoder"]
          if cfg.software_files else []),
        f"ENCODER:   {cfg.encoder.value}",
        f"REMUX:     {'yes' if cfg.remux_to_mp4 else 'no'}    TRANSCODE: {'yes' if cfg.compat_transcode else 'no'}",
        f"OUTPUT:    {out}    ORIGINALS: {cfg.source_action.value}    JOBS: {cfg.jobs}",
        f"RE-ENCODE: only sources >{int((cfg.tier_over_tolerance - 1) * 100)}% over tier target",
        *( [] if dry else [f"RESUME:    {cfg.resolved_ledger_file() or 'disabled'}"] ),
        "",
    ]
    print("\n".join(lines))


def _on_result(r: FileResult) -> None:
    label = _OUTCOME_LINE.get(r.outcome, r.outcome.value)
    extra = ""
    if r.outcome.changed and r.src_bytes:
        pct = 100 * (1 - r.out_bytes / r.src_bytes)
        extra = f"  ({pct:.0f}% smaller)"
    print(f"  {label:42s} {r.path.name}{extra}", flush=True)
    for note in r.notes:
        print(f"      [{note.level}] {note.message}", flush=True)


def _plan_action(row: PlanRow) -> str:
    if row.mode is not None:
        return {"shrink": "shrink", "transcode": "transcode", "remux": "remux"}[row.mode.value]
    return _OUTCOME_LINE.get(row.outcome, row.outcome.value if row.outcome else "?").split()[0].lower()


def _print_dry_run(cfg: RunConfig) -> int:
    rows = pipeline.plan(cfg)
    if not rows:
        print("  (no video files found)")
        return 0
    # A dry run has to show the run that would ACTUALLY happen. With a modern
    # budget in force some qualifying files are deferred, and a table listing them
    # all as "shrink" would promise five encodes where two were going to happen.
    # Re-decide against the same shortlist the run would use.
    if cfg.reencode_modern and cfg.modern_max_files > 0:
        cfg = replace(cfg, modern_files=pipeline.pick_modern_shortlist(cfg, rows))
        rows = [PlanRow(r.path, r.info, *pipeline.decide(cfg, r.info))
                if r.info.ok and r.info.vcodec else r for r in rows]
    print(f"  {'file':<44s} {'res':>9s} {'codec':>6s} {'src':>8s} {'→':^3s} {'action':<10s} {'target':>8s} {'~save':>6s}")
    print("  " + "─" * 100)
    est_saved = 0.0
    n_change = 0
    work_src = 0
    for r in sorted(rows, key=lambda x: x.path.name):
        name = r.path.name if len(r.path.name) <= 44 else r.path.name[:41] + "..."
        res = f"{r.info.width}x{r.info.height}" if r.info.width else "?"
        srcmbps = f"{r.src_kbps/1000:.1f}M" if r.src_kbps else "?"
        # Pass the config: without it this falls back to the bitrate ratio, which
        # ignores audio and so promises savings that cannot arrive.
        sav = r.projected_saving(cfg)
        tgt = f"{r.target_kbps/1000:.1f}M" if r.target_kbps else "—"
        savtxt = f"{sav*100:.0f}%" if sav else ("0%" if sav == 0.0 else "—")
        try:
            size = r.path.stat().st_size
        except OSError:
            size = 0
        if sav:
            est_saved += size * sav
            n_change += 1
            work_src += size
        print(f"  {name:<44s} {res:>9s} {r.info.vcodec or '?':>6s} {srcmbps:>8s} "
              f"{'→':^3s} {_plan_action(r):<10s} {tgt:>8s} {savtxt:>6s}")
    print("  " + "─" * 100)
    # Lead with what happens to the files being TOUCHED. Averaging the saving
    # across a whole library — most of which is deliberately left alone — makes
    # worthwhile work read as pointless.
    # Queued files are counted apart from left-as-is ones: they are waiting for a
    # later run's budget, not decided against, and folding them into "left as-is"
    # would hide the very thing --modern-max is being used to control.
    queued = sum(1 for r in rows if r.outcome is Outcome.DEFER_MODERN)
    tail = f", {queued} queued for a later run" if queued else ""
    print(f"  {len(rows)} file(s): {n_change} would be re-encoded, "
          f"{len(rows) - n_change - queued} left as-is{tail}")
    if n_change:
        pct = (est_saved / work_src * 100) if work_src else 0
        print(f"  Those {n_change}: {human_bytes(work_src)} -> ~{human_bytes(work_src - est_saved)}"
              f"  |  est. recovery ~{human_bytes(est_saved)} ({pct:.0f}% of what is touched)")
    _print_modern_review(cfg, rows)
    return 0


def _print_modern_review(cfg: RunConfig, rows) -> None:
    """Size up the modern re-encode queue before anyone commits a night to it.

    Hours per file is the whole problem with this option, so a dry run says how
    many qualify and names the worst offenders — and says plainly that the choice
    of how many to do is `--modern-max`, and that the rest are not lost.
    """
    if not cfg.reencode_modern:
        return
    review = pipeline.modern_review(cfg, [(r.info, _size_of(r.path)) for r in rows])
    n = review["files"]
    print()
    if not n:
        print("  MODERN: nothing qualifies — no modern file is far enough over target.")
        return
    print(f"  MODERN: {n} bloated {'/'.join(cfg.modern_codecs)} file(s) qualify — "
          f"{human_bytes(review['bytes'])}, est. recovery ~{human_bytes(review['saved_bytes'])}")
    print(f"          This is SLOW work — {_hms(review['seconds'])} of video to re-encode, "
          f"and a re-encode runs far slower than real time.")
    for row in review["top"][:5]:
        print(f"            {row['name'][:52]:<52s} {human_bytes(row['bytes']):>9s}  "
              f"{row['over']:.1f}x over  saves ~{human_bytes(row['saved_bytes'])}")
    if n > 5:
        print(f"            … and {n - 5} more")
    doing = f"all {n}" if not cfg.modern_max_files else f"the top {min(cfg.modern_max_files, n)}"
    print(f"          This run would do {doing}. Choose with --modern-max N; whatever is "
          f"left is queued, not dropped —")
    print("          re-run and it carries on down the same worst-first list.")


def _hms(seconds: float) -> str:
    """A play length a person can read. "0.0 hours" looks like a broken number, so
    short durations are said in minutes and long ones in hours."""
    if seconds < 90:
        return f"{int(seconds)} seconds"
    if seconds < 5400:
        return f"{seconds / 60:.0f} minutes"
    return f"{seconds / 3600:.1f} hours"


def _size_of(p: Path) -> int:
    try:
        return p.stat().st_size
    except OSError:
        return 0


def _print_utility(cfg: RunConfig, tool: str, fix: bool, deep: bool) -> int:
    """Report what a library needs, and only repair when explicitly told to.

    Report-first is the whole shape of these tools: they operate on every file at
    once, and one that starts by rewriting things is not one anybody can try out.
    """
    from . import utilities
    # A single file is a first-class target: when one episode misbehaves, being
    # made to point at its folder and wait for a library scan is the wrong shape
    # of tool entirely.
    one = Path(cfg.src)
    files = [one] if one.is_file() else list(pipeline.iter_video_files(cfg))
    if not files:
        print("  (no video files found)")
        return 0
    title = "Faststart" if tool == "faststart" else "File health"
    where = files[0].name if one.is_file() else f"{len(files)} file(s) under {cfg.src}"
    print(f"\n  {title} — {where}")
    if tool == "health" and not deep:
        print("  Headers only. --deep also decodes each file to find bitstream damage.")
    print()
    reports, needy = [], []
    for f in files:
        rep = (utilities.scan_faststart(cfg, f) if tool == "faststart"
               else utilities.scan_health(cfg, f, decode_seconds=20 if deep else 0))
        reports.append(rep)
        if rep.needs:
            needy.append(rep)
            print(f"    {rep.path.name[:56]:<56} {'; '.join(x.detail for x in rep.faults)[:60]}")
    if not needy:
        print(f"    Nothing to do — all {len(files)} file(s) are fine.")
        return 0
    fixable = [r for r in needy if r.fix != "none"]
    print(f"\n  {len(needy)} file(s) need attention; {len(fixable)} can be repaired here.")
    unfixable = len(needy) - len(fixable)
    if unfixable:
        print(f"  {unfixable} cannot be repaired automatically — a truncated download is "
              f"gone, not broken;\n  fetching the file again is the honest answer.")
    if not fix:
        print("\n  Nothing was changed. Add --fix to repair the ones that can be.")
        return 0
    print()
    ok = 0
    for rep in fixable:
        res = (utilities.remux_faststart(cfg, rep.path) if tool == "faststart"
               else utilities.fix_health(cfg, rep, allow_reencode=deep))
        ok += bool(res.ok)
        status = "fixed" if res.ok else f"FAILED — {res.error}"
        extra = f" ({res.note})" if res.note else ""
        print(f"    {rep.path.name[:56]:<56} {status}{extra}")
    print(f"\n  Repaired {ok} of {len(fixable)}.")
    return 0


def _print_benchmark(cfg: RunConfig, samples: int, seconds: int) -> int:
    """What this machine actually does, measured on the user's own files.

    Prints both halves, because they point opposite ways and the choice needs
    both: hardware is several times faster, software usually produces a much
    smaller file at the same quality tier.
    """
    from . import bench
    print(f"\n  Sampling {samples} file(s) x {seconds}s from {cfg.src}")
    print("  Real files, not test patterns — synthetic clips reverse this comparison.\n")

    def prog(done, total, label):
        print(f"    [{done}/{total}] {label[:78]}", flush=True)

    result = bench.run_benchmark(cfg, samples=samples, seconds=seconds, progress=prog)
    rows = result.summary()
    if not rows:
        print("\n  Nothing could be measured — no usable video files found here.")
        return 1
    print(f"\n  Sampled: {', '.join(result.samples)}")
    if result.skipped:
        print(f"  Not available here: {', '.join(result.skipped)}")
    print(f"\n  {'codec':6} {'path':9} {'encoder':20} {'speed@1080p':>12} "
          f"{'of target':>10} {'SSIM':>8}")
    print("  " + "─" * 70)
    for r in rows:
        ss = f"{r['ssim']:.4f}" if r["ssim"] else "—"
        print(f"  {r['codec']:6} {r['path']:9} {r['encoder']:20} "
              f"{r['at_1080p']:11.1f}x {r['of_target']*100:9.0f}% {ss:>8}")
    print("\n  speed@1080p — x realtime for a 1080p30 frame, so the figure does not")
    print("                depend on which files happened to be sampled")
    print("  of target   — how much of the tier's bitrate allowance it actually spent;")
    print("                software is often far under it at the same quality")
    return 0


# ── Entry point ───────────────────────────────────────────────────────────────
def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    # Same sweep the GUI does at startup: a CLI run killed by a crash, a power cut
    # or a SIGKILL leaves its part-encoded temp behind, and nothing else collects it.
    pipeline.sweep_stale_scratch()

    if args.interactive or args.src is None:
        cfg = interactive_config(args.src)
    else:
        cfg = config_from_args(args)

    # The Utilities take a single file as readily as a folder, so "src is not a
    # directory" is only an error for the paths that actually scan a tree.
    single_file = args.src is not None and Path(args.src).is_file()
    errs = [e for e in cfg.validate()
            if not (single_file and e.startswith("scan directory does not exist"))]
    if errs:
        for e in errs:
            print(f"error: {e}", file=sys.stderr)
        return 2
    if single_file and not (args.faststart or args.health):
        print("error: that is a file, not a directory — only --faststart and --health "
              "take a single file", file=sys.stderr)
        return 2

    if getattr(args, "clear_history", False):
        import dataclasses
        # Force the ledger on just to reach the file: the history exists on disk
        # whether or not this run is using it, and --clear-history must empty it.
        n = pipeline.Ledger(dataclasses.replace(cfg, ledger_enabled=True)).clear()
        print(f"cleared processing history: {n} entr{'y' if n == 1 else 'ies'}")

    if args.faststart or args.health:
        return _print_utility(cfg, "faststart" if args.faststart else "health",
                              fix=args.fix, deep=args.deep)
    if args.benchmark:
        return _print_benchmark(cfg, args.benchmark_samples, args.benchmark_seconds)
    _print_header(cfg, dry=args.dry_run)
    if args.dry_run:
        return _print_dry_run(cfg)

    start = time.monotonic()
    try:
        results = pipeline.run(cfg, on_result=_on_result)
    except KeyboardInterrupt:
        print("\ninterrupted.", file=sys.stderr)
        return 130

    print()
    print(report.render(results))
    print(f"\nDone in {time.monotonic() - start:.0f}s.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
