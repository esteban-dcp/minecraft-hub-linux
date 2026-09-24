"""Reporting a Qt toolkit the host cannot load (issue #205)."""
# SPDX-License-Identifier: MIT

import unittest
from unittest import mock

from minecrafthub import deps, doctor

LOADER_ERROR = ("libzstd.so.1: cannot open shared object file: "
                "No such file or directory")


class MissingSharedLibraryTests(unittest.TestCase):
    def test_the_library_the_loader_names_is_read_back_out(self):
        self.assertEqual(
            deps.missing_shared_library(ImportError(LOADER_ERROR)),
            "libzstd.so.1")
        self.assertEqual(
            deps.missing_shared_library(ImportError(
                "libQt6Core.so.6: cannot open shared object file: No such "
                "file or directory")),
            "libQt6Core.so.6")

    def test_an_ordinary_import_failure_names_no_library(self):
        for message in ("No module named 'PySide6'",
                        "cannot import name 'QToolButton' from 'PySide6'",
                        "/usr/lib/libzstd.so.1: file too short"):
            with self.subTest(message=message):
                self.assertIsNone(
                    deps.missing_shared_library(ImportError(message)))


class GuiToolkitSummaryTests(unittest.TestCase):
    def test_a_toolkit_that_imports_is_reported_ready(self):
        with mock.patch.object(deps, "have", return_value=True), \
                mock.patch.object(deps, "gui_import_error", return_value=None):
            self.assertEqual(doctor.gui_toolkit_summary(), "OK (GUI)")

    def test_a_toolkit_that_is_not_installed_yet_is_not_a_failure(self):
        # The portable .pyz and a bare checkout install it on first launch.
        with mock.patch.object(deps, "have", return_value=False):
            self.assertEqual(doctor.gui_toolkit_summary(),
                             "auto-installed on launch")

    def test_an_installed_toolkit_that_cannot_load_names_the_library(self):
        # What `have()` alone reported as "OK (GUI)" on the machine in #205,
        # where the launcher could not open at all.
        with mock.patch.object(deps, "have", return_value=True), \
                mock.patch.object(deps, "gui_import_error",
                                  return_value=LOADER_ERROR):
            summary = doctor.gui_toolkit_summary()
        self.assertIn("Qt cannot load", summary)
        self.assertIn("libzstd.so.1", summary)

    def test_an_installed_toolkit_broken_otherwise_keeps_the_reason(self):
        with mock.patch.object(deps, "have", return_value=True), \
                mock.patch.object(deps, "gui_import_error",
                                  return_value="undefined symbol: PySide6"):
            self.assertIn("undefined symbol: PySide6",
                          doctor.gui_toolkit_summary())


class GuiImportErrorTests(unittest.TestCase):
    def test_the_import_is_attempted_not_just_the_module_path(self):
        with mock.patch("importlib.import_module",
                        side_effect=ImportError(LOADER_ERROR)) as imported:
            self.assertEqual(deps.gui_import_error(), LOADER_ERROR)
        imported.assert_called_once_with("PySide6.QtCore")

    def test_an_importable_toolkit_reports_no_error(self):
        with mock.patch("importlib.import_module") as imported:
            self.assertIsNone(deps.gui_import_error())
        imported.assert_called_once_with("PySide6.QtCore")


if __name__ == "__main__":
    unittest.main()
