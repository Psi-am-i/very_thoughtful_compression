"""Benchmark this machine: what can it actually encode, how fast, and how small?

Every timing the app quotes rests on a rate measured on the user's own hardware.
Until a run has happened there is nothing to measure, so the app falls back to
constants taken on one developer's Mac — honest, but not *theirs*. This module
closes that gap up front: point it at a library, and it samples real files from
it and encodes them down every path the machine actually supports.

Two design rules, both learned the hard way:

  * **Real files, never synthetic.** A `lavfi` pattern is pathological — huge
    entropy, little temporal redundancy — and it does not merely add noise, it
    reverses conclusions. Measured both ways, synthetic content put hardware
    H.264 only 1.15x ahead of libx264 where real television puts it 3x ahead,
    and made SVT-AV1 look faster than libx265 when it is the slowest path there
    is. So the samples come out of the user's own library.

  * **More than one file.** A single sample can be a static interview or a
    confetti cannon, and those differ by more than the thing being measured. The
    default is two, and the caller can ask for more.

The result is both halves of the choice: how FAST each path is, and how SMALL it
gets at the same quality tier — which run in opposite directions, and neither is
knowable from the other.
"""

from __future__ import annotations

import random
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import encode, pipeline
from .config import Encoder, RunConfig
from .ffprobe import MediaInfo, probe
from .model import OutCodec
from .result import Mode
from .winproc import NO_WINDOW, TEXT_UTF8

# A sample long enough to measure honestly and short enough that a benchmark is
# something you wait for rather than schedule. Process start-up and the first GOP
# dominate anything much below this.
SAMPLE_SECONDS = 30
# Files smaller than this are trailers, extras and sample.mkv — not the workload.
MIN_SAMPLE_BYTES = 200_000_000


@dataclass
class PathResult:
    """One codec down one path (hardware or software), on one sample."""
    codec: str
    path: str                      # "hardware" | "software"
    encoder: str                   # the ffmpeg encoder actually used
    seconds: float = 0.0           # wall clock spent encoding
    rate: float = 0.0              # output pixel-frames per second — the ETA's unit
    realtime: float = 0.0          # x realtime, the human-readable version
    kbps: float = 0.0              # what it actually produced
    target_kbps: int = 0           # what the tier asked for
    ssim: float | None = None      # against the sample; None if not measured
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error and self.rate > 0


@dataclass
class Benchmark:
    """Everything one benchmark run learned."""
    samples: list[str] = field(default_factory=list)      # the files used
    results: list[PathResult] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)      # paths this machine hasn't got

    def rates(self) -> dict[tuple[str, str], float]:
        """Mean pixel-frames/sec per (codec, path), over the samples that worked.

        Averaged rather than best-of: the point is what a library will average,
        and the fastest sample is by definition the least representative one.
        """
        buckets: dict[tuple[str, str], list[float]] = {}
        for r in self.results:
            if r.ok:
                buckets.setdefault((r.codec, r.path), []).append(r.rate)
        return {k: sum(v) / len(v) for k, v in buckets.items()}

    def summary(self) -> list[dict]:
        """One row per (codec, path), averaged — what the UI shows."""
        rows: dict[tuple[str, str], dict] = {}
        for r in self.results:
            if not r.ok:
                continue
            k = (r.codec, r.path)
            row = rows.setdefault(k, {"codec": r.codec, "path": r.path,
                                      "encoder": r.encoder, "n": 0, "realtime": 0.0,
                                      "rate": 0.0, "kbps": 0.0, "target_kbps": 0,
                                      "ssim": 0.0, "ssim_n": 0})
            row["n"] += 1
            row["realtime"] += r.realtime
            row["rate"] += r.rate
            row["kbps"] += r.kbps
            row["target_kbps"] += r.target_kbps
            if r.ssim is not None:
                row["ssim"] += r.ssim
                row["ssim_n"] += 1
        out = []
        for row in rows.values():
            n = row.pop("n")
            sn = row.pop("ssim_n")
            for key in ("realtime", "rate", "kbps", "target_kbps"):
                row[key] = row[key] / n
            row["ssim"] = (row["ssim"] / sn) if sn else None
            # A × realtime averaged over a 1080p episode and a DVD-resolution one
            # is not a number about the machine — it is a number about which files
            # happened to be drawn. Normalise it to a 1080p30 frame so two runs of
            # the benchmark are comparable however the sampling fell out. The rate
            # itself is already resolution-independent, which is why it, and not
            # this, is what gets stored and used.
            row["at_1080p"] = row["rate"] / (1920 * 1080 * 30.0)
            # Likewise sizes: what matters is how much of the tier's allowance each
            # path actually spent, which comparing raw kbps across resolutions hides.
            row["of_target"] = (row["kbps"] / row["target_kbps"]) if row["target_kbps"] else 0.0
            out.append(row)
        # Fastest first within a codec, codecs in the order the UI offers them.
        order = {"h264": 0, "h265": 1, "av1": 2}
        out.sort(key=lambda r: (order.get(r["codec"], 9), -r["at_1080p"]))
        return out


