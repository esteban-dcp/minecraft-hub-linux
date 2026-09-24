"""bol.launchers — the GameLauncher protocol and registry.

Every product the hub can install belongs to a family (minecraft-bedrock
today, minecraft-dungeons / minecraft-legends / minecraft-java to come)
and is run by one :class:`GameLauncher` implementation. The launcher is
what knows how to:

  * fetch the game (today via ``xodus-cli`` -- future titles will swap
    the downloader without the rest of the launcher having to know);
  * install / repair the engine stack (WineGDK for Bedrock, a different
    Proton for the others, no Wine at all for Java);
  * write the install metadata file the GUI lists and the CLI deletes;
  * parse the build folder's own version manifest;
  * start the game and refuse a delete while it is running;
  * report the GPU safety profile that ``doctor`` checks before PLAY.

The :data:`LAUNCHERS` registry maps ``PRODUCTS[*]["launcher"]`` -- the
short tag stored on every product entry, not the family -- to an
instance. ``LAUNCHERS[tag]`` raises ``KeyError`` if the tag is not in
the registry, which is what callers turn into a "not supported yet"
error when a future family is referenced before its launcher ships.
"""
# SPDX-License-Identifier: MIT

from pathlib import Path
from typing import Any, Callable, Optional, Protocol

from ..log import BolError


class GameLauncher(Protocol):
    """How one product family is installed, launched and uninstalled.

    A launcher is a class with no constructor arguments: ``LAUNCHERS``
    stores one instance per tag, and ``games.py`` / ``launch.py`` ask for
    the right one based on the product being acted on. State belongs to
    the data directory and to the build folder, not to the launcher.
    """

    #: The product kind this launcher serves (e.g. ``"bedrock"``). The
    #: registry key.
    kind: str

    #: The human-readable name of the family. The GUI uses this in
    #: library tiles and the diagnose() report uses it in messages.
    family: str

    def install(self, product, version=None, progress=None, force=False) -> Path:
        """Download (if needed) and return the build folder for a build.

        ``product`` is the entry from :data:`bol.config.PRODUCTS`. The
        return value is the folder the launcher should be started from.
        """

    def version_str(self, folder: Path) -> Optional[str]:
        """The version string of an installed build, read from its folder.

        ``None`` if the folder is not a build this launcher recognises.
        """

    def version_key(self, version: str) -> tuple:
        """Sort key for ordering version strings newest-first."""

    def is_running(self) -> bool:
        """True while a launch started by this launcher is still running.

        The GUI uses this to grey out the 'Remove build' button while a
        game is open. ``bol.cli.do_setup`` uses it to refuse engine
        swaps that would terminate a running game.
        """

    def prefix_dir(self) -> Optional[Path]:
        """The Wine prefix this launcher uses, or ``None`` for engines
        that do not need one (Java, native Linux builds)."""

    def wine_engine(self) -> Optional[Path]:
        """The managed engine directory, or ``None`` for engines that
        do not need one."""

    def launch(self, *, on_started=None) -> None:
        """Start the build named by the active selection.

        Raises :class:`bol.log.BolError` for user-facing failures (no
        sign-in, missing engine, broken prefix). ``on_started`` is a
        callback the GUI hands in to flip out of the launcher window
        as soon as the game actually launches.
        """

    def direct_launch_readiness(self) -> list:
        """Issues that would block a shortcut-style launch without the
        GUI. Each entry is a human-readable string the CLI prints."""

    def setup(self, *, force=False, progress=None) -> None:
        """Install/update engine + prefix + game integration.

        Idempotent: re-running with the same versions installed is a
        no-op. ``force`` re-downloads / re-applies even when the pin
        matches.
        """

    def game_root(self, folder: Path) -> Optional[Path]:
        """The folder containing the actual game binary, or ``None`` if
        ``folder`` is not a complete build of this launcher."""

    def install_record_path(self) -> Path:
        """The metadata filename the installer writes inside a build."""

    def gpu_profile(self) -> dict:
        """Per-launcher GPU safety profile, consumed by ``doctor``.

        The shape matches :func:`bol.gpu_safety.profile` so callers do
        not need to know which launcher produced it.
        """

    def product_label(self, product) -> str:
        """Human-readable name for the product (used in messages)."""


