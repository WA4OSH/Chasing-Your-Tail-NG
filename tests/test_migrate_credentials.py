import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from migrate_credentials import migrate_credentials


class CredentialMigrationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.source = self.root / "config.json"
        self.destination = self.root / "sanitized.json"
        self.original = b'{"api_keys":{"wigle":{"encoded_token":"test-token"}},"paths":{}}\n'
        self.source.write_bytes(self.original)
        self.manager = Mock()
        modules = patch.dict("sys.modules", {
            "secure_credentials": SimpleNamespace(SecureCredentialManager=lambda: self.manager),
        })
        modules.start()
        self.addCleanup(modules.stop)

    def assert_files(self, *others):
        self.assertEqual(self.source.read_bytes(), self.original)
        self.assertEqual(set(self.root.iterdir()), {self.source, *others})

    def test_migrates_only_to_requested_destination(self):
        self.assertTrue(migrate_credentials(self.source, self.destination))
        self.manager.store_credential.assert_called_once_with("wigle", "encoded_token", "test-token")
        self.assertEqual(json.loads(self.destination.read_bytes()), {"paths": {}})
        self.assert_files(self.destination)

    def test_rejects_existing_file(self):
        self.destination.write_bytes(b"keep this")
        with self.assertRaises(FileExistsError):
            migrate_credentials(self.source, self.destination)
        self.assertEqual(self.destination.read_bytes(), b"keep this")
        self.manager.store_credential.assert_not_called()
        self.assert_files(self.destination)

    def test_rejects_same_path_and_hardlink(self):
        with self.assertRaisesRegex(ValueError, "refers to the source"):
            migrate_credentials(self.source, self.source)
        os.link(self.source, self.destination)
        with self.assertRaisesRegex(ValueError, "refers to the source"):
            migrate_credentials(self.source, self.destination)
        self.manager.store_credential.assert_not_called()
        self.assert_files(self.destination)

    def test_rejects_symlink_to_source_and_dangling_symlink(self):
        try:
            self.destination.symlink_to(self.source)
        except (OSError, NotImplementedError) as error:
            self.skipTest(f"symlinks unavailable: {error}")
        with self.assertRaisesRegex(ValueError, "refers to the source"):
            migrate_credentials(self.source, self.destination)
        self.assertTrue(self.destination.is_symlink())
        self.destination.unlink()
        missing = self.root / "missing.json"
        self.destination.symlink_to(missing)
        with self.assertRaises(FileExistsError):
            migrate_credentials(self.source, self.destination)
        self.assertEqual(self.destination.readlink(), missing)
        self.manager.store_credential.assert_not_called()
        self.assert_files(self.destination)

    def test_write_failure_discards_partial_staging_file(self):
        def fail_after_partial_write(config, output, **kwargs):
            output.write("{")
            raise OSError("write failed")

        with patch("migrate_credentials.json.dump", side_effect=fail_after_partial_write):
            with self.assertRaisesRegex(OSError, "write failed"):
                migrate_credentials(self.source, self.destination)
        self.manager.store_credential.assert_not_called()
        self.assert_files()

    def test_credential_failure_leaves_no_output(self):
        self.manager.store_credential.side_effect = RuntimeError("storage failed")
        with self.assertRaisesRegex(RuntimeError, "storage failed"):
            migrate_credentials(self.source, self.destination)
        self.assert_files()

    def test_destination_created_during_migration_is_preserved(self):
        self.manager.store_credential.side_effect = lambda *_: self.destination.write_bytes(b"new file")
        with self.assertRaises(FileExistsError):
            migrate_credentials(self.source, self.destination)
        self.assertEqual(self.destination.read_bytes(), b"new file")
        self.assert_files(self.destination)

    def test_unsupported_credentials_are_not_silently_dropped(self):
        self.original = b'{"api_keys":{"wigle":{"encoded_token":"test-token","other":"value"}}}'
        self.source.write_bytes(self.original)
        with self.assertRaisesRegex(ValueError, "only api_keys.wigle.encoded_token"):
            migrate_credentials(self.source, self.destination)
        self.manager.store_credential.assert_not_called()
        self.assert_files()
