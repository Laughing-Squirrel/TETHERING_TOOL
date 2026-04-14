#!/usr/bin/env python3
"""
USB Relay Manager - Build Script

Automates the PyInstaller build process to create the final executable.
Supports both Windows (.exe) and macOS (.app) builds.

Usage:
  python build.py              # Auto-detect platform
  python build.py --windows    # Force Windows build
  python build.py --macos      # Force macOS build
  python build.py --mode android|winmobile|both  # Select device modes (default: both)
  python build.py --no-deps    # Skip auto-install of Python dependencies

Auto-installed prerequisites:
  * Python dependencies (requirements.txt) are installed/upgraded at
    the start of every build via `pip install --upgrade -r requirements.txt`.
    Pass --no-deps to skip this for environments you manage manually.
  * On Windows, the Android platform-tools (adb.exe + DLLs) are
    auto-downloaded from Google's official distribution if missing or
    version-mismatched.
  * If the gnirehtet relay binary is missing from resources/, the build
    will compile it from the vendored Rust source in
    vendor/gnirehtet-relay-rust/ (requires Rust: https://rustup.rs/).
  * On macOS, if the current Python's Tcl/Tk is too old for PyInstaller
    .app bundles, a build venv is created automatically using a newer
    Python found on the system.

Based on gnirehtet by Genymobile (https://github.com/Genymobile/gnirehtet)
Licensed under Apache 2.0
"""

import io
import os
import sys
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path
from urllib.request import urlopen
from urllib.error import URLError

IS_WINDOWS = sys.platform == 'win32'
IS_MACOS = sys.platform == 'darwin'


def build_gnirehtet_from_source(project_dir: Path, platform: str) -> bool:
    """Compile gnirehtet relay from vendored Rust source as a fallback."""
    vendor_dir = project_dir / 'vendor' / 'gnirehtet-relay-rust'
    resources_dir = project_dir / 'resources'

    if not (vendor_dir / 'Cargo.toml').exists():
        print("  Vendored source not found at vendor/gnirehtet-relay-rust/")
        return False

    # Check for cargo (Rust toolchain)
    cargo_bin = shutil.which('cargo')
    if not cargo_bin:
        print("  Rust toolchain not found. Install from https://rustup.rs/")
        return False

    print(f"  Found cargo: {cargo_bin}")
    print("  Compiling gnirehtet from vendored source (this may take a minute)...")

    # Determine build command and output path
    cargo_cmd = [cargo_bin, 'build', '--release']
    if platform == 'windows':
        binary_name = 'gnirehtet.exe'
        # Cross-compile for Windows if not on Windows
        if not IS_WINDOWS:
            target = 'x86_64-pc-windows-gnu'
            cargo_cmd += ['--target', target]
            output_binary = vendor_dir / 'target' / target / 'release' / binary_name
        else:
            output_binary = vendor_dir / 'target' / 'release' / binary_name
    else:
        binary_name = 'gnirehtet'
        output_binary = vendor_dir / 'target' / 'release' / binary_name

    try:
        result = subprocess.run(
            cargo_cmd,
            cwd=str(vendor_dir),
            capture_output=True,
            text=True,
            timeout=300
        )
        if result.returncode != 0:
            print(f"  Cargo build failed (exit code {result.returncode}):")
            # Show last few lines of stderr for diagnostics
            for line in result.stderr.strip().splitlines()[-10:]:
                print(f"    {line}")
            return False
    except FileNotFoundError:
        print("  Failed to execute cargo.")
        return False
    except subprocess.TimeoutExpired:
        print("  Cargo build timed out after 5 minutes.")
        return False

    if not output_binary.exists():
        print(f"  Expected binary not found at: {output_binary}")
        return False

    # Copy compiled binary into resources/
    resources_dir.mkdir(exist_ok=True)
    dest = resources_dir / binary_name
    shutil.copy2(str(output_binary), str(dest))
    size_mb = dest.stat().st_size / (1024 * 1024)
    print(f"  Built successfully: {dest} ({size_mb:.1f} MB)")
    return True


