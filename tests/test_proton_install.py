"""Safety regressions for stock and custom GDK-Proton installation."""

import io
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from minecrafthub import proton
from minecrafthub.log import BolError


class ProtonArchiveTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.base = Path(self._td.name)
        self.destination = self.base / "extract"
        self.destination.mkdir()

    def tearDown(self):
        self._td.cleanup()

    def _reject_member(self, member, payload=None):
        archive = self.base / "bad.tar.gz"
        with tarfile.open(archive, "w:gz") as tf:
            tf.addfile(member, io.BytesIO(payload) if payload is not None else None)
        with tarfile.open(archive) as tf, self.assertRaises(ValueError):
            proton._extract_proton_archive(tf, self.destination)

    def test_path_traversal_is_rejected_without_writing_outside(self):
        member = tarfile.TarInfo("../escaped")
        payload = b"outside"
        member.size = len(payload)

        self._reject_member(member, payload)

        self.assertFalse((self.base / "escaped").exists())

    def test_escaping_symlink_is_rejected(self):
        member = tarfile.TarInfo("GDK-Proton/escape")
        member.type = tarfile.SYMTYPE
        member.linkname = "../../escaped"
        self._reject_member(member)

    def test_hardlink_is_rejected(self):
        member = tarfile.TarInfo("GDK-Proton/hardlink")
        member.type = tarfile.LNKTYPE
        member.linkname = "GDK-Proton/proton"
        self._reject_member(member)

    def test_special_file_is_rejected(self):
        member = tarfile.TarInfo("GDK-Proton/fifo")
        member.type = tarfile.FIFOTYPE
        self._reject_member(member)


class ProtonActivationTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.base = Path(self._td.name)
        self.proton_dir = self.base / "proton"
        self.proton_dir.mkdir()
        self._patch = mock.patch.object(proton, "PROTON_DIR", self.proton_dir)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        self._td.cleanup()

    @staticmethod
    def _engine(path, marker):
        path.mkdir(parents=True)
        (path / "proton").write_text(marker)

    def test_successful_swap_commits_and_removes_rollback(self):
        target = self.proton_dir / "GDK-Proton-active"
        candidate = self.proton_dir / ".staged" / "GDK-Proton-new"
        self._engine(target, "old")
        self._engine(candidate, "new")
        committed = []

        proton._activate_proton(candidate, target,
                                commit=lambda: committed.append(True))

        self.assertEqual((target / "proton").read_text(), "new")
        self.assertEqual(committed, [True])
        self.assertFalse(candidate.exists())
        self.assertFalse(
            target.with_name("." + target.name + ".rollback").exists())

    def test_failed_candidate_rename_restores_active_engine(self):
        target = self.proton_dir / "GDK-Proton-active"
        candidate = self.proton_dir / ".staged" / "GDK-Proton-new"
        self._engine(target, "known-good")
        self._engine(candidate, "candidate")
        original_replace = Path.replace

        def fail_candidate(path, destination):
            if path == candidate:
                raise OSError("simulated candidate rename failure")
            return original_replace(path, destination)

        with mock.patch.object(Path, "replace", autospec=True,
                               side_effect=fail_candidate):
            with self.assertRaises(OSError):
                proton._activate_proton(candidate, target)

        self.assertEqual((target / "proton").read_text(), "known-good")
        self.assertEqual((candidate / "proton").read_text(), "candidate")
        self.assertFalse(
            target.with_name("." + target.name + ".rollback").exists())

    def test_failed_settings_commit_restores_active_engine(self):
        target = self.proton_dir / "GDK-Proton-active"
        candidate = self.proton_dir / ".staged" / "GDK-Proton-new"
        self._engine(target, "known-good")
        self._engine(candidate, "candidate")

        with self.assertRaisesRegex(OSError, "settings"):
            proton._activate_proton(
                candidate, target,
                commit=mock.Mock(side_effect=OSError("settings write failed")))

        self.assertEqual((target / "proton").read_text(), "known-good")
        self.assertFalse(
            target.with_name("." + target.name + ".rollback").exists())

    def test_invalid_custom_archive_keeps_active_engine_and_settings(self):
        active = self.proton_dir / "GDK-Proton-active"
        self._engine(active, "known-good")
        cache = self.base / "cache"
        cache.mkdir()
        archive = cache / "custom-bad.tar.gz"
        with tarfile.open(archive, "w:gz") as tf:
            payload = b"#!/bin/sh\n"
            member = tarfile.TarInfo("not-an-engine/proton")
            member.size = len(payload)
            member.mode = 0o755
            tf.addfile(member, io.BytesIO(payload))
        settings = {
            "proton": str(active),
            "proton_tag": "old",
            "proton_url": "https://example.invalid/bad.tar.gz",
        }

        with mock.patch.object(proton, "CACHE", cache), \
                mock.patch.object(proton, "load_settings",
                                  return_value=settings), \
                mock.patch.object(proton, "save_settings") as save:
            with self.assertRaises(BolError):
                proton.ensure_proton()

        self.assertEqual((active / "proton").read_text(), "known-good")
        save.assert_not_called()

    def test_invalid_stock_archive_keeps_active_engine_and_settings(self):
        active = self.proton_dir / "GDK-Proton-active"
        self._engine(active, "known-good")
        cache = self.base / "cache"
        cache.mkdir()
        archive = cache / "bad-stock.tar.gz"
        with tarfile.open(archive, "w:gz") as tf:
            payload = b"#!/bin/sh\n"
            member = tarfile.TarInfo("GDK-Proton-bad/proton")
            member.size = len(payload)
            member.mode = 0o755
            tf.addfile(member, io.BytesIO(payload))
        settings = {"proton": str(active), "proton_tag": "old"}
        release = {
            "tag_name": "new",
            "assets": [{
                "name": archive.name,
                "browser_download_url": "https://example.invalid/stock",
            }],
        }

        with mock.patch.object(proton, "CACHE", cache), \
                mock.patch.object(proton, "load_settings",
                                  return_value=settings), \
                mock.patch.object(proton, "gh_latest", return_value=release), \
                mock.patch.object(proton, "save_settings") as save:
            with self.assertRaises(BolError):
                proton.ensure_proton(force=True)

        self.assertEqual((active / "proton").read_text(), "known-good")
        save.assert_not_called()


