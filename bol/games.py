"""bol.games — product installation, listing and active selection.

The on-disk layout is one tree per product family (minecraft-bedrock today,
minecraft-dungeons / minecraft-legends / minecraft-java to come) under
``DATA/games/<family>/<id>/<version>/``. Pre-E3 Bedrock installs sat at
``DATA/games/<id>/<version>/`` with no family above the build folder, and
:meth:`migrate_legacy_layout` lifts them into the new shape on first run.

The active build -- the one PLAY would launch -- is named by a JSON pointer
at ``DATA/library/current.json`` and by the ``DATA/content`` symlink, which
:mod:`bol.launch` resolves against the game exe. Either of the two is enough
on its own: the symlink for ``launch`` (it wants a real path), the pointer
for the GUI (it wants family + edition + version). They are written
together by :func:`use_game_dir`, and the migration populates the pointer
from the pre-E3 ``mc_edition``/``mc_version`` settings.
"""
# SPDX-License-Identifier: MIT

import json
import os
import re
import shutil
import time
from pathlib import Path

from . import xodus
from .config import CONTENT, GAMES, LIBRARY, LIBRARY_POINTER
from .config import PRODUCTS
from .log import BolError, die, info, ok, warn
from .util import load_settings, save_settings


_INSTALL_METADATA = ".bedrock-on-linux-install.json"

# Hardcoded so the migration can decide what to move without consulting the
# product registry, which is exactly the layer being introduced here. Any
# other family that lands gets its own subtree from the start.
BEDROCK_FAMILY = "minecraft-bedrock"

# The Bedrock families this layout knows how to migrate from the pre-E3
# ``games/<edition>/<version>/`` shape. Order is significant: the test for
# legacy detection picks the first edition that matches by id, so any
# id-tied Bedrock edition goes here.
_LEGACY_BEDROCK_EDITIONS = frozenset({"release", "preview"})


def list_editions(include_beta=True):
    """The Minecraft editions available for installation."""
    return [entry for entry in xodus.list_editions()
            if include_beta or not entry["beta"]]


def version_dir(edition_id, version):
    """The Bedrock build folder for ``(edition_id, version)``.

    Kept as the Bedrock-only convenience wrapper because every pre-E3 caller
    was written when Bedrock was the only family. Use :func:`version_dir_for`
    when the family is not known to be Bedrock.
    """
    return version_dir_for(BEDROCK_FAMILY, edition_id, version)


def _launcher_for_family(family):
    """The :class:`GameLauncher` that installs and runs ``family``.

    Imported lazily so ``bol.games`` and ``bol.launchers.bedrock`` do not
    pull on each other at module load -- both modules are imported by
    the entry script, and ``bol.launchers.bedrock`` wraps methods on
    ``bol.games`` so a static dependency would loop.
    """
    from .launchers import get_launcher
    # PRODUCTS is the only place that knows the (family -> launcher tag)
    # mapping; the launcher registry itself is keyed by tag. A family may
    # have many products (Bedrock release + preview share a launcher) so
    # any matching entry resolves to the same instance.
    for entry in PRODUCTS:
        if entry.get("family") == family:
            return get_launcher(entry)
    return None


def version_str_for(family, folder):
    """The version string ``folder`` reports, via the right launcher.

    ``None`` for folders this launcher does not recognise, and for
    families whose launcher is not yet implemented (the placeholder
    returns ``None`` from every read).
    """
    launcher = _launcher_for_family(family)
    if launcher is None:
        return None
    return launcher.version_str(folder)


def version_key_for(family, version):
    """Sort key for a build's version string, via the right launcher.

    Falls back to a lexicographic comparison on the string itself when
    the family has no launcher registered -- a tuple of ints for Bedrock,
    the raw string split for everyone else.
    """
    launcher = _launcher_for_family(family)
    if launcher is None:
        return (version,)
    return launcher.version_key(version)


