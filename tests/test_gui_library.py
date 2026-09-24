"""Tests for the multi-game library view (minecrafthub.gui_library)."""
# SPDX-License-Identifier: MIT

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest

from minecrafthub import games
from minecrafthub.config import PRODUCTS
from minecrafthub.gui_library import LibraryTile, LibraryView
from tests.guiharness import qt_app


def _bedrock_release():
    return {
        "family": "minecraft-bedrock",
        "id": "release",
        "kind": "bedrock",
        "launcher": "bedrock",
        "name": "Minecraft for Windows",
        "beta": False,
    }


def _bedrock_preview():
    return {
        "family": "minecraft-bedrock",
        "id": "preview",
        "kind": "bedrock",
        "launcher": "bedrock",
        "name": "Minecraft Preview for Windows",
        "beta": True,
    }


def _dungeons():
    return {
        "family": "minecraft-dungeons",
        "id": "release",
        "kind": "dungeons",
        "launcher": None,
        "name": "Minecraft Dungeons",
        "beta": False,
    }


class _LibraryViewTests(unittest.TestCase):
    """Shared setUp/tearDown so the QApplication exists before any tile."""

    @classmethod
    def setUpClass(cls):
        cls._qtapp = qt_app()

    def setUp(self):
        # Touch the cached app so a Qt failure here shows up in the
        # right test, not in setUpClass.
        self._qtapp.processEvents()


class TileStatusTests(_LibraryViewTests):
    """A tile's status line reflects the on-disk install state."""

    def test_uninstalled_tile_shows_install_action(self):
        # With no installed builds, the release tile is "Not installed"
        # with an "Install" action button. The preview tile is the
        # same: both products render independently of each other.
        with mock.patch.object(games, "installed_builds", return_value=[]):
            tile = LibraryTile(_bedrock_release())
        self.assertEqual(tile.version_lbl.text(), "Not installed")
        self.assertEqual(tile.action_btn.text(), "Install")

    def test_installed_tile_shows_play_action_and_version(self):
        with mock.patch.object(
            games,
            "installed_builds",
            return_value=[
                {
                    "family": "minecraft-bedrock",
                    "id": "release",
                    "version": "1.26.44.3",
                    "path": Path("/tmp/build"),
                    "managed": True,
                    "playable": True,
                    "legacy": False,
                }
            ],
        ):
            tile = LibraryTile(_bedrock_release())
        self.assertEqual(tile.version_lbl.text(), "1.26.44.3")
        self.assertEqual(tile.action_btn.text(), "Play")

    def test_installed_preview_does_not_shadow_installed_release(self):
        # A preview install must not register as a release install.
        with mock.patch.object(
            games,
            "installed_builds",
            return_value=[
                {
                    "family": "minecraft-bedrock",
                    "id": "preview",
                    "version": "1.27.0.5",
                    "path": Path("/tmp/build"),
                    "managed": True,
                    "playable": True,
                    "legacy": False,
                }
            ],
        ):
            release = LibraryTile(_bedrock_release())
            preview = LibraryTile(_bedrock_preview())
        self.assertEqual(release.version_lbl.text(), "Not installed")
        self.assertEqual(preview.version_lbl.text(), "1.27.0.5")

    def test_disabled_tile_for_a_family_without_a_launcher(self):
        # Dungeons has no launcher wired yet. The tile renders but the
        # action button is disabled, so a click cannot take the user
        # to a half-built flow.
        with mock.patch.object(games, "installed_builds", return_value=[]):
            tile = LibraryTile(_dungeons())
        self.assertEqual(tile.version_lbl.text(), "Not installed")
        self.assertFalse(tile.action_btn.isEnabled())


class TileSelectionTests(_LibraryViewTests):
    """Clicking a tile emits a ``selected`` signal carrying the product id."""

    def test_action_button_click_emits_product_id(self):
        captured = []
        with mock.patch.object(games, "installed_builds", return_value=[]):
            tile = LibraryTile(_bedrock_release())
        tile.selected.connect(captured.append)
        tile.action_btn.click()
        self.assertEqual(captured, ["release"])


class ViewTests(_LibraryViewTests):
    """The library view lists every product and refreshes on demand."""

    def test_every_registry_product_has_a_tile(self):
        # Every entry in PRODUCTS becomes a tile. The view does not
        # filter -- families whose launcher has not shipped still
        # appear, so the user sees what is coming.
        with mock.patch.object(games, "installed_builds", return_value=[]):
            view = LibraryView()
        self.assertEqual(view.grid.count(), len(PRODUCTS))

    def test_view_routes_selection_with_full_product_entry(self):
        with mock.patch.object(games, "installed_builds", return_value=[]):
            view = LibraryView()
        captured = []
        view.game_selected.connect(captured.append)
        # Find the first tile in the grid and emit its selection.
        for index in range(view.grid.count()):
            tile = view.grid.itemAt(index).widget()
            if tile.product["id"] == "release":
                tile.selected.emit("release")
                break
        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0]["family"], "minecraft-bedrock")
        self.assertEqual(captured[0]["id"], "release")

    def test_refresh_rebuilds_tiles_with_current_install_state(self):
        # Initially no builds installed.
        with mock.patch.object(games, "installed_builds", return_value=[]):
            view = LibraryView()
        initial = view.grid.count()

        # After a Bedrock release build appears on disk, refreshing
        # the library picks it up.
        with mock.patch.object(
            games,
            "installed_builds",
            return_value=[
                {
                    "family": "minecraft-bedrock",
                    "id": "release",
                    "version": "1.26.44.3",
                    "path": Path("/tmp/build"),
                    "managed": True,
                    "playable": True,
                    "legacy": False,
                }
            ],
        ):
            view.refresh()

        self.assertEqual(view.grid.count(), initial)
        for index in range(view.grid.count()):
            tile = view.grid.itemAt(index).widget()
            if tile.product["id"] == "release":
                self.assertEqual(tile.version_lbl.text(), "1.26.44.3")
                self.assertEqual(tile.action_btn.text(), "Play")
                break