class _NotImplementedLauncher:
    """Placeholder launcher for products whose engine is not yet wired.

    Returns ``None`` from everything that has a return type of "Path or
    None" and reports the missing-launcher reason from every other
    method. The launcher the registry will replace this with is the one
    that ships when Dungeons / Legends / Java support lands; nothing in
    the rest of the code base has to change to take the new launcher.
    """

    def __init__(self, kind: str, family: str, message: str):
        self.kind = kind
        self.family = family
        self._message = message

    def _fail(self):
        from ..log import BolError

        raise BolError(self._message)

    def install(self, *args, **kwargs):
        self._fail()

    def version_str(self, folder):
        return None

    def version_key(self, version):
        return ()

    def is_running(self):
        return False

    def prefix_dir(self):
        return None

    def wine_engine(self):
        return None

    def launch(self, **kwargs):
        self._fail()

    def direct_launch_readiness(self):
        return [self._message]

    def setup(self, **kwargs):
        self._fail()

    def game_root(self, folder):
        return None

    def install_record_path(self):
        return Path(".bedrock-on-linux-install.json")

    def gpu_profile(self):
        return {}

    def product_label(self, product):
        return product.get("name", self.kind)


# Registry of launcher instances keyed by product kind (the same string
# stored in ``PRODUCTS[*]["launcher"]``). The bedock launcher is the only
# one wired today; future families land here as their code is merged.
#
# ``_LAUNCHERS_IMPORT`` is filled in after :mod:`bol.launchers.bedrock`
# has been imported, to break the circular dependency that would otherwise
# exist between this module and the Bedrock launcher (which imports
# :mod:`bol.games`).
_LAUNCHERS_IMPORT: Optional[Callable[[], dict]] = None


def register_launchers(load):
    """Install the launcher factory used by the registry.

    Called from :mod:`bol.launchers.bedrock` once its module is built.
    The factory returns a fresh dict so tests can isolate the registry
    without touching the real launchers.
    """
    global _LAUNCHERS_IMPORT
    _LAUNCHERS_IMPORT = load


def LAUNCHERS() -> dict:
    """The launcher registry, keyed by product kind."""
    if _LAUNCHERS_IMPORT is None:
        raise RuntimeError(
            "bol.launchers: no launchers registered -- import "
            "bol.launchers.bedrock before any code that calls LAUNCHERS()."
        )
    return _LAUNCHERS_IMPORT()


def get_launcher(product) -> GameLauncher:
    """The launcher for ``product``, or raise a clear error if absent.

    A product whose ``launcher`` field is missing or names a launcher
    that has not shipped yet (Dungeons, Legends, Java) ends up here
    before any code that would actually need the engine: install/launch
    on those products surface a single BolError instead of a stack trace
    from inside a half-written launcher.
    """
    try:
        registry = LAUNCHERS()
    except RuntimeError as exc:
        from ..log import BolError

        raise BolError(str(exc)) from exc
    tag = product.get("launcher")
    if not tag:
        from ..log import BolError

        raise BolError(
            f"{product.get('name', product.get('id'))} has no launcher "
            "wired yet. Bedrock is the only family the hub can install "
            "and run today; Dungeons, Legends and Java land in later "
            "releases."
        )
    launcher = registry.get(tag)
    if launcher is None:
        # A product whose launcher tag is recognised but whose
        # implementation has not been imported yet. Fall back to a
        # placeholder so the error surfaces from a real call, not from
        # the lookup itself.
        return _NotImplementedLauncher(
            tag,
            product.get("family", tag),
            f"{product.get('name', tag)} ({tag}) is not yet supported by "
            "this build of the hub. Check for an update that adds the "
            "launcher.",
        )
    return launcher


# Import the Bedrock launcher at the bottom so the registry factory
# below is in place by the time bol.launchers.bedrock imports
# ``register_launchers``. Future launchers (``dungeons``, ``legends``,
# ``java``) are wired here the same way.
from . import bedrock as _bedrock  # noqa: E402, F401


# Import the Bedrock launcher at the bottom so the registry factory
# below is in place by the time bol.launchers.bedrock imports
# ``register_launchers``. Future launchers (``dungeons``, ``legends``,
# ``java``) are wired here the same way.
from . import bedrock as _bedrock  # noqa: E402, F401