PLATFORM_TOOLS_URL = 'https://dl.google.com/android/repository/platform-tools-latest-windows.zip'

# Files that must come from the same platform-tools release
ADB_FILES = ['adb.exe', 'AdbWinApi.dll', 'AdbWinUsbApi.dll']


def download_platform_tools(resources_dir: Path) -> bool:
    """Download official Android SDK Platform Tools and extract ADB files.

    All three files (adb.exe, AdbWinApi.dll, AdbWinUsbApi.dll) are pulled
    from the same zip so they are guaranteed version-matched.
    """
    print(f"  Downloading Android SDK Platform Tools...")
    print(f"  URL: {PLATFORM_TOOLS_URL}")

    try:
        resp = urlopen(PLATFORM_TOOLS_URL, timeout=60)
        data = resp.read()
    except (URLError, OSError) as e:
        print(f"  Download failed: {e}")
        return False

    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            extracted = 0
            for adb_file in ADB_FILES:
                zip_path = f'platform-tools/{adb_file}'
                try:
                    info = zf.getinfo(zip_path)
                except KeyError:
                    print(f"  {adb_file} not found in zip archive")
                    continue

                dest = resources_dir / adb_file
                with zf.open(info) as src, open(dest, 'wb') as dst:
                    dst.write(src.read())
                size_kb = dest.stat().st_size / 1024
                print(f"  Extracted {adb_file} ({size_kb:.0f} KB)")
                extracted += 1

            if extracted == len(ADB_FILES):
                print("  All ADB files downloaded successfully (version-matched)")
                return True
            else:
                print(f"  Only extracted {extracted}/{len(ADB_FILES)} files")
                return False

    except zipfile.BadZipFile:
        print("  Downloaded file is not a valid zip archive")
        return False


def validate_adb_version_match(resources_dir: Path) -> bool:
    """Check that adb.exe and its companion DLLs are from the same release.

    Compares file modification times — files extracted from the same
    platform-tools zip will have timestamps within seconds of each other.
    A large gap suggests they were sourced separately and may be mismatched.
    """
    paths = [resources_dir / f for f in ADB_FILES]

    # Can't validate if any file is missing
    if not all(p.exists() for p in paths):
        return True

    try:
        mtimes = [p.stat().st_mtime for p in paths]
    except OSError:
        return True

    # If modification times differ by more than 24 hours, they likely
    # came from different downloads/releases
    max_gap = max(mtimes) - min(mtimes)
    if max_gap > 86400:  # 24 hours in seconds
        print("  WARNING: ADB files appear to be from different releases!")
        print(f"  Modification time gap: {max_gap / 3600:.0f} hours")
        print("  adb.exe, AdbWinApi.dll, and AdbWinUsbApi.dll must all")
        print("  come from the same platform-tools download.")
        return False

    return True


VENV_DIR_NAME = '.build_venv'

# Minimum Tcl/Tk version required for macOS .app bundles.
# System Tcl/Tk 8.5 is deprecated and crashes in PyInstaller bundles.
MIN_TCL_VERSION = (8, 6)

# Candidate Python interpreters to search on macOS (prefer newer).
MACOS_PYTHON_CANDIDATES = [
    '/opt/homebrew/bin/python3.13',
    '/opt/homebrew/bin/python3.12',
    '/opt/homebrew/bin/python3.11',
    '/opt/homebrew/bin/python3.10',
    '/usr/local/bin/python3.13',
    '/usr/local/bin/python3.12',
    '/usr/local/bin/python3.11',
    '/usr/local/bin/python3.10',
    '/Library/Frameworks/Python.framework/Versions/3.13/bin/python3',
    '/Library/Frameworks/Python.framework/Versions/3.12/bin/python3',
    '/Library/Frameworks/Python.framework/Versions/3.11/bin/python3',
]


