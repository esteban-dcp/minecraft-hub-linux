"""Safe extraction helpers for untrusted TAR archives."""
# SPDX-License-Identifier: MIT

import os
import posixpath
import shutil
import tarfile
from pathlib import Path, PurePosixPath


def _normalise_member_name(member):
    raw_name = member.name
    raw_path = PurePosixPath(raw_name)
    if (not raw_name or raw_path.is_absolute()
            or ".." in raw_path.parts):
        raise ValueError(f"unsafe path in TAR archive: {raw_name}")
    name = posixpath.normpath(raw_name)
    if name in ("", "."):
        if member.isdir():
            return None
        raise ValueError(f"unsafe path in TAR archive: {raw_name}")
    return name


def _normalise_link_target(name, target, *, symlink):
    if not target or PurePosixPath(target).is_absolute():
        raise ValueError(
            f"unsafe link in TAR archive: {name} -> {target}")
    base = posixpath.dirname(name) if symlink else ""
    resolved = posixpath.normpath(posixpath.join(base, target))
    if resolved in ("", ".", "..") or resolved.startswith("../"):
        raise ValueError(
            f"unsafe link in TAR archive: {name} -> {target}")
    return resolved


def _remove_extraction_tree(path):
    """Remove a failed staging tree without following its symlinks."""
    try:
        if path.is_symlink() or path.is_file():
            path.unlink()
        elif path.exists():
            shutil.rmtree(path)
    except FileNotFoundError:
        pass


def safe_extract_tar(archive, destination):
    """Extract an untrusted :class:`tarfile.TarFile` into an empty directory.

    Every member is validated before extraction.  Absolute paths, ``..`` path
    components, duplicate/conflicting entries, links that resolve outside the
    destination and special files are rejected.  Relative symlinks and hard
    links are supported when their targets remain wholly inside the archive.

    Extraction is manual rather than delegated to ``TarFile.extractall`` so
    the policy is identical on every supported Python version.  The staging
    directory is removed if an error occurs after extraction has started.
    """
    destination = Path(destination)
    members = archive.getmembers()
    entries = {}
    directories = []

    # Validate the complete archive before creating or changing destination.
    for member in members:
        name = _normalise_member_name(member)
        if name is None:
            continue
        if name in entries:
            raise ValueError(f"duplicate path in TAR archive: {member.name}")

        target = None
        if member.isdir():
            kind = "directory"
            directories.append((name, member.mode))
        elif member.isfile():
            kind = "file"
        elif member.issym():
            kind = "symlink"
            target = _normalise_link_target(
                name, member.linkname, symlink=True)
        elif member.islnk():
            kind = "hardlink"
            target = _normalise_link_target(
                name, member.linkname, symlink=False)
        else:
            # Devices, FIFOs, sockets and implementation-specific entries do
            # not belong in either of the application archives using this
            # helper and can have dangerous extraction semantics.
            raise ValueError(
                f"unsupported entry in TAR archive: {member.name}")
        entries[name] = (member, kind, target)

    # No archive member may be nested below another member that is not a real
    # directory.  In particular, this prevents writes through a symlink even
    # when the link itself has a destination-internal target.
    for name in entries:
        parts = PurePosixPath(name).parts
        for end in range(1, len(parts)):
            parent = PurePosixPath(*parts[:end]).as_posix()
            parent_entry = entries.get(parent)
            if parent_entry and parent_entry[1] != "directory":
                raise ValueError(
                    f"archive member is nested below a {parent_entry[1]}: "
                    f"{name}")

    # Hard links must ultimately name a regular file supplied by this same
    # archive.  Resolving the graph up front also rejects cycles and links to
    # directories/symlinks before any filesystem changes occur.
    hardlink_targets = {}

    def regular_target(name, trail=()):
        if name in trail:
            raise ValueError(f"hard-link cycle in TAR archive: {name}")
        entry = entries.get(name)
        if entry is None:
            raise ValueError(
                f"hard-link target missing from TAR archive: {name}")
        _member, kind, target = entry
        if kind == "file":
            return name
        if kind != "hardlink":
            raise ValueError(
                f"hard-link target is not a regular file: {name}")
        return regular_target(target, trail + (name,))

    for name, (_member, kind, target) in entries.items():
        if kind == "hardlink":
            hardlink_targets[name] = regular_target(target, (name,))

    if destination.is_symlink():
        raise ValueError("TAR extraction destination must not be a symlink")
    if destination.exists():
        if not destination.is_dir():
            raise ValueError("TAR extraction destination is not a directory")
        if any(destination.iterdir()):
            raise ValueError("TAR extraction destination is not empty")

    extraction_started = False
    try:
        destination.mkdir(parents=True, exist_ok=True, mode=0o700)
        extraction_started = True

        # Use writable temporary directory modes; archive modes are restored
        # only after every child has been created.
        for name, (_member, kind, _target) in entries.items():
            if kind == "directory":
                (destination / name).mkdir(
                    parents=True, exist_ok=True, mode=0o700)

        # Extract only real files first.  Links are deliberately created later
        # so no regular-file write can ever traverse an archive-created link.
        for name, (member, kind, _target) in entries.items():
            if kind != "file":
                continue
            output = destination / name
            output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            if output.exists() or output.is_symlink():
                raise ValueError(f"conflicting path in TAR archive: {name}")
            source = archive.extractfile(member)
            if source is None:
                raise tarfile.ExtractError(
                    f"could not read TAR archive member: {name}")
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            fd = os.open(output, flags, 0o600)
            try:
                with source, os.fdopen(fd, "wb") as stream:
                    fd = -1
                    shutil.copyfileobj(source, stream)
            finally:
                if fd >= 0:
                    os.close(fd)
            output.chmod((member.mode & 0o777) | 0o600)

        for name, target in hardlink_targets.items():
            output = destination / name
            output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            if output.exists() or output.is_symlink():
                raise ValueError(f"conflicting path in TAR archive: {name}")
            os.link(destination / target, output, follow_symlinks=False)

        for name, (member, kind, _target) in entries.items():
            if kind != "symlink":
                continue
            output = destination / name
            output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            if output.exists() or output.is_symlink():
                raise ValueError(f"conflicting path in TAR archive: {name}")
            output.symlink_to(member.linkname)

        for name, mode in sorted(
                directories,
                key=lambda item: len(PurePosixPath(item[0]).parts),
                reverse=True):
            (destination / name).chmod((mode & 0o777) | 0o700)
    except Exception:
        if extraction_started:
            _remove_extraction_tree(destination)
        raise
