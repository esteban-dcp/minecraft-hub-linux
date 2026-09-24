"""Tests for the GameLauncher abstraction (bol.launchers)."""
# SPDX-License-Identifier: MIT

import unittest
from pathlib import Path
from unittest import mock

from bol import launchers
from bol.config import PRODUCTS
from bol.log import BolError


class RegistryTests(unittest.TestCase):
    def test_bedrock_launcher_is_registered(self):
        registry = launchers.LAUNCHERS()
        self.assertIn("bedrock", registry)

    def test_get_launcher_resolves_bedrock_by_product(self):
        bedrock = launchers.get_launcher(PRODUCTS[0])
        self.assertEqual(bedrock.kind, "bedrock")
        self.assertEqual(bedrock.family, "minecraft-bedrock")

    def test_get_launcher_rejects_product_without_a_launcher(self):
        fake = {
            "family": "minecraft-dungeons",
            "id": "release",
            "kind": "dungeons",
            "launcher": None,
            "name": "Minecraft Dungeons",
        }
        with self.assertRaises(BolError) as ctx:
            launchers.get_launcher(fake)
        self.assertIn("no launcher", str(ctx.exception))

    def test_get_launcher_uses_placeholder_when_tag_is_unknown(self):
        # The placeholder reports a clear message from install/setup/launch,
        # not a KeyError from the registry. ``install_record_path`` and
        # ``product_label`` still work so callers that only need metadata
        # are not blocked by a missing implementation.
        fake = {
            "family": "minecraft-dungeons",
            "id": "release",
            "kind": "dungeons",
            "launcher": "dungeons",
            "name": "Minecraft Dungeons",
        }
        launcher = launchers.get_launcher(fake)
        self.assertEqual(launcher.kind, "dungeons")
        # product_label still resolves the human name from the product
        # entry, so GUI tiles keep showing "Minecraft Dungeons".
        self.assertEqual(launcher.product_label(fake), "Minecraft Dungeons")
        # The placeholder's BolError names the family, not the registry.
        with self.assertRaises(BolError) as ctx:
            launcher.install()
        self.assertIn("not yet supported", str(ctx.exception))


class BedrockLauncherDelegationTests(unittest.TestCase):
    """The BedrockLauncher wraps the existing Bedrock-specific functions,
    so the test surface is small -- it is a thin facade until E5."""

    def test_install_delegates_to_games_install_game(self):
        from bol import games
        from bol.launchers.bedrock import BedrockLauncher

        launcher = BedrockLauncher()
        product = PRODUCTS[0]
        with mock.patch.object(
            games, "install_game", return_value=Path("/tmp/build")
        ) as install:
            root = launcher.install(product, version="1.2.3")

        install.assert_called_once_with(product, "1.2.3", progress=None, force=False)
        self.assertEqual(root, Path("/tmp/build"))

    def test_version_str_delegates_to_games_mc_version_str(self):
        from bol import games
        from bol.launchers.bedrock import BedrockLauncher

        launcher = BedrockLauncher()
        with mock.patch.object(
            games, "mc_version_str", return_value="1.26.20.4"
        ) as parser:
            self.assertEqual(launcher.version_str(Path("/tmp/build")), "1.26.20.4")
        parser.assert_called_once_with(Path("/tmp/build"))

    def test_is_running_delegates_to_prefix_running_check(self):
        from bol import prefix
        from bol.launchers.bedrock import BedrockLauncher

        launcher = BedrockLauncher()
        with mock.patch.object(prefix, "_mc_running", return_value=True):
            self.assertTrue(launcher.is_running())

    def test_install_record_path_matches_games_convention(self):
        from bol import games
        from bol.launchers.bedrock import BedrockLauncher

        launcher = BedrockLauncher()
        # The path is a bare name (it lives inside a build folder), so
        # ``.name`` strips the parent the launcher wraps around it.
        self.assertEqual(launcher.install_record_path().name, games._INSTALL_METADATA)

    def test_gpu_profile_is_empty_for_bedrock_today(self):
        from bol.launchers.bedrock import BedrockLauncher

        # Bedrock uses the global GPU safety thresholds; the launcher's
        # override map is empty. A future launcher that needs different
        # thresholds returns a non-empty dict and ``doctor`` consults it.
        self.assertEqual(BedrockLauncher().gpu_profile(), {})


class GamesDispatchTests(unittest.TestCase):
    """bol.games routes version_str and version_key through the launcher."""

    def test_version_str_for_bedrock_uses_launcher(self):
        from bol import games
        from bol.launchers.bedrock import BedrockLauncher

        launcher = BedrockLauncher()
        with mock.patch.object(launcher, "version_str", return_value="9.9.9") as parser:
            with mock.patch.object(
                games, "_launcher_for_family", return_value=launcher
            ):
                self.assertEqual(
                    games.version_str_for("minecraft-bedrock", Path("/tmp/build")),
                    "9.9.9",
                )
        parser.assert_called_once_with(Path("/tmp/build"))

    def test_version_str_for_unknown_family_returns_none(self):
        from bol import games

        with mock.patch.object(games, "_launcher_for_family", return_value=None):
            self.assertIsNone(
                games.version_str_for("minecraft-unknown", Path("/tmp/build"))
            )

    def test_version_key_for_unknown_family_falls_back_to_string_tuple(self):
        from bol import games

        with mock.patch.object(games, "_launcher_for_family", return_value=None):
            self.assertEqual(
                games.version_key_for("minecraft-unknown", "1.2.3"), ("1.2.3",)
            )