def _tcl_version(python_bin: str) -> tuple:
    """Return the Tcl/Tk version tuple for a Python interpreter, or (0,0)."""
    try:
        out = subprocess.check_output(
            [python_bin, '-c',
             'import _tkinter; print(_tkinter.TCL_VERSION)'],
            text=True, timeout=10, stderr=subprocess.DEVNULL,
        ).strip()
        return tuple(int(x) for x in out.split('.'))
    except Exception:
        return (0, 0)


def find_suitable_python():
    """Find a Python with Tcl/Tk >= MIN_TCL_VERSION on macOS."""
    for candidate in MACOS_PYTHON_CANDIDATES:
        if os.path.isfile(candidate) and _tcl_version(candidate) >= MIN_TCL_VERSION:
            return candidate
    return None


def ensure_build_venv(project_dir):
    """Create (or reuse) a venv with PyInstaller using a suitable Python.

    Returns the path to the venv's python binary.
    """
    venv_dir = project_dir / VENV_DIR_NAME
    venv_python = venv_dir / 'bin' / 'python3'

    # Reuse existing venv if it already has a good Tcl/Tk and PyInstaller
    if venv_python.exists():
        if _tcl_version(str(venv_python)) >= MIN_TCL_VERSION:
            has_pi = subprocess.run(
                [str(venv_python), '-m', 'PyInstaller', '--version'],
                capture_output=True, timeout=10,
            ).returncode == 0
            if has_pi:
                print(f"  Reusing existing build venv: {venv_dir}")
                return venv_python

    # Find a suitable base Python
    base_python = find_suitable_python()
    if not base_python:
        return None

    tcl_ver = '.'.join(str(x) for x in _tcl_version(base_python))
    print(f"  Using {base_python} (Tcl/Tk {tcl_ver})")
    print(f"  Creating build venv at {venv_dir} ...")

    # Create fresh venv
    if venv_dir.exists():
        shutil.rmtree(venv_dir)
    subprocess.check_call([base_python, '-m', 'venv', str(venv_dir)])

    # Install build + runtime dependencies inside the venv from
    # requirements.txt. This pulls in PyInstaller plus any optional
    # runtime deps (like tkinterdnd2 for drag-and-drop) so the
    # packaged .app has them available. Falls back to a bare
    # PyInstaller install if requirements.txt is missing.
    pip = venv_dir / 'bin' / 'pip'
    req_path = project_dir / 'requirements.txt'
    if req_path.exists():
        print(f"  Installing build dependencies in venv from {req_path.name}...")
        subprocess.check_call(
            [str(pip), 'install', '--quiet', '-r', str(req_path)],
            stdout=subprocess.DEVNULL,
        )
    else:
        print("  Installing PyInstaller in venv (no requirements.txt found)...")
        subprocess.check_call(
            [str(pip), 'install', '--quiet', 'pyinstaller'],
            stdout=subprocess.DEVNULL,
        )

    return venv_python


VALID_MODES = ('android', 'winmobile', 'both')


def detect_mode(args: list) -> str:
    """Parse --mode flag from command line arguments."""
    for i, arg in enumerate(args):
        if arg == '--mode' and i + 1 < len(args):
            return args[i + 1]
    return 'both'


def write_build_config(project_dir: Path, mode: str):
    """Write src/build_config.py with the selected build options."""
    if mode == 'both':
        modes = ['android', 'winmobile']
    else:
        modes = [mode]

    config_path = project_dir / 'src' / 'build_config.py'
    config_path.write_text(
        '"""\nUSB Relay Manager - Build Configuration\n\n'
        'This module is overwritten by build.py at build time to reflect the\n'
        'selected --mode option. The defaults here are used when running\n'
        'from source during development.\n"""\n\n'
        f'ENABLED_MODES = {modes!r}\n'
    )
    print(f"  Wrote build_config.py: ENABLED_MODES = {modes!r}")


