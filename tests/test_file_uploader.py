import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from file_uploader import DEST_DOWNLOAD, FileUploader


class TestFileUploaderSelection(unittest.TestCase):
    """Selection state management (add/remove/clear/set)."""

    def setUp(self):
        self.uploader = FileUploader(
            adb_path=Path('/usr/bin/adb'),
            on_log=lambda msg, level='info': None,
            on_progress=lambda current, total: None,
            on_complete=lambda success, total: None,
            on_overwrite_prompt=lambda filename: True,
        )

    def test_initial_selection_empty(self):
        self.assertEqual(self.uploader.selected_files, [])

    def test_set_files(self):
        files = [Path('/tmp/a.txt'), Path('/tmp/b.txt')]
        self.uploader.set_files(files)
        self.assertEqual(self.uploader.selected_files, files)

    def test_clear_files(self):
        self.uploader.set_files([Path('/tmp/a.txt')])
        self.uploader.clear_files()
        self.assertEqual(self.uploader.selected_files, [])

    def test_set_files_replaces_prior_selection(self):
        self.uploader.set_files([Path('/tmp/a.txt')])
        self.uploader.set_files([Path('/tmp/b.txt')])
        self.assertEqual(self.uploader.selected_files, [Path('/tmp/b.txt')])

    def test_has_files(self):
        self.assertFalse(self.uploader.has_files())
        self.uploader.set_files([Path('/tmp/a.txt')])
        self.assertTrue(self.uploader.has_files())

    def test_add_files_appends(self):
        self.uploader.set_files([Path('/tmp/a.txt')])
        self.uploader.add_files([Path('/tmp/b.txt')])
        self.assertEqual(
            self.uploader.selected_files,
            [Path('/tmp/a.txt'), Path('/tmp/b.txt')],
        )

    def test_add_files_skips_duplicates(self):
        self.uploader.set_files([Path('/tmp/a.txt')])
        self.uploader.add_files([Path('/tmp/a.txt'), Path('/tmp/b.txt')])
        self.assertEqual(
            self.uploader.selected_files,
            [Path('/tmp/a.txt'), Path('/tmp/b.txt')],
        )

    def test_remove_file(self):
        self.uploader.set_files([
            Path('/tmp/a.txt'), Path('/tmp/b.txt'), Path('/tmp/c.txt'),
        ])
        self.uploader.remove_file(Path('/tmp/b.txt'))
        self.assertEqual(
            self.uploader.selected_files,
            [Path('/tmp/a.txt'), Path('/tmp/c.txt')],
        )

    def test_remove_file_not_in_list(self):
        self.uploader.set_files([Path('/tmp/a.txt')])
        self.uploader.remove_file(Path('/tmp/z.txt'))
        self.assertEqual(self.uploader.selected_files, [Path('/tmp/a.txt')])


class TestFileUploaderUpload(unittest.TestCase):
    """Upload flow — every file pushes to /sdcard/Download/."""

    def setUp(self):
        self.logs = []
        self.progress = []
        self.complete_args = None

        def log(msg, level='info'):
            self.logs.append((msg, level))

        def progress(current, total):
            self.progress.append((current, total))

        def complete(success, total):
            self.complete_args = (success, total)

        self.uploader = FileUploader(
            adb_path=Path('/usr/bin/adb'),
            on_log=log,
            on_progress=progress,
            on_complete=complete,
            on_overwrite_prompt=lambda filename: True,
        )

    @patch('file_uploader.subprocess.run')
    def test_push_file_success(self, mock_run):
        """A new file uploads cleanly to /sdcard/Download/."""
        ls_result = MagicMock(returncode=1)          # test -f → not found
        push_result = MagicMock(
            returncode=0, stdout='1 file pushed.', stderr=''
        )
        mock_run.side_effect = [ls_result, push_result]

        with tempfile.NamedTemporaryFile(suffix='.txt', delete=False) as f:
            tmp_path = Path(f.name)

        try:
            self.uploader.set_files([tmp_path])
            self.uploader.upload('device123')

            self.assertEqual(self.complete_args, (1, 1))
            push_cmd = mock_run.call_args_list[1][0][0]
            self.assertIn('push', push_cmd)
            self.assertTrue(any(DEST_DOWNLOAD in str(arg) for arg in push_cmd))
        finally:
            tmp_path.unlink()

    @patch('file_uploader.subprocess.run')
    def test_push_file_already_exists_overwrite_declined(self, mock_run):
        """User declines overwrite → file is skipped, push never fires."""
        ls_result = MagicMock(returncode=0)          # test -f → found
        mock_run.return_value = ls_result
        self.uploader.on_overwrite_prompt = lambda filename: False

        with tempfile.NamedTemporaryFile(suffix='.txt', delete=False) as f:
            tmp_path = Path(f.name)

        try:
            self.uploader.set_files([tmp_path])
            self.uploader.upload('device123')

            self.assertEqual(self.complete_args, (0, 1))
            # Only the existence check ran, no push.
            self.assertEqual(mock_run.call_count, 1)
        finally:
            tmp_path.unlink()

    @patch('file_uploader.subprocess.run')
    def test_push_file_already_exists_overwrite_accepted(self, mock_run):
        """User accepts overwrite → push proceeds."""
        ls_result = MagicMock(returncode=0)
        push_result = MagicMock(
            returncode=0, stdout='1 file pushed.', stderr=''
        )
        mock_run.side_effect = [ls_result, push_result]

        with tempfile.NamedTemporaryFile(suffix='.txt', delete=False) as f:
            tmp_path = Path(f.name)

        try:
            self.uploader.set_files([tmp_path])
            self.uploader.upload('device123')

            self.assertEqual(self.complete_args, (1, 1))
            self.assertEqual(mock_run.call_count, 2)
        finally:
            tmp_path.unlink()

    def test_missing_host_file_skipped(self):
        """Files that don't exist on the host are logged and skipped."""
        self.uploader.set_files([Path('/nonexistent/file.txt')])
        self.uploader.upload('device123')

        self.assertEqual(self.complete_args, (0, 1))
        self.assertTrue(any('not found' in msg.lower() for msg, _ in self.logs))

    @patch('file_uploader.subprocess.run')
    def test_push_failure_logged(self, mock_run):
        """An adb push that returns non-zero is logged as an error."""
        ls_result = MagicMock(returncode=1)
        push_result = MagicMock(
            returncode=1, stderr='error: device not found', stdout=''
        )
        mock_run.side_effect = [ls_result, push_result]

        with tempfile.NamedTemporaryFile(suffix='.txt', delete=False) as f:
            tmp_path = Path(f.name)

        try:
            self.uploader.set_files([tmp_path])
            self.uploader.upload('device123')

            self.assertEqual(self.complete_args, (0, 1))
            self.assertTrue(any('failed' in msg.lower() for msg, _ in self.logs))
        finally:
            tmp_path.unlink()

    @patch('file_uploader.subprocess.run')
    def test_multiple_files_progress(self, mock_run):
        """Progress callback fires once per file."""
        ls_result = MagicMock(returncode=1)
        push_result = MagicMock(
            returncode=0, stdout='1 file pushed.', stderr=''
        )
        mock_run.side_effect = [ls_result, push_result, ls_result, push_result]

        files = []
        for i in range(2):
            f = tempfile.NamedTemporaryFile(suffix='.txt', delete=False)
            files.append(Path(f.name))
            f.close()

        try:
            self.uploader.set_files(files)
            self.uploader.upload('device123')

            self.assertEqual(self.complete_args, (2, 2))
            self.assertEqual(self.progress, [(1, 2), (2, 2)])
        finally:
            for f in files:
                f.unlink()