class NewWineGDKPatchTests(unittest.TestCase):
    def test_combase_stub_changes_only_four_bytes_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "engine"
            dll_dir = root / "files/lib/wine/x86_64-windows"
            dll_dir.mkdir(parents=True)
            combase_path = dll_dir / "combase.dll"
            ntdll_path = dll_dir / "ntdll.dll"
            original = b"PREFIX!!" + bytes.fromhex("4883ec28") + b"BODY-NEIGHBOR"
            combase_path.write_bytes(original)
            ntdll_path.write_bytes(b"fixture")

            class FakeCombase:
                @staticmethod
                def export_off(name):
                    return 8 if name == "RoOriginateErrorW" else None

            class FakeNtdll:
                data = bytes.fromhex("b8020000c0c3")

                @staticmethod
                def export_rva(_name):
                    return None

                @staticmethod
                def export_off(name):
                    return 0 if name == "NtQueryWnfStateData" else None

            def fake_pe(path):
                return FakeCombase() if Path(path) == combase_path else FakeNtdll()

            with mock.patch.object(proton, "PE", side_effect=fake_pe), \
                    mock.patch.object(proton, "WINEGDK_OUT",
                                      Path(td) / "different-engine"):
                proton.patch_proton(root)
                first = combase_path.read_bytes()
                proton.patch_proton(root)
                second = combase_path.read_bytes()
                backup = combase_path.with_suffix(
                    ".dll.bol-orig").read_bytes()

        expected = original[:8] + bytes.fromhex("31c0c390") + original[12:]
        self.assertEqual(first, expected)
        self.assertEqual(second, expected)
        self.assertEqual(len(second), len(original))
        self.assertEqual(backup, original)

    def test_direct_ntquerywnf_status_not_implemented_stub_is_safe(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "engine"
            dll_dir = root / "files/lib/wine/x86_64-windows"
            dll_dir.mkdir(parents=True)
            (dll_dir / "combase.dll").write_bytes(b"fixture")
            (dll_dir / "ntdll.dll").write_bytes(b"fixture")

            combase = mock.Mock()
            combase.export_off.return_value = 0
            ntdll = mock.Mock()
            ntdll.data = bytes.fromhex("b8020000c0c3")
            ntdll.export_rva.return_value = None
            ntdll.export_off.return_value = 0

            with mock.patch.object(proton, "PE",
                                   side_effect=[combase, ntdll]), \
                    mock.patch.object(proton, "apply_patch"), \
                    mock.patch.object(proton, "WINEGDK_OUT",
                                      Path(td) / "different-engine"), \
                    mock.patch.object(proton, "die") as die, \
                    mock.patch.object(proton, "warn") as warn, \
                    mock.patch.object(proton, "info") as info:
                proton.patch_proton(root)

        die.assert_not_called()
        warn.assert_not_called()
        self.assertTrue(any(
            "NtQueryWnfStateData" in str(call.args[0])
            for call in info.call_args_list))

    def test_guarded_winegdk_ntquerywnf_stub_is_safe(self):
        """The pinned r12 build retains logging and clears buffer_size."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "engine"
            dll_dir = root / "files/lib/wine/x86_64-windows"
            dll_dir.mkdir(parents=True)
            (dll_dir / "combase.dll").write_bytes(b"fixture")
            (dll_dir / "ntdll.dll").write_bytes(b"fixture")

            combase = mock.Mock()
            combase.export_off.return_value = 0
            ntdll = mock.Mock()
            ntdll.data = (
                bytes.fromhex("534883ec50488b9c2488000000")
                + bytes.fromhex(
                    "4885db7406c70300000000b8020000c04883c4505bc3")
            )
            ntdll.export_rva.return_value = None
            ntdll.export_off.return_value = 0

            with mock.patch.object(proton, "PE",
                                   side_effect=[combase, ntdll]), \
                    mock.patch.object(proton, "apply_patch"), \
                    mock.patch.object(proton, "WINEGDK_OUT",
                                      Path(td) / "different-engine"), \
                    mock.patch.object(proton, "warn") as warn, \
                    mock.patch.object(proton, "info") as info:
                proton.patch_proton(root)

        warn.assert_not_called()
        self.assertTrue(any(
            "NtQueryWnfStateData" in str(call.args[0])
            for call in info.call_args_list))


if __name__ == "__main__":
    unittest.main()