def generate_spec(project_dir: Path, platform: str, mode: str) -> Path:
    """Generate a PyInstaller spec file tailored to the selected mode.

    Returns the path to the generated spec file.
    """
    android = mode in ('android', 'both')
    winmobile = mode in ('winmobile', 'both')

    # --- Binaries ---
    binaries = []
    if platform == 'windows' and android:
        binaries.extend([
            "('resources/gnirehtet.exe', '.')",
            "('resources/adb.exe', '.')",
            "('resources/AdbWinApi.dll', '.')",
            "('resources/AdbWinUsbApi.dll', '.')",
        ])
    elif platform == 'macos' and android:
        binaries.extend([
            "('resources/gnirehtet', '.')",
            "('resources/adb', '.')",
        ])

    # --- Data files ---
    datas = []
    if android:
        datas.append("('resources/gnirehtet.apk', '.')")

    # --- Hidden imports ---
    hiddenimports = ['gui', 'build_config']
    if android:
        hiddenimports.extend(['relay_manager', 'adb_monitor', 'file_uploader'])
    if winmobile:
        hiddenimports.extend(['wmdc_monitor', 'dhcp_server'])
    # Optional: drag-and-drop support. PyInstaller emits a warning and
    # produces a working (but drag-drop-less) binary if tkinterdnd2
    # isn't installed in the build environment.
    hiddenimports.append('tkinterdnd2')

    binaries_str = ',\n        '.join(binaries)
    datas_str = ',\n        '.join(datas)
    hiddenimports_str = repr(hiddenimports)

    # --- UPX exclusions (only for bundled android binaries) ---
    upx_exclude = []
    if platform == 'windows' and android:
        upx_exclude = ['adb.exe', 'gnirehtet.exe', 'AdbWinApi.dll', 'AdbWinUsbApi.dll']

    if platform == 'windows':
        spec_content = f"""\
# -*- mode: python ; coding: utf-8 -*-
# Auto-generated by build.py (mode={mode})

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

# Optional drag-and-drop support via tkinterdnd2. collect_data_files
# and collect_dynamic_libs return [] if the package is not installed
# in the build environment, so the build still succeeds without it.
_tkdnd_datas = collect_data_files('tkinterdnd2')
_tkdnd_binaries = collect_dynamic_libs('tkinterdnd2')

a = Analysis(
    ['src/main.py'],
    pathex=[],
    binaries=[
        {binaries_str}
    ] + _tkdnd_binaries,
    datas=[
        {datas_str}
    ] + _tkdnd_datas,
    hiddenimports={hiddenimports_str},
    hookspath=[],
    hooksconfig={{}},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='USBRelay',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude={upx_exclude!r},
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)
"""
    else:
        # macOS spec
        spec_content = f"""\
# -*- mode: python ; coding: utf-8 -*-
# Auto-generated by build.py (mode={mode})

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

# Optional drag-and-drop support via tkinterdnd2. collect_data_files
# and collect_dynamic_libs return [] if the package is not installed
# in the build environment, so the build still succeeds without it.
_tkdnd_datas = collect_data_files('tkinterdnd2')
_tkdnd_binaries = collect_dynamic_libs('tkinterdnd2')

a = Analysis(
    ['src/main.py'],
    pathex=[],
    binaries=[
        {binaries_str}
    ] + _tkdnd_binaries,
    datas=[
        {datas_str}
    ] + _tkdnd_datas,
    hiddenimports={hiddenimports_str},
    hookspath=[],
    hooksconfig={{}},
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
    bundle_identifier='com.securenode.usbrelay',
    info_plist={{
        'CFBundleName': 'USB Relay Manager',
        'CFBundleDisplayName': 'USB Relay Manager',
        'CFBundleShortVersionString': '1.0.0',
        'CFBundleVersion': '1.0.0',
        'NSHighResolutionCapable': True,
    }},
)
"""

    # Write to a temp file inside the project so PyInstaller resolves
    # relative resource paths correctly.
    spec_path = project_dir / f'USBRelay.generated.spec'
    spec_path.write_text(spec_content)
    return spec_path