class TestFileUploaderEdgeCases(unittest.TestCase):
    def setUp(self):
        self.logs = []
        self.complete_args = None

        def log(msg, level='info'):
            self.logs.append((msg, level))

        self.uploader = FileUploader(
            adb_path=Path('/usr/bin/adb'),
            on_log=log,
            on_progress=lambda current, total: None,
            on_complete=lambda success, total: setattr(self, 'complete_args', (success, total)),
            on_overwrite_prompt=lambda filename: True,
        )

    @patch('file_uploader.subprocess.run')
    def test_push_timeout_logged(self, mock_run):
        """A push timeout is caught and logged as a failure."""
        ls_result = MagicMock(returncode=1)
        mock_run.side_effect = [
            ls_result,
            subprocess.TimeoutExpired(cmd='adb', timeout=300),
        ]

        with tempfile.NamedTemporaryFile(suffix='.txt', delete=False) as f:
            tmp_path = Path(f.name)

        try:
            self.uploader.set_files([tmp_path])
            self.uploader.upload('device123')

            self.assertEqual(self.complete_args, (0, 1))
            self.assertTrue(any('failed' in msg.lower() for msg, _ in self.logs))
        finally:
            tmp_path.unlink()

    def test_upload_with_no_files(self):
        """Empty selection completes with (0, 0) and no errors."""
        self.uploader.upload('device123')
        self.assertEqual(self.complete_args, (0, 0))

    @patch('file_uploader.subprocess.run')
    def test_files_with_spaces_in_name(self, mock_run):
        """Space-containing filenames round-trip through the push command."""
        ls_result = MagicMock(returncode=1)
        push_result = MagicMock(
            returncode=0, stdout='1 file pushed.', stderr=''
        )
        mock_run.side_effect = [ls_result, push_result]

        with tempfile.NamedTemporaryFile(suffix=' my file.txt', delete=False) as f:
            tmp_path = Path(f.name)

        try:
            self.uploader.set_files([tmp_path])
            self.uploader.upload('device123')

            self.assertEqual(self.complete_args, (1, 1))
            push_cmd = mock_run.call_args_list[1][0][0]
            self.assertTrue(any(' ' in str(arg) for arg in push_cmd))
        finally:
            tmp_path.unlink()

    @patch('file_uploader.subprocess.run')
    def test_file_exists_check_exception_returns_false(self, mock_run):
        """A timeout during the existence check is caught and treated as 'not present'."""
        mock_run.side_effect = [
            subprocess.TimeoutExpired(cmd='adb', timeout=10),
            MagicMock(returncode=0, stdout='1 file pushed.', stderr=''),
        ]

        with tempfile.NamedTemporaryFile(suffix='.txt', delete=False) as f:
            tmp_path = Path(f.name)

        try:
            self.uploader.set_files([tmp_path])
            self.uploader.upload('device123')

            # Falls through to push after existence check swallows the timeout.
            self.assertEqual(self.complete_args, (1, 1))
            self.assertTrue(any('error checking' in msg.lower() for msg, _ in self.logs))
        finally:
            tmp_path.unlink()

    def test_concurrent_upload_blocked(self):
        """A second upload() while one is in flight no-ops."""
        self.uploader._uploading = True
        self.uploader.set_files([Path('/tmp/a.txt')])
        self.uploader.upload('device123')
        self.assertIsNone(self.complete_args)


if __name__ == '__main__':
    unittest.main()