def version_dir_for(family, edition_id, version):
    """The build folder for ``(family, edition_id, version)``.

    All new installs land here. The folder holds one build of one edition of
    one family, and that is also what the launcher starts: nothing in there is
    shared with another build or another edition, so going back to a build
    already on disk costs nothing.
    """
    return GAMES / family / edition_id / version


def list_versions(edition_id, ignore_cache=False):
    """Installable builds for an edition, newest first.

    Each entry gains ``installed``: whether that exact build is already on
    disk, which is what lets switching back to a build you already have cost
    nothing. ``ignore_cache`` bypasses the 12-hour cache on the build index,
    for when a build known to be out is not showing up yet.
    """
    out = []
    for entry in xodus.version_catalogue(edition_id, ignore_cache=ignore_cache):
        entry = dict(entry)
        entry["installed"] = _game_root(
            version_dir(edition_id, entry["version"])) is not None
        out.append(entry)
    return out


def _game_root(dest):
    """Folder of a complete installed build (exe + appxmanifest), else None
    (a bare exe with no manifest means a truncated install → reinstall).

    The shape is decided in bol.xodus, which is what writes the directory and
    has to tell a finished download from one that installed nothing. What is
    added here is the other way a folder can look complete and not be one: a
    Store build whose encrypted package went missing cannot be decrypted, so
    it is not something to launch — it is something to download again, and
    saying so is what gives PLAY a way to repair it (issue #216)."""
    root = xodus.game_root(dest)
    if root is None or xodus.lost_package_cache(root):
        return None
    return root