def pick_samples(config: RunConfig, count: int, rng: random.Random | None = None,
                 min_bytes: int = MIN_SAMPLE_BYTES) -> list[Path]:
    """`count` files chosen at random from the library, biggest-first as a fallback.

    Random rather than first-N: a library is alphabetical, and the first few files
    are one show — which is one kind of content, shot one way. Anything too small
    to be an episode is left out, since trailers and extras are not the workload.
    """
    rng = rng or random.Random()
    files = [f for f in pipeline.iter_video_files(config)]
    big = [f for f in files if _size(f) >= min_bytes]
    pool = big or files                       # a library of small files is still a library
    if not pool:
        return []
    if len(pool) <= count:
        return sorted(pool)
    return sorted(rng.sample(pool, count))


def _size(p: Path) -> int:
    try:
        return p.stat().st_size
    except OSError:
        return 0


def available_paths(config: RunConfig) -> list[tuple[OutCodec, str, str]]:
    """Every (codec, path, encoder) this machine can actually encode down.

    Probed, not assumed — `select_hw_encoder` runs a real one-frame encode, so a
    listed-but-broken encoder never makes it into a benchmark and then into an
    estimate.
    """
    out: list[tuple[OutCodec, str, str]] = []
    for codec in (OutCodec.H264, OutCodec.H265, OutCodec.AV1):
        hw = encode.select_hw_encoder(RunConfig(src=config.src, out_codec=codec,
                                                ffmpeg=config.ffmpeg))
        if hw:
            out.append((codec, "hardware", hw))
        soft = encode.AV1_SOFTWARE if codec is OutCodec.AV1 else (
            "libx265" if codec is OutCodec.H265 else "libx264")
        if codec is not OutCodec.AV1 or encode._encoder_works(config.ffmpeg, soft):
            out.append((codec, "software", soft))
    return out


# The sample budget is quoted at 1080p30. A benchmark's cost is pixel-frames, not
# seconds, so a fixed number of SECONDS makes the run time depend entirely on what
# the library happens to contain — 60s of 4K is four times the work of 60s of
# 1080p, and 4K60 is eight times, for exactly the same answer, since the rate that
# comes out is normalised to pixel-frames anyway. A 4K library was measured taking
# over half an hour where a 1080p one took eight minutes.
_REF_PIXEL_FPS = 1920 * 1080 * 30.0
# Never go below this: at some point the encoder's start-up dominates and the
# measurement stops being about steady-state throughput.
_MIN_SAMPLE_SECONDS = 8


def sample_seconds(info: MediaInfo, budget: int) -> float:
    """How long a slice of THIS file equals `budget` seconds of 1080p30 work."""
    work = (info.display_width or info.width) * (info.display_height or info.height) \
        * (info.fps or 30.0)
    if work <= 0:
        return float(budget)
    return max(_MIN_SAMPLE_SECONDS, min(float(budget), budget * _REF_PIXEL_FPS / work))


def _extract(config: RunConfig, src: Path, dest: Path, seconds: int) -> MediaInfo | None:
    """A stream-copied slice from the middle of a file — no re-encode, so nothing
    about the sample is coloured by how we took it. The middle, because the start
    of an episode is titles and the end is credits, and neither is the content.

    `seconds` is a budget at 1080p30; the slice actually taken is shortened for a
    bigger frame so every sample costs the encoder about the same (see
    sample_seconds). Without that, one 4K show in the library turns a two-minute
    benchmark into half an hour.
    """
    info = probe(src, config.ffprobe)
    if not info.ok or not info.vcodec or info.width <= 0:
        return None
    seconds = sample_seconds(info, seconds)
    dur = info.duration or 0.0
    start = max(0.0, dur / 2 - seconds / 2) if dur > seconds else 0.0
    r = subprocess.run(
        [config.ffmpeg, "-v", "error", "-y", "-ss", f"{start:.3f}", "-t", str(seconds),
         "-i", str(src), "-map", "0:v:0", "-c", "copy", str(dest)],
        stdin=subprocess.DEVNULL, capture_output=True, text=True, **TEXT_UTF8, **NO_WINDOW)
    if r.returncode != 0 or not dest.exists() or dest.stat().st_size == 0:
        return None
    got = probe(dest, config.ffprobe)
    return got if got.ok and got.duration else None


_SSIM_RE = re.compile(r"All:\s*([0-9.]+)")


