"""bol.launchers.bedrock -- the Bedrock-specific GameLauncher.

This wraps every Bedrock-specific function the launcher has lived with
across :mod:`bol.games`, :mod:`bol.launch`, :mod:`bol.gamesetup`,
:mod:`bol.prefix` and :mod:`bol.winegdk`. E4 introduces the wrapper;
E5 will move those functions physically into this module and the rest
of the package will import them from here. For now the wrapper is a
thin facade so behaviour, tests and the call graph stay identical.
"""
# SPDX-License-Identifier: MIT

from pathlib import Path
from typing import Optional

from .. import games, prefix, winegdk
from ..config import (
    CONTENT,
    PFX,
    PRODUCTS,
    WINEGDK_OUT,
)
from ..log import BolError
from . import register_launchers


class BedrockLauncher:
    """The launcher for every product in the minecraft-bedrock family."""

    kind = "bedrock"
    family = "minecraft-bedrock"

    # ---- installation -------------------------------------------------

    def install(self, product, version=None, progress=None, force=False) -> Path:
        return games.install_game(product, version, progress=progress, force=force)

    def version_str(self, folder: Path) -> Optional[str]:
        return games.mc_version_str(folder)

    def version_key(self, version: str) -> tuple:
        return games.xodus.version_key(version)

    def is_running(self) -> bool:
        return prefix._mc_running()

    def prefix_dir(self) -> Optional[Path]:
        return PFX

    def wine_engine(self) -> Optional[Path]:
        return WINEGDK_OUT

    # ---- launch -------------------------------------------------------

    def launch(self, *, on_started=None) -> None:
        from .. import launch as _launch

        # ``launch()`` is the legacy free function. Wrapping it here keeps
        # callers -- and future launchers -- talking to one method.
        _launch.launch(on_started=on_started)

    def direct_launch_readiness(self) -> list:
        from .. import launch as _launch

        return _launch.direct_launch_readiness()

    # ---- setup --------------------------------------------------------

    def setup(self, *, force=False, progress=None) -> None:
        from .. import gamesetup

        gamesetup.do_setup(force=force, progress=progress)

    # ---- installation metadata ---------------------------------------

    def game_root(self, folder: Path) -> Optional[Path]:
        return games.xodus.game_root(folder)

    def install_record_path(self) -> Path:
        # The metadata filename lives in bol.games today. E5 will move
        # the constant here; for E4 the launcher asks the games module
        # what the convention is so the path can change without breaking
        # callers.
        return Path(games._INSTALL_METADATA)

    def gpu_profile(self) -> dict:
        # Per-launcher GPU safety overrides, consumed by ``doctor`` before
        # PLAY. Bedrock uses the global profile today (every title in the
        # family shares the same Vulkan/D3D12 safety thresholds), so the
        # override map is empty. The hook is here so a future launcher
        # that needs different thresholds (e.g. a Vulkan-1.0-only engine)
        # can ship them with the launcher instead of inside the safety
        # module.
        return {}

    def product_label(self, product) -> str:
        return product.get("name", "Minecraft")


def _build_registry() -> dict:
    """The default registry -- one Bedrock launcher, no others yet."""
    return {"bedrock": BedrockLauncher()}


def _register():
    register_launchers(_build_registry)


_register()