def _install_record(dest):
    try:
        return json.loads(
            (Path(dest) / _INSTALL_METADATA).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return {}


def _write_install_record(dest, edition, version, url):
    record = {
        "schema": 2,
        "edition": edition["id"],
        "product": edition["product"],
        "version": version,
        "source_url": url,
        "xodus_rev": xodus.XODUS_REV,
        "installed": int(time.time()),
    }
    target = Path(dest) / _INSTALL_METADATA
    staged = target.with_name("." + target.name + ".tmp")
    try:
        staged.write_text(
            json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8")
        staged.replace(target)
    finally:
        staged.unlink(missing_ok=True)


def _configured_legacy_root():
    """A complete install configured before the move to the Store, or None.

    Anything under GAMES/<edition>/ belongs to the new layout and is handled
    by the ordinary path; this is only about the copy an upgrade inherits.
    """
    configured = (load_settings().get("game_dir") or "").strip()
    if not configured:
        return None
    path = Path(configured)
    try:
        # Legacy installs also live under GAMES, as GAMES/<version-tag>/, so
        # what marks the new layout is the edition id, not the parent.
        owner = path.resolve().relative_to(GAMES.resolve()).parts[0]
    except (ValueError, IndexError, OSError):
        owner = None
    if owner and xodus.edition(owner):
        return None
    return _game_root(path)


# --------------------------------------------------- library pointer
#
# The active build -- the one PLAY would launch -- is named by a small JSON
# file at DATA/library/current.json with the shape:
#
#     {"family": "minecraft-bedrock", "id": "release", "version": "1.26.44.3"}
#
# It is read by the GUI (which needs the family, edition and version as
# separate values, not a path) and by anyone who wants to know which build
# the launcher would start without chasing the CONTENT symlink. CONTENT is
# kept in sync so launch.py can keep treating it as a real path.


def _read_pointer():
    """The active selection, or None if no pointer file exists yet.

    Tolerates a missing or malformed file: the launcher is still useful
    without a pointer, the GUI just falls back to scanning installed_builds
    and picking the newest.
    """
    try:
        text = LIBRARY_POINTER.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        data = json.loads(text)
    except (ValueError, UnicodeError):
        return None
    if not isinstance(data, dict):
        return None
    family = data.get("family")
    edition_id = data.get("id")
    version = data.get("version")
    if not (isinstance(family, str) and isinstance(edition_id, str)
            and isinstance(version, str)):
        return None
    return {"family": family, "id": edition_id, "version": version}


def _write_pointer(family, edition_id, version):
    """Update current.json atomically and report the new path.

    Written through a sibling .tmp file so a crash mid-write does not leave
    the pointer half-written -- the launcher would otherwise look at a
    truncated JSON on the next run and silently lose the active selection.
    """
    payload = {"family": family, "id": edition_id, "version": version}
    LIBRARY.mkdir(parents=True, exist_ok=True)
    tmp = LIBRARY_POINTER.with_name("." + LIBRARY_POINTER.name + ".tmp")
    try:
        tmp.write_text(
            json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8")
        tmp.replace(LIBRARY_POINTER)
    finally:
        tmp.unlink(missing_ok=True)


def _selection_from_settings():
    """The active selection as last remembered by the legacy settings keys.

    Used to seed the pointer file on first launch after E3 and by callers
    that still want to read the legacy fields (``mc_edition``/``mc_version``)
    for backward compatibility.
    """
    s = load_settings()
    edition_id = (s.get("mc_edition") or "").strip()
    version = (s.get("mc_version") or "").strip()
    if not edition_id or not version:
        return None
    return {"family": BEDROCK_FAMILY, "id": edition_id, "version": version}


def active_selection():
    """The active (family, id, version) the launcher would start next.

    Order of preference:
      1. The pointer file at ``DATA/library/current.json`` (canonical).
      2. The legacy settings fields ``mc_edition`` + ``mc_version``
         (Bedrock only -- what 2.x wrote).

    Returns None when neither is set, which is the case before the very
    first install.
    """
    return _read_pointer() or _selection_from_settings()


# --------------------------------------------------------- legacy migration
#
# Pre-E3 the layout was ``games/<edition>/<version>/`` (Bedrock only). The
# new layout is ``games/<family>/<id>/<version>/``. On first launch after
# E3, lift every Bedrock edition folder to ``games/minecraft-bedrock/<id>/``
# and rewrite the active selection so the launcher keeps pointing at the
# same build. The legacy games/<id>/ folder for non-Bedrock editions does
# not exist (no other family ever used this tree) so there is nothing to
# mistake for Bedrock.


def migrate_legacy_layout():
    """Move pre-E3 Bedrock installs into the family-keyed layout.

    Idempotent: does nothing if no legacy folders are present, and re-runs
    are safe after a partial move. Returns the list of editions that were
    moved, so the caller can warn the user about what changed.
    """
    if not GAMES.exists():
        return []
    moved = []
    target_parent = GAMES / BEDROCK_FAMILY
    for entry in sorted(GAMES.iterdir()):
        if not entry.is_dir() or entry.is_symlink():
            continue
        if entry.name == BEDROCK_FAMILY:
            continue
        if entry.name not in _LEGACY_BEDROCK_EDITIONS:
            continue
        if xodus.edition(entry.name) is None:
            continue
        target = target_parent / entry.name
        target_parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            # A previous partial move already put the new tree in place; leave
            # the legacy copy where it is so the user can decide what to do,
            # and let the next launch of installed_builds() reconcile it.
            continue
        try:
            entry.rename(target)
        except OSError:
            # A read-only bind mount or a permission issue: do not strand the
            # user. The legacy copy still works through the fallback paths.
            continue
        moved.append(entry.name)
    if moved:
        # Seed the pointer from the legacy settings so the launcher does not
        # lose its selection across the move. Best effort: a missing file
        # just leaves the GUI to discover the selection on its own.
        seed = _selection_from_settings()
        if seed is not None:
            _write_pointer(seed["family"], seed["id"], seed["version"])
    return moved


def install_game(edition, version=None, progress=None, force=False):
    """Install one build of one edition through Xodus.

    Each build lives in its own folder, so going back to a build already on
    disk costs nothing and the delta cache Xodus keeps beside it stays valid.
    ``xodus-cli streaming`` is itself incremental and atomic -- it compares
    local segment hashes against the package, fetches only what changed and
    commits with a rename -- so there is deliberately no staging dance here.
    """
    catalogue = list_versions(edition["id"])
    if not catalogue:
        raise BolError(
            f"No {edition['name']} build is listed. Check the network "
            "connection and try again.")
    wanted = str(version or "").strip()
    entry = next((c for c in catalogue if c["version"] == wanted), None)
    if entry is None:
        if wanted:
            warn(f"{edition['name']} {wanted} is no longer listed; using "
                 f"{catalogue[0]['version']} instead.")
        entry = catalogue[0]

    family = edition.get("family") or BEDROCK_FAMILY
    dest = version_dir_for(family, edition["id"], entry["version"])
    dest.parent.mkdir(parents=True, exist_ok=True)
    root = _game_root(dest)
    if root and not force:
        info(f"{edition['name']} {entry['version']} already installed")
        return root

    # A build installed before the move to the Store lives outside
    # GAMES/<edition>/<version>/ and cannot be reached by this path. It is
    # still a complete, working game and it is the one the player has, so keep
    # it as the fallback rather than stranding them with nothing to launch.
    fallback = None if root else _configured_legacy_root()

    info(f"{'Reinstalling' if root else 'Installing'} {edition['name']} "
         f"{entry['version']} — this downloads it from Microsoft with your "
         "own account …")
    # Every mirror the index lists, not just the first: they carry the same
    # package, so a truncated body from one is retryable on the next.
    url = entry["urls"][0]
    try:
        xodus.install(entry["urls"], dest, progress)
    except xodus.NotSignedIn:
        # Actionable, and only the caller can act: never fold this into the
        # fallback below, or the launcher quietly keeps starting the old build
        # instead of offering the sign-in that would install this one.
        raise
    except BolError as exc:
        if fallback is None:
            raise
        warn(f"Could not download {edition['name']} {entry['version']} "
             f"({exc}) — starting the copy already installed. It predates the "
             "switch to the Microsoft Store, so it stays on its own build "
             "until the download works.")
        return fallback
    root = _game_root(dest)
    if not root:
        die(f"Minecraft.Windows.exe missing after installing "
            f"{edition['name']} {entry['version']}.")
    _write_install_record(dest, edition, entry["version"], url)
    ok(f"{edition['name']} {entry['version']} installed")
    _mention_other_builds(edition["id"], entry["version"])
    return root


def _human_size(size):
    value = float(size or 0)
    for unit in ("B", "KiB", "MiB"):
        if value < 1024:
            return f"{value:.0f} {unit}"
        value /= 1024
    return f"{value:.1f} GiB"


def _mention_other_builds(edition_id, version):
    """Say what the builds this download did not replace are taking up.

    Each build has a folder of its own, so a download never removes the one
    it follows. That is deliberate -- it is what makes going back instant --
    and it was completely invisible: nothing said the old build was still
    there, so a few version changes quietly became 10 GiB and the launcher
    read as "it keeps downloading Minecraft over and over" (issue #214).
    Saying it here turns that into a number and a place to act on it.
    """
    try:
        others = [build for build in installed_builds()
                  if build["managed"]
                  and not (build["family"] == BEDROCK_FAMILY
                           and build["id"] == edition_id
                           and build["version"] == version)]
    except OSError:
        return
    if not others:
        return
    total = sum(build["size"] or 0 for build in others)
    info(f"{len(others)} other Minecraft build"
         f"{'s are' if len(others) != 1 else ' is'} still installed, taking "
         f"{_human_size(total)}. Remove the ones you are finished with in "
         "Settings ▸ Versions — worlds and settings are kept.")


def _selection_from_path(folder):
    """The (family, id, version) triple that ``folder`` belongs to.

    Reads the folder's position under GAMES and matches the family and
    edition parts of the path. For folders outside the managed tree -- an
    imported copy -- the family is best-effort: Bedrock today (the only
    thing the launcher has ever imported), so the settings can still be
    updated and the GUI knows which edition picker to show.
    """
    try:
        parts = folder.relative_to(GAMES.resolve()).parts
    except ValueError:
        version = mc_version_str(folder) or "unknown"
        return {"family": BEDROCK_FAMILY, "id": None, "version": version}
    # Three parts (family, edition, version): current layout.
    if len(parts) == 3 and PRODUCTS_BY_FAMILY_ID().get((parts[0], parts[1])):
        return {"family": parts[0], "id": parts[1], "version": parts[2]}
    # Two parts (edition, version): pre-E3 Bedrock layout. The legacy
    # folder moved into the bedrock family on migration; treat it as such
    # even before the move so the pointer still names something.
    if len(parts) == 2 and xodus.edition(parts[0]):
        return {"family": BEDROCK_FAMILY, "id": parts[0],
                "version": parts[1]}
    version = parts[-1] if parts else "unknown"
    return {"family": BEDROCK_FAMILY, "id": None, "version": version}


# Pre-index the registry so the path lookup above is a single dict read
# instead of a registry walk on every install.
_PRODUCTS_BY_FAMILY_ID = None
def PRODUCTS_BY_FAMILY_ID():
    global _PRODUCTS_BY_FAMILY_ID
    if _PRODUCTS_BY_FAMILY_ID is None:
        _PRODUCTS_BY_FAMILY_ID = {
            (entry["family"], entry["id"]): entry
            for entry in PRODUCTS
        }
    return _PRODUCTS_BY_FAMILY_ID


def use_game_dir(folder):
    folder = Path(folder).expanduser().resolve()
    if not (folder / "Minecraft.Windows.exe").exists():
        cands = list(folder.rglob("Minecraft.Windows.exe"))
        if not cands:
            die(f"Minecraft.Windows.exe not found in {folder} (nor in "
                f"its subfolders). Choose an installed edition folder, "
                f"or use '① Minecraft edition'.")
        best = max(cands, key=lambda e: _vt(mc_version_str(e.parent) or "0"))
        folder = best.parent
        info(f"Minecraft found: {folder} "
             f"(version {mc_version_str(folder) or '?'})")
    if CONTENT.is_symlink() or CONTENT.exists():
        CONTENT.unlink() if CONTENT.is_symlink() else shutil.rmtree(CONTENT)
    CONTENT.symlink_to(folder)
    s = load_settings()
    s["game_dir"] = str(folder)
    # Remember what was selected so the picker and auto-select default to what
    # you last played. The new layout is games/<family>/<id>/<version>/;
    # legacy 2.x was games/<id>/<version>/. A folder from outside the managed
    # tree names neither, and keeping the previous choice there would silently
    # reinstall over an imported copy.
    selection = _selection_from_path(folder)
    s["library.current"] = selection
    # The legacy keys only make sense for Bedrock -- and only when the
    # folder is inside the managed tree, so we know which edition it is.
    # An imported copy (selection has no id) leaves the previous choice in
    # place: a setup that names neither the previous edition nor this one
    # would silently reinstall over the import on the next launch.
    if selection["family"] == BEDROCK_FAMILY and selection["id"]:
        s["mc_edition"] = selection["id"]
        s["mc_version"] = selection["version"]
    else:
        # An imported copy still reports its build, for display and bug reports.
        version = mc_version_str(folder)
        if version:
            s["mc_version"] = version
    _write_pointer(selection["family"], selection["id"] or "",
                  selection["version"])
    save_settings(s)
    return folder


# ------------------------------------------------------------ installed builds

# Every build is downloaded into its own folder, which is what makes going
# back to one already on disk instant -- and what makes them pile up: three
# builds tried out is three copies of a 2.5 GiB game, and until this section
# existed nothing but `rm -rf` ever removed one (issue #214).
#
# Nothing the player made is in there. Worlds, settings, screenshots, skins
# and packs live in the Wine prefix, under the account that made them (see
# bol.content), and the prefix belongs to the profile rather than to any one
# build -- so removing a build removes the game and none of what was played
# with it. That sentence belongs wherever the launcher offers the removal:
# "delete this version" reads like "delete my worlds" to anyone who has not
# been told otherwise.


def _dir_size(path):
    """Bytes the tree under ``path`` holds, unreadable parts skipped."""
    total = 0
    stack = [str(path)]
    while stack:
        try:
            entries = list(os.scandir(stack.pop()))
        except OSError:
            continue
        for entry in entries:
            try:
                if entry.is_dir(follow_symlinks=False):
                    stack.append(entry.path)
                elif entry.is_file(follow_symlinks=False):
                    total += entry.stat(follow_symlinks=False).st_size
            except OSError:
                continue
    return total


def _managed_parts(path):
    """``path`` as its parts under GAMES, or None when it is outside it.

    Resolved on both sides, so a symlinked data directory -- which is what
    "Storage ▸ Browse…" leaves behind -- is still recognised as the managed
    tree rather than treated as somewhere the launcher must not touch.
    """
    try:
        return Path(path).resolve().relative_to(GAMES.resolve()).parts
    except (OSError, ValueError):
        return None


def _selected_root():
    """The build folder the launcher would start next, or None."""
    configured = (load_settings().get("game_dir") or "").strip()
    if not configured:
        return None
    try:
        return Path(configured).expanduser().resolve()
    except OSError:
        return None


def _holds(folder, path):
    """Whether ``path`` is ``folder`` or something inside it."""
    if path is None:
        return False
    try:
        path.relative_to(Path(folder).resolve())
    except (OSError, ValueError):
        return False
    return True


def _build_entry(folder, edition_entry, version, family, selected,
                  with_size=True, legacy=False):
    root = xodus.game_root(folder)
    record = _install_record(folder)
    managed = _managed_parts(folder) is not None
    return {
        "family": family,
        "id": edition_entry["id"] if edition_entry else None,
        # Kept under the old key because the GUI renders it in several
        # places where renaming to "name" would touch every layout. New
        # code should read "id" and look the name up from the registry.
        "name": edition_entry["name"] if edition_entry else "Minecraft",
        "version": (version
                 or version_str_for(family, root or folder)
                 or "unknown"),
        "path": Path(folder),
        "size": _dir_size(folder) if with_size else None,
        # Complete *and* decryptable: a Store build whose package went missing
        # still has an exe and a manifest and cannot be started (#216), and
        # the only thing to do with it is download it again.
        "playable": _game_root(folder) is not None,
        "in_use": _holds(folder, selected),
        "installed_at": record.get("installed"),
        # Only what the launcher itself downloaded is the launcher's to
        # delete; a folder the player imported stays theirs.
        "managed": managed,
        # In the tree the launcher owns, but not under the new family key:
        # either a pre-E3 Bedrock install (still at games/<id>/<version>/)
        # or a copy the launcher cannot re-download. Real builds, listed
        # and removable like the rest.
        "legacy": legacy,
    }


def installed_builds(with_size=True):
    """Every installed build across all families, newest first.

    Walks two shapes the on-disk tree can have:

      * ``games/<family>/<id>/<version>/`` -- the current layout.
      * ``games/<id>/<version>/`` -- a pre-E3 Bedrock install that the
        migration has not yet lifted into ``games/minecraft-bedrock/<id>/``.
        Listed under ``family="minecraft-bedrock"`` with ``legacy=True`` so
        the GUI can show it, run it and offer to delete it without
        pretending it was downloaded into the family-keyed tree.

    A build folder the player pointed the launcher at from somewhere else
    is also listed (so the active selection is always shown) and marked
    ``managed=False`` so nothing offers to delete it.

    ``with_size=False`` skips walking each build, for callers that only need
    to know what is there.
    """
    selected = _selected_root()
    products_by_family_id = PRODUCTS_BY_FAMILY_ID()
    out, seen = [], set()
    try:
        top = sorted(GAMES.iterdir())
    except OSError:
        top = []
    for entry in top:
        if not entry.is_dir() or entry.is_symlink():
            continue
        # Pre-Store layout: games/<version>/, with no edition folder above
        # it (older installs copied from before Microsoft Store downloads).
        # The folder name is a version string, not an edition id -- and not
        # a known family either -- so it falls through both branches below
        # and is recognised by the fact it holds a complete build on its
        # own.
        if (xodus.edition(entry.name) is None
                and entry.name not in {p["family"] for p in list_editions(True)}
                and xodus.game_root(entry) is not None):
            out.append(_build_entry(
                entry, None, entry.name, BEDROCK_FAMILY,
                selected, with_size, legacy=True))
            seen.add(entry.resolve())
            continue
        # Pre-E3 Bedrock layout: games/<edition>/<version>/. The first
        # level is an edition id, not a family.
        if entry.name in {p["id"] for p in list_editions(True)}:
            edition_entry = xodus.edition(entry.name)
            try:
                builds = sorted(entry.iterdir())
            except OSError:
                continue
            for build in builds:
                if not build.is_dir() or xodus.game_root(build) is None:
                    continue
                out.append(_build_entry(
                    build, edition_entry, build.name, BEDROCK_FAMILY,
                    selected, with_size, legacy=True))
                seen.add(build.resolve())
            continue
        # Current layout: games/<family>/<edition>/<version>/. Anything
        # else at this depth is left alone (it is not a build folder the
        # launcher can start).
        family_products = [p for p in list_editions(True)
                           if p.get("family") == entry.name]
        if not family_products:
            continue
        try:
            editions = sorted(entry.iterdir())
        except OSError:
            continue
        for edition_dir in editions:
            if not edition_dir.is_dir() or edition_dir.is_symlink():
                continue
            edition_entry = next(
                (p for p in family_products if p["id"] == edition_dir.name),
                None)
            if edition_entry is None:
                continue
            try:
                builds = sorted(edition_dir.iterdir())
            except OSError:
                continue
            for build in builds:
                if not build.is_dir() or xodus.game_root(build) is None:
                    continue
                out.append(_build_entry(
                    build, edition_entry, build.name, entry.name,
                    selected, with_size, legacy=False))
                seen.add(build.resolve())
    if selected is not None and _game_root(selected) is not None and not any(
            _holds(build["path"], selected) for build in out):
        selection = _selection_from_path(selected)
        family = selection["family"] if selection else None
        edition_id = selection["id"] if selection else None
        edition_entry = (xodus.edition(edition_id) if edition_id else None)
        out.append(_build_entry(
            selected, edition_entry, None, family,
            selected, with_size, legacy=False))
    out.sort(key=lambda build: version_key_for(
                 build.get("family"), build["version"]), reverse=True)
    return out


def remove_build(path):
    """Delete one downloaded build and return the bytes that frees.

    Worlds, settings and screenshots are not in there -- see the note at the
    top of this section -- so this takes the download and nothing else. What
    it does have to take with it is the *selection*: the launcher starts
    whatever ``game_dir`` names, and a setting left pointing at a folder that
    is gone turns the next PLAY into a launch failure instead of the download
    it should be.
    """
    from .prefix import _mc_running

    folder = Path(path).expanduser()
    try:
        folder = folder.resolve()
    except OSError as exc:
        raise BolError(f"Could not remove {path}: {exc}") from exc
    parts = _managed_parts(folder)
    # Three depths are valid:
    #   games/<family>/<edition>/<version>/   -- the current layout.
    #   games/<edition>/<version>/            -- pre-E3 Bedrock (still served
    #                                            by the GUI until the migration
    #                                            runs).
    #   games/<version>/                      -- the very oldest layout, from
    #                                            before the move to the Store.
    # Anything else is GAMES itself, a folder deeper inside a build, or --
    # the one that would really hurt -- games/<family>/ or games/<edition>/,
    # which hold every build of that edition. A delete aimed at any of those
    # is a bug, not a request.
    if not parts:
        raise BolError(
            f"{folder} is not a Minecraft build this launcher downloaded, so "
            "it will not be removed. Delete it yourself if you are sure.")
    if len(parts) == 3:
        family_dir, edition_dir, _version = parts
        if PRODUCTS_BY_FAMILY_ID().get((family_dir, edition_dir)) is None:
            raise BolError(
                f"{folder} is not a Minecraft build this launcher "
                "downloaded, so it will not be removed. Delete it yourself "
                "if you are sure.")
    elif len(parts) == 2:
        edition_dir, _version = parts
        # games/<family>/ is the tree the launcher owns -- and has the same
        # depth as the pre-Store layout, so length alone does not gate it.
        if edition_dir in {p["family"] for p in list_editions(True)}:
            raise BolError(
                f"{folder} is not a Minecraft build this launcher downloaded, "
                "so it will not be removed. Delete it yourself if you are "
                "sure.")
        if xodus.edition(edition_dir) is None:
            # games/<version>/, the pre-Store layout. Allowed; not gated.
            pass
    else:
        raise BolError(
            f"{folder} is not a Minecraft build this launcher downloaded, so "
            "it will not be removed. Delete it yourself if you are sure.")
    if not folder.is_dir():
        raise BolError(f"There is no build in {folder} to remove.")
    # And it has to look like a build: a game inside it, the record the
    # installer wrote, or the encrypted package a download leaves. Whatever
    # else has found its way in there is not this function's to delete.
    if not (xodus.game_root(folder) or xodus.has_package_cache(folder)
            or (folder / _INSTALL_METADATA).exists()):
        raise BolError(
            f"{folder} holds no Minecraft build, so it will not be removed.")
    if _mc_running():
        raise BolError(
            "Minecraft is running. Close the game, then remove the build.")

    selected = _selected_root()
    freed = _dir_size(folder)
    shutil.rmtree(folder)
    if _holds(folder, selected):
        # The launcher runs the game through this symlink, so it goes with
        # the folder it points into. The pointer file is what the GUI and
        # the new layout look at, so the build that just went away is dropped
        # from there too: leaving a dangling pointer would only confuse the
        # next launch.
        try:
            if CONTENT.is_symlink():
                CONTENT.unlink()
        except OSError:
            pass
        settings = load_settings()
        settings.pop("game_dir", None)
        settings.pop("library.current", None)
        settings.pop("mc_edition", None)
        settings.pop("mc_version", None)
        save_settings(settings)
        try:
            LIBRARY_POINTER.unlink()
        except OSError:
            pass
    return freed


def mc_version_str(game_dir: Path):
    """The Bedrock-specific version parser.

    Bedrock-only because the manifest format is Bedrock's. The launcher
    is the right entry point for multi-family callers -- this shim stays
    so the Bedrock-specific surface area (fixups, content, doctor) does
    not change.
    """
    for nm in ("appxmanifest.xml", "AppxManifest.xml"):
        man = game_dir / nm
        if man.exists():
            m = re.search(r'Identity[^>]*Version="(\d+)\.(\d+)\.(\d+)\.\d+"',
                          man.read_text(errors="ignore"))
            if m:
                p = m.group(3)
                # Bedrock packs "<minor><patch>" into the Appx 3rd field, e.g.
                # 2004 -> "20.4", 3005 -> "30.5", 0301 -> "3.1" — split it back
                # so the result matches Mojang's version numbers (e.g. 1.26.20.4).
                if len(p) >= 3:
                    return f"{m.group(1)}.{m.group(2)}.{int(p[:2])}.{int(p[2:])}"
                return f"{m.group(1)}.{m.group(2)}.{int(p)}"
    return None


def _vt(v):
    try:
        return tuple(int(x) for x in v.split("."))
    except Exception:
        return (0,)


def _auto_selection(s):
    """The (edition, version) to install when the caller named neither.

    An unset or no-longer-listed version falls through to the newest build,
    which is what install_game() does with it.
    """
    want = (s.get("mc_edition") or "").strip()
    chosen = xodus.edition(want) if want else None
    if chosen is None:
        editions = list_editions(include_beta=False) or list_editions(True)
        if not editions:
            die("No Minecraft edition is available.")
        chosen = editions[0]
    return chosen, (s.get("mc_version") or "").strip() or None