def check_resources(project_dir: Path, platform: str, mode: str = 'both') -> bool:
    """Verify all required resources are present for the target platform.

    Only checks resources that are actually needed for the selected mode.
    If the gnirehtet relay binary is missing, attempts to compile it from
    the vendored Rust source before giving up.
    """
    resources_dir = project_dir / 'resources'
    android = mode in ('android', 'both')

    required_files = []

    if platform == 'windows':
        if android:
            gnirehtet_binary = 'gnirehtet.exe'
            required_files.extend([
                gnirehtet_binary,
                'adb.exe',
                'AdbWinApi.dll',
                'AdbWinUsbApi.dll',
                'gnirehtet.apk',
            ])
    else:
        if android:
            gnirehtet_binary = 'gnirehtet'
            required_files.extend([
                gnirehtet_binary,
                'adb',
                'gnirehtet.apk',
            ])

    # Build gnirehtet from source if missing
    if android:
        gnirehtet_binary = 'gnirehtet.exe' if platform == 'windows' else 'gnirehtet'
        if not (resources_dir / gnirehtet_binary).exists():
            print(f"  {gnirehtet_binary} not found in resources/, building from source...")
            if not build_gnirehtet_from_source(project_dir, platform):
                print(f"  Could not build {gnirehtet_binary} from source.")

    # On Windows, auto-download ADB files if missing or version-mismatched
    if platform == 'windows' and android:
        adb_missing = any(
            not (resources_dir / f).exists() for f in ADB_FILES
        )
        adb_mismatched = not validate_adb_version_match(resources_dir)

        if adb_missing or adb_mismatched:
            reason = "missing" if adb_missing else "version-mismatched"
            print(f"  ADB files are {reason}, downloading from official release...")
            if not download_platform_tools(resources_dir):
                print("  Failed to download platform-tools.")
                print("  Manually download from: https://developer.android.com/tools/releases/platform-tools")
                if adb_mismatched:
                    print("  IMPORTANT: adb.exe, AdbWinApi.dll, and AdbWinUsbApi.dll")
                    print("  must ALL come from the same platform-tools release.")

    missing = []
    for filename in required_files:
        if not (resources_dir / filename).exists():
            missing.append(filename)

    if missing:
        print(f"ERROR: Missing required resources for {platform} (mode={mode}):")
        for f in missing:
            print(f"  - {f}")
        print(f"\nPlease ensure these files are in: {resources_dir}")

        if android:
            gnirehtet_binary = 'gnirehtet.exe' if platform == 'windows' else 'gnirehtet'
            if gnirehtet_binary in missing:
                print(f"\n  To build gnirehtet from source, install Rust (https://rustup.rs/)")
                print(f"  and re-run this build script.")
            if platform == 'macos' and 'adb' in missing:
                print("\n  adb: Download Android SDK Platform Tools for macOS from")
                print("       https://developer.android.com/tools/releases/platform-tools")
        return False

    # Final version validation (after any downloads)
    if platform == 'windows' and android and not validate_adb_version_match(resources_dir):
        print("ERROR: ADB version mismatch persists after download attempt.")
        print("Manually replace adb.exe, AdbWinApi.dll, and AdbWinUsbApi.dll")
        print("with files from the same platform-tools release.")
        return False

    return True