def _ssim(config: RunConfig, ref: Path, test: Path) -> float | None:
    """Structural similarity of an encode against the sample it came from.

    Without it "smaller" is not a result — a codec can always be smaller by being
    worse. This is what lets the benchmark say software produced a fifth of the
    size at the same quality, which is the finding worth having.
    """
    try:
        r = subprocess.run(
            # -v INFO, not error: the ssim filter reports at info level, so
            # quietening ffmpeg the way every other call here does would silently
            # return no score at all — which is exactly what it did the first time.
            [config.ffmpeg, "-hide_banner", "-v", "info", "-i", str(test), "-i", str(ref),
             "-lavfi", "[0:v][1:v]ssim", "-f", "null", "-"],
            stdin=subprocess.DEVNULL, capture_output=True, text=True,
            timeout=600, **TEXT_UTF8, **NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return None
    for line in reversed((r.stderr or "").splitlines()):
        m = _SSIM_RE.search(line)
        if m:
            try:
                return float(m.group(1))
            except ValueError:
                return None
    return None


def run_benchmark(config: RunConfig, samples: int = 2, seconds: int = SAMPLE_SECONDS,
                  measure_quality: bool = True, progress=None,
                  rng: random.Random | None = None,
                  stop=None) -> Benchmark:
    """Encode real slices of this library down every path this machine supports.

    `progress(done, total, label)` is called before each encode so a UI can show
    what is happening; `stop()` returning True abandons the run between encodes.
    Never raises: a machine that cannot encode something records that and carries
    on, because a benchmark that dies on one bad path is worth nothing.
    """
    bench = Benchmark()
    paths = available_paths(config)
    for codec in (OutCodec.H264, OutCodec.H265, OutCodec.AV1):
        if not any(c is codec and p == "hardware" for c, p, _ in paths):
            bench.skipped.append(f"{codec.value} hardware")
    picks = pick_samples(config, samples, rng=rng)
    if not picks:
        return bench

    total = len(picks) * len(paths)
    done = 0
    with tempfile.TemporaryDirectory(prefix="vtcbench") as tmp:
        tmp = Path(tmp)
        for n, src in enumerate(picks):
            if stop and stop():
                break
            sample = tmp / f"s{n}.mkv"
            info = _extract(config, src, sample, seconds)
            if info is None:
                bench.skipped.append(f"could not sample {src.name}")
                total -= len(paths)
                continue
            bench.samples.append(src.name)
            for codec, path, enc_name in paths:
                if stop and stop():
                    break
                done += 1
                if progress:
                    progress(done, total, f"{src.name} · {codec.value} {path}")
                bench.results.append(
                    _time_one(config, info, sample, tmp, codec, path, enc_name,
                              measure_quality, n))
    return bench


def _time_one(config: RunConfig, info: MediaInfo, sample: Path, tmp: Path,
              codec: OutCodec, path: str, enc_name: str,
              measure_quality: bool, n: int) -> PathResult:
    res = PathResult(codec=codec.value, path=path, encoder=enc_name)
    cfg = RunConfig(src=config.src, out_codec=codec, ffmpeg=config.ffmpeg,
                    ffprobe=config.ffprobe, tier=config.tier,
                    tier_bpp=dict(config.tier_bpp),
                    max_short_edge=config.max_short_edge,
                    encoder=Encoder.HARDWARE if path == "hardware" else Encoder.SOFTWARE)
    # The tier's real target for this file, so the sizes below are the sizes a run
    # would actually produce rather than an arbitrary bitrate.
    target = pipeline.decide(cfg, info)[2]
    if target <= 0:
        target = pipeline.target_kbps(
            cfg.tier, info.pixels, info.fps, codec, floor_kbps=cfg.bitrate_floor_kbps,
            bpp=cfg.bpp_for(), hevc=cfg.hevc_factors(), av1=cfg.av1_factors())
    res.target_kbps = target
    hw = enc_name if path == "hardware" else None
    args = encode.build_video_args(cfg, info, Mode.SHRINK, target, hw)
    out = tmp / f"o{n}-{codec.value}-{path}.mp4"
    t0 = time.monotonic()
    try:
        r = subprocess.run(
            [config.ffmpeg, "-v", "error", "-y", "-i", str(sample), *args, "-an", str(out)],
            stdin=subprocess.DEVNULL, capture_output=True, text=True,
            timeout=1800, **TEXT_UTF8, **NO_WINDOW)
    except (OSError, subprocess.SubprocessError) as e:
        res.error = str(e)[:120]
        return res
    res.seconds = time.monotonic() - t0
    if r.returncode != 0 or not out.exists() or out.stat().st_size == 0:
        res.error = ((r.stderr or "").strip().splitlines() or ["encode failed"])[-1][:120]
        return res
    dur = info.duration or 0.0
    dims = pipeline.capped_dims(info.display_width, info.display_height, cfg.max_short_edge)
    w, h = dims if dims else (info.display_width, info.display_height)
    work = pipeline.encode_work(w, h, info.fps, dur)
    if work > 0 and res.seconds > 0:
        res.rate = work / res.seconds
        res.realtime = dur / res.seconds
    res.kbps = out.stat().st_size * 8 / 1000.0 / dur if dur > 0 else 0.0
    if measure_quality:
        res.ssim = _ssim(config, sample, out)
    return res


def have_tools(config: RunConfig) -> bool:
    return bool(shutil.which(config.ffmpeg) and shutil.which(config.ffprobe))
