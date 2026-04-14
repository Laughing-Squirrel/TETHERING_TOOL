# -*- mode: python ; coding: utf-8 -*-
# USBRelay.macos.spec - PyInstaller spec for macOS build

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

# tkinterdnd2 ships a Tcl extension (TkDnD) as a bundled data directory.
# collect_data_files / collect_dynamic_libs return [] if the package
# isn't installed, so the build still succeeds (just without
# drag-and-drop in the packaged .app).
_tkdnd_datas = collect_data_files('tkinterdnd2')
_tkdnd_binaries = collect_dynamic_libs('tkinterdnd2')

a = Analysis(
    ['src/main.py'],
    pathex=[],
    binaries=_tkdnd_binaries,
    datas=[
        ('resources/scan_logo.png', '.'),
        ('resources/gnirehtet.apk', '.'),
    ] + _tkdnd_datas,
    hiddenimports=['gui', 'relay_manager', 'adb_monitor', 'tkinterdnd2'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='USBRelay',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='USBRelay',
)

app = BUNDLE(
    coll,
    name='USBRelay.app',
    icon=None,
    bundle_identifier='com.scan.usbrelay',
    info_plist={
        'CFBundleName': 'USB Relay Manager',
        'CFBundleDisplayName': 'USB Relay Manager',
        'CFBundleShortVersionString': '1.0.0',
        'CFBundleVersion': '1.0.0',
        'NSHighResolutionCapable': True,
    },
)
