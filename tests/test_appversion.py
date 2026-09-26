"""Tests for packaging/appversion.py — the built apps' version metadata.

The version was typed into three places and drifted in two of them: the shipped
v1.2 .app told Finder it was 1.0.1, and the Windows .exe went on telling Explorer
1.2 after the .app was fixed. Both specs now derive it from vtc.__version__, so
what is worth testing is the derivation and that neither spec hardcodes again.

Pure text/AST, no PyInstaller needed.

Run:  python tests/test_appversion.py   |   pytest tests/test_appversion.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "packaging"))

from appversion import (numeric_version, read_version, version_tuple,
                        win_version_resource)


def test_reads_the_package_version():
    import vtc
    assert read_version(str(ROOT)) == vtc.__version__


def test_numeric_version_strips_a_prerelease_tag():
    # An Info.plist CFBundleShortVersionString takes one to three integers only.
    assert numeric_version("1.4.0") == "1.4.0"
    assert numeric_version("1.4.0b1") == "1.4.0"
    assert numeric_version("2.0rc1") == "2.0"


def test_version_tuple_is_always_four_ints():
    assert version_tuple("1.4.0") == (1, 4, 0, 0)
    assert version_tuple("1.2") == (1, 2, 0, 0)
    assert version_tuple("1.4.0b1") == (1, 4, 0, 0)
    assert version_tuple("1.2.3.4.5") == (1, 2, 3, 4)


def test_win_resource_carries_the_version_given():
    text = win_version_resource("9.8.7")
    assert "filevers=(9, 8, 7, 0)" in text
    assert "StringStruct('FileVersion', '9.8.7')" in text
    assert "StringStruct('ProductVersion', '9.8.7')" in text


def test_neither_spec_hardcodes_a_version():
    # The regression that started this: a literal version somewhere in the build.
    import vtc
    for name in ("vtc-gui.spec", "vtc-gui-win.spec"):
        text = (ROOT / "packaging" / name).read_text(encoding="utf-8")
        assert "read_version" in text, f"{name} must derive the version"
        body = "\n".join(ln for ln in text.splitlines() if not ln.strip().startswith("#"))
        assert vtc.__version__ not in body, f"{name} hardcodes {vtc.__version__}"


def test_the_stale_checked_in_resource_is_gone():
    assert not (ROOT / "packaging" / "version_win.txt").exists()


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"ok   {name}")
            except AssertionError as e:
                fails += 1
                print(f"FAIL {name}: {e}")
    raise SystemExit(1 if fails else 0)
