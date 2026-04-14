#!/usr/bin/env python3
"""
USB Relay Manager - File Uploader

Pushes selected files to /sdcard/Download/ on the connected device via
`adb push`. Supports multi-file selection with progress tracking and
overwrite confirmation.
"""

import subprocess
import sys
import threading
from pathlib import Path
from typing import Callable, List, Optional

IS_WINDOWS = sys.platform == 'win32'

DEST_DOWNLOAD = '/sdcard/Download/'


def _subprocess_kwargs():
    """Platform-specific subprocess keyword arguments."""
    kwargs = {}
    if IS_WINDOWS and hasattr(subprocess, 'CREATE_NO_WINDOW'):
        kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW
    return kwargs


class FileUploader:
    """Manages file pushes to Android devices via ADB.

    All files are pushed to /sdcard/Download/. The caller owns file
    selection state (add/remove/clear) and then calls `upload` or
    `upload_async` to deliver them.
    """

    def __init__(
        self,
        adb_path: Path,
        on_log: Callable[[str, str], None],
        on_progress: Callable[[int, int], None],
        on_complete: Callable[[int, int], None],
        on_overwrite_prompt: Callable[[str], bool],
    ):
        self.adb_path = adb_path
        self.on_log = on_log
        self.on_progress = on_progress
        self.on_complete = on_complete
        self.on_overwrite_prompt = on_overwrite_prompt

        self.selected_files: List[Path] = []
        self._uploading = False
        self._upload_thread: Optional[threading.Thread] = None

    # ----- selection management -----

    def set_files(self, files: List[Path]):
        """Set the selected file list."""
        self.selected_files = list(files)

    def add_files(self, files: List[Path]):
        """Add files to the selection, skipping duplicates."""
        existing = set(self.selected_files)
        for f in files:
            if f not in existing:
                self.selected_files.append(f)
                existing.add(f)

    def remove_file(self, file_path: Path):
        """Remove a single file from the selection."""
        self.selected_files = [f for f in self.selected_files if f != file_path]

    def clear_files(self):
        """Clear the selected file list."""
        self.selected_files = []

    def has_files(self) -> bool:
        """Check if any files are selected."""
        return len(self.selected_files) > 0

    def get_files(self) -> List[Path]:
        """Return a copy of the selected file list."""
        return list(self.selected_files)

    def is_uploading(self) -> bool:
        """Check if an upload is in progress."""
        return self._uploading

    # ----- upload dispatch -----

    def upload(self, device_id: str):
        """Push all selected files to /sdcard/Download/. Runs synchronously."""
        if self._uploading:
            return
        self._uploading = True
        total = len(self.selected_files)
        success_count = 0

        for i, file_path in enumerate(self.selected_files):
            if not file_path.exists():
                self.on_log(f"File not found: {file_path}", 'error')
                self.on_progress(i + 1, total)
                continue

            filename = file_path.name
            dest = DEST_DOWNLOAD

            if self._file_exists_on_device(device_id, filename, dest):
                if not self.on_overwrite_prompt(filename):
                    self.on_log(f"Skipped {filename} (already exists)", 'warning')
                    self.on_progress(i + 1, total)
                    continue

            if self._push_file(device_id, file_path, dest):
                success_count += 1

            self.on_progress(i + 1, total)

        self._uploading = False
        self.on_complete(success_count, total)

    def upload_async(self, device_id: str):
        """Start upload on a background thread."""
        self._upload_thread = threading.Thread(
            target=self.upload,
            args=(device_id,),
            daemon=True
        )
        self._upload_thread.start()

    # ----- low-level adb helpers -----

    def _file_exists_on_device(self, device_id: str, filename: str, dest: str) -> bool:
        """Check if a file exists on the device at the destination path."""
        remote_path = f'{dest}{filename}'
        try:
            # Use 'test -f' to avoid shell glob interpretation that 'ls' has.
            result = subprocess.run(
                [str(self.adb_path), '-s', device_id, 'shell',
                 f'test -f "{remote_path}"'],
                capture_output=True, text=True, timeout=10,
                cwd=str(self.adb_path.parent),
                **_subprocess_kwargs()
            )
            return result.returncode == 0
        except Exception as e:
            self.on_log(f"Error checking device file: {e}", 'warning')
            return False

    def _push_file(self, device_id: str, file_path: Path, dest: str) -> bool:
        """Push a single file to the device. Returns True on success."""
        filename = file_path.name
        self.on_log(f"Uploading {filename} → {dest}", 'info')
        try:
            result = subprocess.run(
                [str(self.adb_path), '-s', device_id, 'push',
                 str(file_path), f'{dest}{filename}'],
                capture_output=True, text=True, timeout=300,
                cwd=str(self.adb_path.parent),
                **_subprocess_kwargs()
            )
            if result.returncode == 0:
                self.on_log(f"Uploaded {filename}", 'success')
                return True
            else:
                error = result.stderr.strip() or result.stdout.strip()
                self.on_log(f"Failed to upload {filename}: {error}", 'error')
                return False
        except Exception as e:
            self.on_log(f"Failed to upload {filename}: {e}", 'error')
            return False
