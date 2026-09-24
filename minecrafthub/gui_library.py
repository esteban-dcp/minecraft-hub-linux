"""minecrafthub.gui_library — multi-game library view.

The library is the launcher entry point for users who installed more than
one game. It is a grid of tiles: one tile per product in the registry
(Bedrock release, Bedrock preview, future Dungeons, future Legends),
each showing the family, edition, version, and the action that would
start the install/launch flow.

The page is intentionally lightweight: it does not own the install or
launch code (that lives in :class:`minecrafthub.launchers.GameLauncher`)
and it does not refit the existing hero/setup/settings panels. A tile
click emits a ``game_selected`` signal the main window listens to,
and the main window routes the user to the hero or setup flow based on
whether the product has an installed build.

The widget uses object names (``#Card``, ``#Title``, ``#Play``,
``#Install``) so the global ``Theme.qss()`` stylesheet in :mod:`gui`
picks them up; no per-widget QSS lives here.
"""
# SPDX-License-Identifier: MIT

from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from . import games
from .config import CONTENT, LIBRARY_POINTER, PRODUCTS


class LibraryTile(QFrame):
    """One game in the library.

    A clickable card showing the product name, the edition/version (or
    "Not installed"), and the action that would start the
    install/launch flow. The tile emits ``selected(product_id)`` on
    click; the main window decides what to do with the selection.

    The tile does not block on a missing launcher: a future family
    whose launcher has not shipped yet still renders, with the action
    button disabled and a tooltip pointing the user at the registry.
    """

    selected = Signal(str)

    def __init__(self, product, parent=None):
        super().__init__(parent)
        self.product = product
        self.setObjectName("Card")
        self.setCursor(Qt.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 12, 14, 12)
        outer.setSpacing(6)

        # Family: "Minecraft Bedrock" / "Minecraft Dungeons" / ...
        self.family_lbl = QLabel(product.get("name", product.get("id")))
        self.family_lbl.setObjectName("Title")
        outer.addWidget(self.family_lbl)

        # Edition chip: "release" / "preview", lowercase by convention.
        edition = QLabel(product.get("id", ""))
        edition.setObjectName("Chip")
        outer.addWidget(edition)

        # Status line: either "1.26.44.3" or "Not installed". The
        # library asks the games module, which knows about every
        # family because the registry is its input.
        version, installed = self._resolve_status()
        self.version_lbl = QLabel(version if installed else "Not installed")
        self.version_lbl.setObjectName("Sub")
        outer.addWidget(self.version_lbl)

        # Action button: "Play" if installed, "Install" otherwise. The
        # button is disabled when the product has no launcher wired
        # yet (Dungeons / Legends / Java land here).
        self.action_btn = QPushButton("Play" if installed else "Install")
        self.action_btn.setObjectName("Play" if installed else "Install")
        self._supports_install = self._launcher_supports(product, "install")
        self.action_btn.setEnabled(installed or self._supports_install)
        self.action_btn.clicked.connect(
            lambda: self.selected.emit(self.product.get("id"))
        )
        outer.addWidget(self.action_btn, alignment=Qt.AlignLeft)

        # Whole card click counts as a selection too -- the action
        # button is the focused control, but a click on the card body
        # is what the gamepad/keyboard ring would land on.
        self.mousePressEvent = lambda _e: self.selected.emit(self.product.get("id"))

    def _resolve_status(self):
        """The installed version of this product, or ("Not installed", False).

        Walks the on-disk layout once: the games module already knows
        the family-keyed path of every family, including future ones
        once they are registered.
        """
        family = self.product.get("family")
        edition_id = self.product.get("id")
        if not family or not edition_id:
            return ("Not installed", False)
        try:
            for build in games.installed_builds(with_size=False):
                if build.get("family") == family and build.get("id") == edition_id:
                    return (build.get("version", ""), True)
        except OSError:
            pass
        return ("Not installed", False)

    @staticmethod
    def _launcher_supports(product, capability):
        """True if the product's launcher implements ``capability``."""
        try:
            from .launchers import get_launcher

            launcher = get_launcher(product)
        except Exception:
            return False
        return callable(getattr(launcher, capability, None))


class LibraryView(QWidget):
    """The library page -- a scrollable grid of :class:`LibraryTile`.

    The grid is one tile per registry entry; the order matches the
    declaration order in :data:`minecrafthub.config.PRODUCTS` so that
    "Minecraft Bedrock -- Release" is the first tile a user sees.
    """

    game_selected = Signal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("LibraryView")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(22, 22, 22, 22)
        outer.setSpacing(14)

        title = QLabel("Library")
        title.setObjectName("Title")
        outer.addWidget(title)
        subtitle = QLabel("Every game installed or available through the hub.")
        subtitle.setObjectName("Sub")
        outer.addWidget(subtitle)

        # The grid lives inside a scroll area so the page keeps its
        # bounds when a future family grows the registry; the user's
        # window size does not change just because we added Dungeons.
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        outer.addWidget(scroll, 1)

        grid_host = QWidget()
        scroll.setWidget(grid_host)
        self.grid = QGridLayout(grid_host)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(14)
        self.grid.setColumnStretch(0, 1)
        self.grid.setColumnStretch(1, 1)
        self.grid.setColumnStretch(2, 1)

        self._render()

    def _render(self):
        """Build a tile for every product in the registry.

        The grid is rebuilt on ``refresh()`` so newly-installed builds
        show their version after the user runs ``setup``. The signal
        wiring is set up once per tile.
        """
        for index, product in enumerate(PRODUCTS):
            tile = LibraryTile(product)
            tile.selected.connect(lambda pid, p=product: self._on_selected(p, pid))
            self.grid.addWidget(tile, index // 3, index % 3)

    def _on_selected(self, product, _product_id):
        """Forward the click to the main window with the full product entry."""
        self.game_selected.emit(dict(product))

    def refresh(self):
        """Rebuild every tile from the current on-disk state.

        Called after install/remove so the version line catches up. The
        simpler rebuild (clear + re-add) is fine because the page is
        not interactive with more than a handful of products and the
        alternative (mutating existing tiles) would require carrying
        which tile is which across rebuilds.
        """
        # Drop every tile from the grid, then re-render.
        while self.grid.count():
            item = self.grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        self._render()
