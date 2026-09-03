"""Drive the real Advanced-settings UI in a DOM and check it does what it says.

The page is loaded exactly as shipped, then the controls are operated the way a
person operates them — type in the box, click the toggle — and the state handed
to the engine is read back. Pairs with tests/test_advanced_labels.py, which takes
that state and proves the engine then behaves as the label promised.

Needs node + jsdom (`cd tests/ui && npm install jsdom`); skipped without them, so
a plain `pytest tests/` on a machine with no node still passes.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

try:
    import pytest
except ModuleNotFoundError:                      # standalone: no pytest available
    class _Stub:                                 # just enough to define the tests
        class mark:
            @staticmethod
            def skipif(cond, reason=""):
                def deco(fn):
                    fn.__skip__ = cond
                    return fn
                return deco
    pytest = _Stub()

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_UI = Path(__file__).resolve().parent / "ui"


def _have_jsdom() -> bool:
    if not shutil.which("node"):
        return False
    r = subprocess.run(["node", "-e", "require('jsdom')"], cwd=_UI,
                       capture_output=True, stdin=subprocess.DEVNULL)
    return r.returncode == 0


@pytest.mark.skipif(not _have_jsdom(), reason="node + jsdom not installed (see tests/ui/README)")
def test_advanced_settings_ui():
    r = subprocess.run(["node", "drive.js"], cwd=_UI, capture_output=True, text=True,
                       stdin=subprocess.DEVNULL, timeout=180)
    assert r.stdout.strip(), f"harness produced nothing:\n{r.stderr[:2000]}"
    out = json.loads(r.stdout)
    assert "fatal" not in out, f"{out['fatal']}: {out.get('errors')}"
    failed = [x for x in out["results"] if not x["ok"]]
    assert not failed, "UI controls not behaving as labelled:\n" + "\n".join(
        f"  {x['name']}: got {x['got']!r}, want {x['want']!r}" for x in failed)
    assert len(out["results"]) >= 40, "harness ran fewer checks than expected"


@pytest.mark.skipif(not _have_jsdom(), reason="node + jsdom not installed (see tests/ui/README)")
def test_parallel_progress_rows():
    """With jobs>1 every file in flight must stay visible.

    A single curName meant the workers overwrote each other: one file showed as
    processing and the rest vanished, while the Stop button was already saying
    "files in flight" (plural).
    """
    r = subprocess.run(["node", "flight.js"], cwd=_UI, capture_output=True, text=True,
                       stdin=subprocess.DEVNULL, timeout=120)
    assert r.stdout.strip(), f"harness produced nothing:\n{r.stderr[:2000]}"
    failed = [x for x in json.loads(r.stdout) if not x["ok"]]
    assert not failed, "parallel progress broken:\n" + "\n".join(
        f"  {x['n']}: got {x['g']!r}, want {x['e']!r}" for x in failed)


@pytest.mark.skipif(not _have_jsdom(), reason="node + jsdom not installed (see tests/ui/README)")
def test_step_navigation_does_not_strand_you():
    """Going back to change an answer must not disable the question you came from.

    Reported from use: jumping from an unanswered DESTINATION back to QUALITY left
    destination neither done nor current, so it disabled itself and the only way
    forward was re-confirming every question in between.
    """
    r = subprocess.run(["node", "nav.js"], cwd=_UI, capture_output=True, text=True,
                       stdin=subprocess.DEVNULL, timeout=120)
    assert r.stdout.strip(), f"harness produced nothing:\n{r.stderr[:2000]}"
    failed = [x for x in json.loads(r.stdout) if not x["ok"]]
    assert not failed, "step navigation broken:\n" + "\n".join(
        f"  {x['n']}: got {x['g']!r}, want {x['e']!r}" for x in failed)


@pytest.mark.skipif(not _have_jsdom(), reason="node + jsdom not installed (see tests/ui/README)")
def test_frame_size_answer_reaches_the_settings():
    """Committing the FRAME SIZE step must mirror the cap into the settings.

    The run maps `answers.resize` itself, but the tier previews only ever see the
    settings dict — so without the mirror the previews would encode 4K panels for
    a run that is about to write 1080p, and misreport both size and density.
    """
    r = subprocess.run(["node", "frame.js"], cwd=_UI, capture_output=True, text=True,
                       stdin=subprocess.DEVNULL, timeout=120)
    assert r.stdout.strip(), f"harness produced nothing:\n{r.stderr[:2000]}"
    failed = [x for x in json.loads(r.stdout) if not x["ok"]]
    assert not failed, "frame-size wiring broken:\n" + "\n".join(
        f"  {x['n']}: got {x.get('g')!r}, want {x['e']!r}" for x in failed)


@pytest.mark.skipif(not _have_jsdom(), reason="node + jsdom not installed (see tests/ui/README)")
def test_modern_review_opens_on_all_not_a_chosen_number():
    """The review sheet must never open pre-set to a budget.

    How many hours to spend is the user's decision; the sheet exists to show them
    the work and let them reduce it. Opening on "top 25" would read as a
    recommendation — and a budget stored from a previous answer must not creep
    back in as one either.
    """
    r = subprocess.run(["node", "modernsel.js"], cwd=_UI, capture_output=True, text=True,
                       stdin=subprocess.DEVNULL, timeout=120)
    assert r.stdout.strip(), f"harness produced nothing:\n{r.stderr[:2000]}"
    failed = [x for x in json.loads(r.stdout) if not x["ok"]]
    assert not failed, "review sheet pre-selects a budget:\n" + "\n".join(
        f"  {x['n']}: got {x.get('g')!r}, want {x['e']!r}" for x in failed)


@pytest.mark.skipif(not _have_jsdom(), reason="node + jsdom not installed (see tests/ui/README)")
def test_av1_ui_tells_the_truth_about_this_machine():
    """AV1's UI has to change with the machine, and getting it wrong states a
    falsehood rather than looking untidy.

    Three profiles: a Mac (software only — no Apple silicon encodes AV1), a PC
    with a hardware AV1 card, and a machine with neither. The encoder question
    must follow the CHOSEN codec rather than "is there any hardware at all",
    which is what it used to do — pick AV1 on a Mac and Hardware stayed
    selectable while the engine silently ran software.
    """
    r = subprocess.run(["node", "av1.js"], cwd=_UI, capture_output=True, text=True,
                       stdin=subprocess.DEVNULL, timeout=120)
    assert r.stdout.strip(), f"harness produced nothing:\n{r.stderr[:2000]}"
    failed = [x for x in json.loads(r.stdout) if not x.get("ok")]
    assert not failed, "AV1 UI misstates this machine:\n" + "\n".join(
        f"  {x.get('n')}: got {x.get('g')!r}, want {x.get('e')!r}" for x in failed)


@pytest.mark.skipif(not _have_jsdom(), reason="node + jsdom not installed (see tests/ui/README)")
def test_benchmark_section_and_settings_passthrough():
    """The "This machine" section, and the thing it must never do: lose what the
    engine measured. Rates live in the same object as the user's preferences, so a
    save that dropped unknown keys would delete an hour of benchmarking. Python is
    now authoritative for those keys, and this pins the UI half."""
    r = subprocess.run(["node", "bench.js"], cwd=_UI, capture_output=True, text=True,
                       stdin=subprocess.DEVNULL, timeout=120)
    assert r.stdout.strip(), f"harness produced nothing:\n{r.stderr[:2000]}"
    failed = [x for x in json.loads(r.stdout) if not x.get("ok")]
    assert not failed, "benchmark section broken:\n" + "\n".join(
        f"  {x.get('n')}: got {x.get('g')!r}, want {x.get('e')!r}" for x in failed)


@pytest.mark.skipif(not _have_jsdom(), reason="node + jsdom not installed (see tests/ui/README)")
def test_the_first_run_benchmark_offer_asks_once():
    """Offered, never imposed: shown only when nothing has measured this machine,
    suppressed by a real run or an existing benchmark, queued behind the other
    up-front sheets, and recorded on every route out so it cannot nag."""
    r = subprocess.run(["node", "benchoffer.js"], cwd=_UI, capture_output=True, text=True,
                       stdin=subprocess.DEVNULL, timeout=120)
    assert r.stdout.strip(), f"harness produced nothing:\n{r.stderr[:2000]}"
    failed = [x for x in json.loads(r.stdout) if not x.get("ok")]
    assert not failed, "first-run offer broken:\n" + "\n".join(
        f"  {x.get('n')}: got {x.get('g')!r}, want {x.get('e')!r}" for x in failed)


@pytest.mark.skipif(not _have_jsdom(), reason="node + jsdom not installed (see tests/ui/README)")
def test_utilities_mode_refuses_what_it_must_refuse():
    """Utilities driven end to end, on the mock that now speaks the engine's own
    callbacks — so what passes here is what the real engine gets.

    The load-bearing checks are the refusals. A file whose sample index has
    desynchronised can never be armed for a remux (remuxing it destroys the frames
    a repair could still recover, and reports success), a refusal has to read as a
    refusal rather than a failure, and a DRM file — intact, and playable in
    whatever app it belongs to — must never reach the trash flow.
    """
    r = subprocess.run(["node", "utils.js"], cwd=_UI, capture_output=True, text=True,
                       stdin=subprocess.DEVNULL, timeout=180)
    assert r.stdout.strip(), f"harness produced nothing:\n{r.stderr[:2000]}"
    failed = [x for x in json.loads(r.stdout) if not x.get("ok")]
    assert not failed, "Utilities behaving unsafely:\n" + "\n".join(
        f"  {x.get('n')}: got {x.get('g')!r}, want {x.get('e')!r}" for x in failed)


@pytest.mark.skipif(not _have_jsdom(), reason="node + jsdom not installed (see tests/ui/README)")
def test_an_hours_figure_says_how_solid_it_is():
    """Four sources can price a night of encoding, and they are not equally solid.

    Quoting "about 46 hours" off a five-second preview clip in the same words as a
    measured one makes a guess look like a measurement — at the exact moment
    someone decides whether to commit a night to it. The weak source has to say so.
    """
    r = subprocess.run(["node", "estsource.js"], cwd=_UI, capture_output=True, text=True,
                       stdin=subprocess.DEVNULL, timeout=120)
    assert r.stdout.strip(), f"harness produced nothing:\n{r.stderr[:2000]}"
    failed = [x for x in json.loads(r.stdout) if not x["ok"]]
    assert not failed, "an estimate misstates where it came from:\n" + "\n".join(
        f"  {x['n']}: got {x['g']!r}, want {x['e']!r}" for x in failed)


def _run_all():
    # Ask the question directly. When pytest IS installed the decorator is
    # pytest's own and leaves no attribute behind, so relying on the marker ran
    # the harness on a machine with no jsdom and failed the build.
    if not _have_jsdom():
        print("  (node/jsdom not installed — UI harness skipped)")
        return
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"  ok  {fn.__name__}")
    print(f"\n{len(fns)} UI test(s) passed.")


if __name__ == "__main__":
    _run_all()
