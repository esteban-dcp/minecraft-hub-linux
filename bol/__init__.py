"""Compatibility shim -- the canonical package is ``minecrafthub``.

E5 renamed the Python package from ``bol`` (BedrockOnLinux) to
``minecrafthub`` (Minecraft Hub for Linux). The old name is kept as a
thin shim so existing imports -- ``from bol.games import install_game``,
``from bol.cli import main`` -- keep working.

How it works
============

``bol`` is a single-file package with no submodule of its own. Its
``__path__`` is shared with ``minecrafthub`` so ``import bol.games``
resolves to ``minecrafthub/games.py`` (Python's import system walks
``__path__`` and finds ``games.py`` regardless of which package it
"belongs" to). ``__getattr__`` (PEP 562) forwards every submodule
attribute lookup to ``minecrafthub`` so dotted imports work without
forcing an ``import bol.games`` at module load.

New code should ``import minecrafthub`` directly. The shim stays
because removing it is a breaking change for any external caller that
still types ``bol`` -- a real risk for a fork that ships independently.
"""
# SPDX-License-Identifier: MIT

# Pointing bol's __path__ at minecrafthub means ``import bol.games``
# resolves through the loader to minecrafthub/games.py: there is no
# bol/games.py, but Python's path-based finder does not care -- it
# looks at every directory in __path__ for the requested module name.
import os as _os

_CANDIDATES = (
    _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "minecrafthub"),
)
for _candidate in _CANDIDATES:
    if _os.path.isdir(_candidate):
        __path__ = [_candidate]
        break
else:  # pragma: no cover - minecrafthub/ always ships next to bol/.
    raise ImportError(
        "bol: cannot find minecrafthub/ beside bol/. The hub package "
        "layout is broken -- reinstall the app "
        "(https://github.com/esteban-dcp/minecraft-hub-linux)."
    )


def __getattr__(name):
    """Forward attribute lookups to minecrafthub (PEP 562).

    Every import of a bol submodule -- ``from bol import games``,
    ``import bol.cli``, even ``from bol.launchers import bedrock`` --
    resolves through this method, which imports the same-named
    submodule under minecrafthub and returns it. The submodule name
    becomes the new attribute on the bol module, so repeated lookups
    are O(1) after the first.
    """
    import importlib as _il

    module = _il.import_module(f"minecrafthub.{name}")
    globals()[name] = module
    return module


def __dir__():
    """Tab completion: list every submodule available via the shim."""
    return sorted(set(globals()) | _list_submodules())


def _list_submodules():
    """Every public submodule name under minecrafthub/."""
    import pathlib as _pl

    here = _pl.Path(_CANDIDATES[0])
    if not here.is_dir():
        return []
    return [
        p.stem
        for p in here.iterdir()
        if p.is_file() and p.suffix == ".py" and p.stem != "__init__"
    ]


__version__ = "2.2.7"
