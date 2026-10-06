# One native binary per OS/architecture; never package a profile or application data.
from pathlib import Path

root = Path(SPECPATH).parent
analysis = Analysis(
    [str(root / 'login_helper.py')],
    pathex=[str(root)],
    binaries=[],
    datas=[(str(root / 'LICENSE'), '.')] + [(str(path), 'lang') for path in (root / 'lang').glob('*.json')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['pytest', 'coverage', 'mypy', 'ruff'],
    noarchive=False,
    optimize=1,
)
archive = PYZ(analysis.pure)
executable = EXE(
    archive,
    analysis.scripts,
    analysis.binaries,
    analysis.datas,
    [],
    name='tdm-login-helper',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
)