def clean_build(project_dir: Path):
    """Clean previous build artifacts."""
    dirs_to_clean = ['build', 'dist', '__pycache__']

    for dir_name in dirs_to_clean:
        dir_path = project_dir / dir_name
        if dir_path.exists():
            print(f"Cleaning {dir_path}...")
            try:
                shutil.rmtree(dir_path)
            except PermissionError as e:
                print(f"WARNING: Could not delete {dir_path}")
                print(f"  {e}")
                if IS_WINDOWS:
                    print("  Close any running USBRelay.exe and try again.")
                    print("  Or run: taskkill /f /im USBRelay.exe")
                else:
                    print("  Close any running USBRelay and try again.")
                    print("  Or run: pkill -f USBRelay")
                return False

    # Clean pycache in src
    src_pycache = project_dir / 'src' / '__pycache__'
    if src_pycache.exists():
        try:
            shutil.rmtree(src_pycache)
        except PermissionError:
            pass  # Ignore pycache errors

    return True


def run_pyinstaller(project_dir: Path, platform: str, spec_file: Path,
                     python_bin=None) -> bool:
    """Run PyInstaller to build the executable using the given spec file."""
    if not spec_file.exists():
        print(f"ERROR: Spec file not found: {spec_file}")
        return False

    python = python_bin or sys.executable

    print(f"\nBuilding {platform} application with PyInstaller...")
    print(f"Spec file: {spec_file.name}")
    print(f"Python:    {python}")
    print("-" * 50)

    try:
        result = subprocess.run(
            [python, '-m', 'PyInstaller', str(spec_file), '--clean'],
            cwd=str(project_dir),
            check=True
        )
        return result.returncode == 0
    except subprocess.CalledProcessError as e:
        print(f"ERROR: PyInstaller failed with code {e.returncode}")
        return False
    except FileNotFoundError:
        print("ERROR: PyInstaller not found. Install with: pip install pyinstaller")
        return False


def verify_output(project_dir: Path, platform: str) -> bool:
    """Verify the build output exists."""
    if platform == 'macos':
        app_path = project_dir / 'dist' / 'USBRelay.app'
        if app_path.exists():
            # Calculate total .app bundle size
            total_size = sum(
                f.stat().st_size for f in app_path.rglob('*') if f.is_file()
            )
            size_mb = total_size / (1024 * 1024)
            print(f"\nBuild successful!")
            print(f"Output: {app_path}")
            print(f"Size: {size_mb:.1f} MB")

            # Create zip for distribution
            zip_path = project_dir / 'dist' / 'USBRelay.app.zip'
            print(f"\nCreating distribution archive: {zip_path}")
            shutil.make_archive(
                str(project_dir / 'dist' / 'USBRelay.app'),
                'zip',
                str(project_dir / 'dist'),
                'USBRelay.app'
            )
            if zip_path.exists():
                zip_mb = zip_path.stat().st_size / (1024 * 1024)
                print(f"Archive: {zip_path} ({zip_mb:.1f} MB)")
            return True
        else:
            print("\nERROR: Build output not found")
            return False
    else:
        exe_path = project_dir / 'dist' / 'USBRelay.exe'
        if exe_path.exists():
            size_mb = exe_path.stat().st_size / (1024 * 1024)
            print(f"\nBuild successful!")
            print(f"Output: {exe_path}")
            print(f"Size: {size_mb:.1f} MB")
            return True
        else:
            print("\nERROR: Build output not found")
            return False


def detect_platform(args: list) -> str:
    """Detect target platform from args or current OS."""
    if '--windows' in args:
        return 'windows'
    if '--macos' in args:
        return 'macos'
    if IS_WINDOWS:
        return 'windows'
    if IS_MACOS:
        return 'macos'
    return 'unknown'


