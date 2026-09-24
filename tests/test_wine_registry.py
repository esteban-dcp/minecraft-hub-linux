"""Regression tests for GPU-free Wine registry updates."""
# SPDX-License-Identifier: MIT

import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from minecrafthub import wine_registry
from minecrafthub.log import BolError


SYSTEM = b"""WINE REGISTRY Version 2
;; All keys relative to REGISTRY\\Machine

#arch=win64

[Software\\\\Wine\\\\WineGDK] 1
#time=1
\"ForceMsaFacet\"=dword:00000000

[System\\\\CurrentControlSet] 1
#time=1
#link
\"SymbolicLinkValue\"=hex(6):00,00

[System\\\\Select] 1
#time=1
\"Current\"=dword:00000002

"""

USER = b"""WINE REGISTRY Version 2
;; All keys relative to REGISTRY\\User\\S-1-5-21-0-0-0-1000

#arch=win64

[Environment] 1
#time=1
\"TEMP\"=\"C:\\\\Temp\"

"""


class OfflineRegistryTests(unittest.TestCase):
    def make_prefix(self, root):
        prefix = Path(root) / "pfx"
        prefix.mkdir()
        (prefix / "system.reg").write_bytes(SYSTEM)
        (prefix / "user.reg").write_bytes(USER)
        return prefix

    def test_add_replace_delete_and_escape(self):
        with tempfile.TemporaryDirectory() as td:
            prefix = self.make_prefix(td)
            changed = wine_registry.update_prefix_registry(
                prefix,
                machine=[
                    wine_registry.reg_dword(
                        r"Software\Wine\WineGDK", "ForceMsaFacet", 1),
                    wine_registry.reg_sz(
                        r"Software\Wine\WineGDK", "RefreshToken",
                        'safe\\value"quoted'),
                ],
                user=[
                    wine_registry.reg_sz("Environment", "NEW_VALUE", "yes"),
                    wine_registry.reg_delete("Environment", "TEMP"),
                ],
            )
            self.assertTrue(changed)
            system = (prefix / "system.reg").read_text()
            user = (prefix / "user.reg").read_text()
            self.assertIn('"ForceMsaFacet"=dword:00000001', system)
            self.assertIn(
                '"RefreshToken"="safe\\\\value\\"quoted"', system)
            self.assertIn('"NEW_VALUE"="yes"', user)
            self.assertNotIn('"TEMP"=', user)
            self.assertEqual(stat.S_IMODE((prefix / "system.reg").stat().st_mode),
                             0o600)
            self.assertEqual(stat.S_IMODE((prefix / "user.reg").stat().st_mode),
                             0o600)

    def test_new_key_is_idempotent(self):
        with tempfile.TemporaryDirectory() as td:
            prefix = self.make_prefix(td)
            change = wine_registry.reg_dword(
                r"System\CurrentControlSet\Services\Example", "Start", 3)
            self.assertTrue(wine_registry.update_prefix_registry(
                prefix, machine=[change]))
            before = (prefix / "system.reg").read_bytes()
            self.assertFalse(wine_registry.update_prefix_registry(
                prefix, machine=[change]))
            self.assertEqual((prefix / "system.reg").read_bytes(), before)

    def test_current_control_set_targets_selected_concrete_hive(self):
        with tempfile.TemporaryDirectory() as td:
            prefix = self.make_prefix(td)
            change = wine_registry.reg_dword(
                r"System\CurrentControlSet\Services\FreshService", "Start", 3)
            self.assertTrue(wine_registry.update_prefix_registry(
                prefix, machine=[change]))
            system = (prefix / "system.reg").read_text()
            self.assertIn(
                r"[System\\ControlSet002\\Services\\FreshService]", system)
            self.assertNotIn(
                r"[System\\CurrentControlSet\\Services\\FreshService]", system)

    def test_second_replace_failure_restores_first_file(self):
        with tempfile.TemporaryDirectory() as td:
            prefix = self.make_prefix(td)
            original_system = (prefix / "system.reg").read_bytes()
            original_user = (prefix / "user.reg").read_bytes()
            real_replace = os.replace
            failed = False

            def fail_user_once(source, destination):
                nonlocal failed
                if Path(destination).name == "user.reg" and not failed:
                    failed = True
                    raise OSError("injected user.reg failure")
                return real_replace(source, destination)

            with mock.patch.object(wine_registry.os, "replace",
                                   side_effect=fail_user_once):
                with self.assertRaisesRegex(OSError, "injected"):
                    wine_registry.update_prefix_registry(
                        prefix,
                        machine=[wine_registry.reg_dword(
                            r"Software\Wine\WineGDK", "ForceMsaFacet", 1)],
                        user=[wine_registry.reg_sz(
                            "Environment", "NEW_VALUE", "yes")],
                    )

            self.assertEqual((prefix / "system.reg").read_bytes(),
                             original_system)
            self.assertEqual((prefix / "user.reg").read_bytes(), original_user)

    def test_rejects_non_wine_file_without_replacing_it(self):
        with tempfile.TemporaryDirectory() as td:
            prefix = self.make_prefix(td)
            path = prefix / "system.reg"
            path.write_text("not a registry")
            with self.assertRaisesRegex(ValueError, "not a Wine"):
                wine_registry.update_prefix_registry(
                    prefix, machine=[wine_registry.reg_dword("A", "B", 1)])
            self.assertEqual(path.read_text(), "not a registry")

    def test_live_prefix_is_rejected_before_registry_read_or_replace(self):
        with tempfile.TemporaryDirectory() as td:
            prefix = self.make_prefix(td)
            before = (prefix / "system.reg").read_bytes()
            with mock.patch(
                    "minecrafthub.prefix.require_prefix_idle",
                    side_effect=BolError("prefix active")):
                with self.assertRaisesRegex(BolError, "prefix active"):
                    wine_registry.update_prefix_registry(
                        prefix,
                        machine=[wine_registry.reg_dword("A", "B", 1)],
                    )
            self.assertEqual((prefix / "system.reg").read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
