# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec — macOS build of "Very Thoughtful Compression" (the GUI).

Produces a windowed macOS .app (WKWebView via pywebview) bundling:
  - the Python runtime + pywebview (+ pyobjc backend)
  - the vtc engine package
  - the interface: vtc/vtc_app_v3.html (loaded from the bundle root at runtime)
  - static ffmpeg AND ffprobe (both resolved by vtc.webapp at runtime)

The two binaries are passed via FFMPEG_BINARY_PATH / FFPROBE_BINARY_PATH; the
build script (packaging/build_gui_app.sh) sets them. PyInstaller cannot
cross-compile — the Windows build uses vtc-gui-win.spec on a Windows runner.
"""

import os
import re

repo_root = os.path.dirname(SPECPATH)

# The version comes from the package, never typed here. These two keys were
# hardcoded at 1.0.1 and Finder reported 1.0.1 for every release from August
# onwards — the .app's own Get Info was two minor versions behind the code it
# contained. Parsed rather than imported so the build does not depend on the
# package being importable in the spec's interpreter.
_init = os.path.join(repo_root, 'vtc', '__init__.py')
with open(_init, encoding='utf-8') as _f:
    _m = re.search(r'^__version__\s*=\s*[\'"]([^\'"]+)[\'"]', _f.read(), re.M)
if not _m:
    raise SystemExit(f'cannot find __version__ in {_init} — refusing to build an unlabelled app')
VTC_VERSION = _m.group(1)
ffmpeg = os.environ.get('FFMPEG_BINARY_PATH')
ffprobe = os.environ.get('FFPROBE_BINARY_PATH')
for label, path in (('FFMPEG_BINARY_PATH', ffmpeg), ('FFPROBE_BINARY_PATH', ffprobe)):
    if not path or not os.path.exists(path):
        raise SystemExit(f"{label} must point to a static binary. "
                         f"Run packaging/build_gui_app.sh, which sets both for you.")

html = os.path.join(repo_root, 'vtc', 'vtc_app_v3.html')
if not os.path.exists(html):
    raise SystemExit("vtc/vtc_app_v3.html missing from the repo.")

a = Analysis(
    [os.path.join(repo_root, 'packaging', 'vtc_app.py')],
    pathex=[repo_root],
    binaries=[(ffmpeg, '.'), (ffprobe, '.')],
    datas=[(html, '.')],                       # -> bundle root, next to the exe
    hiddenimports=['webview', 'webview.platforms.cocoa',
                   'vtc', 'vtc.webapp', 'vtc.pipeline', 'vtc.encode',
                   'vtc.ffprobe', 'vtc.model', 'vtc.config', 'vtc.ledger',
                   'vtc.report', 'vtc.result', 'vtc.netmove', 'vtc.winproc',
                   # Utilities and the benchmark: reached through vtc.webapp, so
                   # static analysis should find them — listed anyway, because a
                   # module missing from a SHIPPED bundle fails at the worst moment.
                   'vtc.utilities', 'vtc.mp4index', 'vtc.bench'],
    hookspath=[],
    runtime_hooks=[],
    excludes=['tkinter', 'PIL', 'numpy', 'pytest'],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name='VeryThoughtfulCompression',
    debug=False, strip=False, upx=False,
    console=False,                             # windowed GUI, no Terminal
    argv_emulation=False,
)

coll = COLLECT(
    exe, a.binaries, a.datas,
    strip=False, upx=False,
    name='VeryThoughtfulCompression',
)

app = BUNDLE(
    coll,
    name='Very Thoughtful Compression.app',
    icon=os.path.join(repo_root, 'packaging', 'app_icon.icns'),
    bundle_identifier='com.picniclabs.verythoughtfulcompression',
    info_plist={
        'CFBundleName': 'Very Thoughtful Compression',
        'CFBundleDisplayName': 'Very Thoughtful Compression',
        'CFBundleShortVersionString': VTC_VERSION,
        'CFBundleVersion': VTC_VERSION,    # Finder shows Short; this is the build number
        'NSHighResolutionCapable': True,
        'LSMinimumSystemVersion': '10.14',
    },
)