def _run_pip_install(python, extra_args, timeout=300):
    """Invoke pip install with the given extra args. Returns (rc, stdout, stderr).

    rc == -1 indicates pip itself could not be invoked (missing, timed
    out, etc.). Any other rc is pip's own exit code.
    """
    try:
        result = subprocess.run(
            [str(python), '-m', 'pip', 'install', *extra_args],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return result.returncode, result.stdout or '', result.stderr or ''
    except FileNotFoundError:
        return -1, '', 'pip not found for this Python interpreter'
    except subprocess.TimeoutExpired:
        return -1, '', 'pip install timed out after 5 minutes'


def install_python_deps(project_dir: Path, python_bin=None) -> bool:
    """Install / upgrade Python dependencies from requirements.txt.

    Runs `{python} -m pip install --upgrade -r requirements.txt` with
    the given interpreter (defaults to the one running build.py).

    Handles PEP 668 "externally managed environment" errors (Homebrew
    Python on macOS, Debian/Ubuntu system Python, etc.) by retrying
    with `--break-system-packages --user`. Works as-is on Windows,
    inside venvs, and on any non-externally-managed Python.

    The build continues on partial failures — the resource checks and
    PyInstaller run that follow will surface anything actually broken.
    Returns True unless continuing is pointless (currently always True,
    since failures degrade rather than abort). The return value exists
    so future logic can choose to fail-fast on a dep problem if needed.

    The --no-deps command-line flag bypasses this step entirely for
    environments the user manages manually.
    """
    req_path = project_dir / 'requirements.txt'
    if not req_path.exists():
        print(f"  requirements.txt not found at {req_path}; skipping")
        return True

    python = python_bin or sys.executable
    print(f"  Using Python: {python}")
    print(f"  Source:       {req_path.name}")

    base_args = ['--upgrade', '-r', str(req_path)]
    rc, stdout, stderr = _run_pip_install(python, base_args)

    # PEP 668 retry path. Externally-managed-environment errors contain
    # either "externally-managed" or a pointer at --break-system-packages
    # in the error text; match on either so we catch distro variants.
    if rc > 0:
        lowered = stderr.lower()
        is_externally_managed = (
            'externally-managed' in lowered
            or 'externally managed' in lowered
            or 'break-system-packages' in lowered
        )
        if is_externally_managed:
            print("  Python environment is externally-managed (PEP 668)")
            print("  Retrying with --break-system-packages --user")
            retry_args = ['--break-system-packages', '--user'] + base_args
            rc, stdout, stderr = _run_pip_install(python, retry_args)

    if rc == -1:
        # Setup error — pip missing or timed out. stderr holds the reason.
        print(f"  WARNING: {stderr}")
        print("  Run manually: pip install -r requirements.txt")
        return True

    if rc != 0:
        print(f"  WARNING: pip install returned exit code {rc}")
        for line in stderr.strip().splitlines()[-10:]:
            print(f"    {line}")
        print("  Build will continue. Features depending on any package")
        print("  that failed to install may not work at runtime. Run")
        print("  manually if needed: pip install -r requirements.txt")
        return True

    # Summarise pip's output: prefer "Successfully installed" lines when
    # anything actually changed, otherwise show the "already satisfied"
    # count so the user can see the check happened.
    installed_lines = [l for l in stdout.splitlines()
                       if l.strip().startswith('Successfully installed')]
    already_count = sum(1 for l in stdout.splitlines()
                        if 'Requirement already satisfied' in l)

    if installed_lines:
        for line in installed_lines:
            print(f"  {line.strip()}")
    elif already_count:
        print(f"  All dependencies already satisfied ({already_count} packages)")
    else:
        print("  pip install completed")
    return True


def main():
    """Main build process."""
    # Get project directory (where this script is located)
    project_dir = Path(__file__).parent.absolute()
    platform = detect_platform(sys.argv)
    mode = detect_mode(sys.argv)
    print("=" * 50)
    print("USB Relay Manager - Build Script")
    print("=" * 50)
    print(f"\nProject directory: {project_dir}")
    print(f"Target platform:  {platform}")
    print(f"Build mode:       {mode}")

    if platform == 'unknown':
        print("\nERROR: Unsupported platform. Use --windows or --macos to specify.")
        return 1

    if mode not in VALID_MODES:
        print(f"\nERROR: Invalid mode '{mode}'. Must be one of: {', '.join(VALID_MODES)}")
        return 1

    # Windows Mobile mode requires Windows platform
    if mode == 'winmobile' and platform != 'windows':
        print(f"\nERROR: Windows Mobile mode requires --windows platform.")
        print("Windows Mobile tethering is only available on Windows.")
        return 1

    # Cross-compilation warning
    if platform == 'windows' and not IS_WINDOWS:
        print("\nWARNING: Building Windows target on non-Windows platform.")
        print("The resulting executable may not work. Build on Windows for best results.")
        response = input("Continue anyway? (y/n): ")
        if response.lower() != 'y':
            print("Build cancelled.")
            return 1
    elif platform == 'macos' and not IS_MACOS:
        print("\nWARNING: Building macOS target on non-macOS platform.")
        print("The resulting app may not work. Build on macOS for best results.")
        response = input("Continue anyway? (y/n): ")
        if response.lower() != 'y':
            print("Build cancelled.")
            return 1

    skip_deps = '--no-deps' in sys.argv

    # Step 0 (macOS only): Ensure we have a Python with working Tcl/Tk.
    # ensure_build_venv installs the full requirements.txt into the venv,
    # so on macOS the Python dep install below is a no-op when a venv
    # was created — but running it still verifies nothing got removed.
    build_python = None
    if platform == 'macos':
        cur_tcl = _tcl_version(sys.executable)
        if cur_tcl < MIN_TCL_VERSION:
            print(f"\n[0/6] Current Python has Tcl/Tk {'.'.join(str(x) for x in cur_tcl)} "
                  f"(need >= {'.'.join(str(x) for x in MIN_TCL_VERSION)})...")
            print("  System Tcl/Tk 8.5 is broken in PyInstaller .app bundles.")
            print("  Setting up a build venv with a compatible Python...")
            venv_python = ensure_build_venv(project_dir)
            if venv_python is None:
                print("\nERROR: No Python with Tcl/Tk >= 8.6 found.")
                print("Install one with:  brew install python@3.12 python-tk@3.12")
                print("Or download from:  https://www.python.org/downloads/")
                return 1
            build_python = str(venv_python)
            tcl_ver = '.'.join(str(x) for x in _tcl_version(build_python))
            print(f"  Build venv ready (Tcl/Tk {tcl_ver})")
        else:
            print(f"\n  Python Tcl/Tk {'.'.join(str(x) for x in cur_tcl)} — OK")

    # Step 1: Install Python dependencies from requirements.txt
    # (unless --no-deps was passed).
    if skip_deps:
        print("\n[1/6] Skipping Python dependency install (--no-deps)")
    else:
        print("\n[1/6] Installing Python dependencies...")
        install_python_deps(project_dir, python_bin=build_python)

    # Step 2: Check resources
    print("\n[2/6] Checking resources...")
    if not check_resources(project_dir, platform, mode):
        return 1
    print("All resources found.")

    # Step 3: Write build config
    print("\n[3/6] Writing build configuration...")
    write_build_config(project_dir, mode)

    # Step 4: Clean previous build
    print("\n[4/6] Cleaning previous build...")
    if not clean_build(project_dir):
        return 1
    print("Clean complete.")

    # Step 5: Generate spec and run PyInstaller
    print("\n[5/6] Running PyInstaller...")
    spec_file = generate_spec(project_dir, platform, mode)
    if not run_pyinstaller(project_dir, platform, spec_file, python_bin=build_python):
        return 1

    # Clean up generated spec file
    if spec_file.exists():
        spec_file.unlink()

    # Step 6: Verify output
    print("\n[6/6] Verifying output...")
    if not verify_output(project_dir, platform):
        return 1

    print("\n" + "=" * 50)
    print(f"Build completed successfully! ({platform}, mode={mode})")
    print("=" * 50)
    return 0


if __name__ == '__main__':
    sys.exit(main())
