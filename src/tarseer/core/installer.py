"""Extracting tarballs into place, with a backup copy and rollback.

Layout for an app with id "calibre":
    <install_root>/calibre/       current version
    <install_root>/calibre.bak/   previous version (rollback target)
    <install_root>/.tarseer-*     temporary staging dirs (same filesystem, so
                                  the final swap is an instant rename)
"""

from __future__ import annotations

import os
import shutil
import tarfile
import tempfile
from pathlib import Path


class InstallError(Exception):
    pass


def backup_path(target: Path) -> Path:
    return target.with_name(target.name + ".bak")


def has_backup(target: Path) -> bool:
    return backup_path(target).is_dir()


def extract_tarball(archive: Path, dest: Path) -> None:
    """Extract any supported compression; refuse unsafe members (absolute
    paths, '..', links escaping dest, device files)."""
    try:
        with tarfile.open(archive, mode="r:*") as tf:
            tf.extractall(dest, filter="data")
    except tarfile.FilterError as e:
        raise InstallError(f"Archive contains an unsafe file: {e}") from e
    except (tarfile.TarError, EOFError, OSError) as e:
        raise InstallError(f"Could not extract archive: {e}") from e


def _content_root(extracted: Path) -> Path:
    """If the archive wrapped everything in one top-level folder, use that
    folder (the script's "subdir" mode); otherwise the files themselves
    ("flat" mode)."""
    entries = list(extracted.iterdir())
    if len(entries) == 1 and entries[0].is_dir() and not entries[0].is_symlink():
        return entries[0]
    return extracted


def install_archive(archive: Path, target: Path) -> bool:
    """Replace `target` with the contents of `archive`.

    The current install (if any) becomes the backup. On any failure the
    current install is left untouched. Returns True if a backup was made.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".tarseer-{target.name}-", dir=target.parent))
    try:
        extracted = staging / "extracted"
        extracted.mkdir()
        extract_tarball(archive, extracted)
        root = _content_root(extracted)
        if not any(root.iterdir()):
            raise InstallError("Archive is empty")
        # Owner must be able to enter/read the top folder whatever the archive says.
        root.chmod(0o755)

        backup = backup_path(target)
        had_old = target.exists()
        if had_old:
            if backup.exists():
                shutil.rmtree(backup)
            os.rename(target, backup)
        try:
            os.rename(root, target)
        except OSError as e:
            if had_old:
                os.rename(backup, target)
            raise InstallError(f"Could not move new version into place: {e}") from e
        return had_old
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def rollback(target: Path) -> None:
    """Swap the current install with its backup. Running it twice undoes it."""
    backup = backup_path(target)
    if not backup.is_dir():
        raise InstallError("No previous version to roll back to")
    swap = target.with_name(target.name + ".swap")
    if swap.exists():
        shutil.rmtree(swap)
    if target.exists():
        os.rename(target, swap)
    os.rename(backup, target)
    if swap.exists():
        os.rename(swap, backup)


def remove(target: Path) -> None:
    for path in (target, backup_path(target)):
        if path.exists() or path.is_symlink():
            shutil.rmtree(path)
