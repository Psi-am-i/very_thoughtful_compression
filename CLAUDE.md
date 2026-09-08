**MemPalace wing:** `very_thoughtful_compression` — file every diary entry, drawer and KG fact for this project here.
**Global policy — agent identity, MemPalace, machines, secrets — lives in `~/CLAUDE.md` (rules 1-5)** and `~/projects/SECRETS.md`. Do not restate it here.

---

# Very Thoughtful Compression

A selective re-encoder for a video library: it decides, per file, whether shrinking
it is actually worth doing — then does it without ever making a file worse or losing
an original. The bash script it grew out of is retired; this is a Python engine with
two front ends, packaged as Mac and Windows apps.

```
vtc/model.py        the bitrate/quality model — pure arithmetic, no I/O
vtc/config.py       RunConfig: every setting, UI-agnostic
vtc/pipeline.py     scan → probe → decide → encode → place; the run itself
vtc/encode.py       ffmpeg argument construction and execution
vtc/ffprobe.py      probing; MediaInfo
vtc/ledger.py       the resume ledger ("processing history")
vtc/report.py       run report
vtc/netmove.py      network-safe moves (stalled/vanished volumes)
vtc/cli.py          the `vtc` command line
vtc/webapp.py       the GUI's Python side: pywebview shell + the JS bridge
vtc/vtc_app_v3.html the ENTIRE front end, one self-contained file
docs/               quality-model.md, copy-deck.md, library-file-fixes.md
tests/              pytest; tests/ui/ is a jsdom harness driving the real HTML
packaging/          PyInstaller specs + build_gui_app.sh
```

## Read these before changing behaviour

- **`docs/quality-model.md`** — tiers as a bits-per-pixel *density*, absolute targets,
  the convergence gate, frame size, bloated modern sources, and how long a run will
  take. Almost every "why is it doing that?" is answered here.
- **`DESIGN-NOTES.md`** — the UI's design history and the user's taste, round by round,
  including what was rejected and why.

## The rules that matter

**Never make a file worse, and never lose an original.** A source is replaced only
after the new file exists, probes valid, matches the play length and is meaningfully
smaller. Targets are *absolute* (a function of the tier and the file's pixels/fps),
never a fraction of the current bitrate — that is what makes re-runs converge instead
of shaving a file smaller every pass.

**⛔ `vtc/vtc_app_v3.html` is a ~7,000-line bespoke artifact. NEVER use Write on it.**
Only surgical, verified single-match replacements (Edit, or a helper that asserts
exactly one match). An agent once used Write on it and then `git checkout`, destroying
~6,600 lines of uncommitted work; it was recovered only from a stale browser tab.
Commit a checkpoint after every pass, and never run `git checkout`/`restore`/`stash`/
`reset` to "undo" something here.

**⚠️ A remux is not always harmless — check the index first.** Everyone treats a
stream-copy remux as free, and for a healthy file it is. For one whose sample index
has desynchronised from its media it is destructive: a remux copies samples out
*according to the index*, so a broken index means the wrong bytes are written out as
the new truth and the recoverable frames are gone. Measured on a real file:
rebuilding the index recovered 20,915 of 20,927 frames with no errors; doing the same
after "just a remux" recovered 18,493 with 21. The remuxed file still decodes cleanly
at the head, so the obvious check agrees it worked. `utilities.remux_faststart()`
refuses a desynchronised file for this reason, and anything new that rewrites a
container must do the same — see `docs/library-file-fixes.md` §0.

**The app is deliberately network-free.** Fonts are bundled as base64; there is no
CDN, no icon font, no telemetry. Don't add a dependency to solve a small problem.

**Tests and the real thing are different claims.** `pytest -q` proves behaviour (the
jsdom harness in `tests/ui/` drives the actual HTML); only a real encode proves the
ffmpeg arguments do what the argument list says. For anything touching encoding, run
one. Chrome proves it *looks* right — see [[ui-test-harness]] in the palace.

**Benchmark on REAL video, never on `lavfi`.** Synthetic sources — `testsrc2`,
`mandelbrot`, noise — are pathological: enormous entropy, little temporal
redundancy, and motion a search resolves far too easily. They flatter and punish
the two encoder paths differently, so a conclusion drawn from them can be simply
backwards. Measured the same comparison both ways: on `testsrc2`, hardware H.264
came out at speed ×1.15 against `libx264`, which would have justified recommending
software; on real episodes it is **speed ×3.0**. Synthetic content also mis-ranked SVT-AV1
against libx265 — and note that even on real footage that ranking flips with
resolution (AV1 is slower at 1080p, faster at 4K), so never state it without one.
Use `/Volumes/RAID/TV` (154 shows,
every resolution and bitrate) and take a few five-minute samples with `-c copy`.
Synthetic clips are fine for *plumbing* tests — does the filter apply, does the
argument reach ffmpeg — and for nothing that produces a number a user will see.

**Estimates must be measured or absent.** Encode time is predicted in output
pixel-frames from a rate this machine actually achieved; where nothing has been
measured, the app says it cannot estimate rather than inventing a number. Keep it
that way.

## Working on it

```sh
python3 -m pytest -q              # the whole suite (needs ffmpeg/ffprobe for some)
python3 -m vtc.cli DIR --dry-run  # decide every file, encode nothing
open vtc/vtc_app_v3.html          # the front end standalone, on mock data
```

The GUI file feature-detects `window.pywebview`: opened directly in a browser it runs
entirely on mock data, which is how UI work is done and reviewed without a build.

⚠️ **Never run `packaging/build_gui_app.sh` while the app is running** — its first line
`pkill`s the running app and will kill a live encode session.
