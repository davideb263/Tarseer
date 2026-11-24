"""Extracting tarballs into place, with a backup copy and rollback.

Layout for an app with id "calibre":
    <install_root>/calibre/       current version
    <install_root>/calibre.bak/   previous version (rollback target)
    <install_root>/calibre.swap/  only while rolling back (recovered if left over)
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


def _swap_path(target: Path) -> Path:
    return target.with_name(target.name + ".swap")


def _recover(target: Path) -> None:
    """Put back a version left in `.swap` by an interrupted rollback."""
    swap = _swap_path(target)
    if not swap.exists():
        return
    if not target.exists():
        os.rename(swap, target)  # stopped before the backup moved in
    elif not backup_path(target).exists():
        os.rename(swap, backup_path(target))  # stopped before the last step
    else:
        shutil.rmtree(swap)  # every version is in place: just a stale copy


# Space always left free on the install filesystem, so an archive bigger than
# it unpacks to (or a decompression bomb) can't fill the disk.
DISK_RESERVE = 512 * 1024 * 1024


class _TooLarge(Exception):
    pass


def extract_tarball(archive: Path, dest: Path) -> None:
    """Extract any supported compression; refuse unsafe members (absolute
    paths, '..', links escaping dest, device files) and archives that would
    leave less than DISK_RESERVE free."""
    budget = shutil.disk_usage(dest).free - DISK_RESERVE
    total = 0

    def data_with_budget(member: tarfile.TarInfo, path: str) -> tarfile.TarInfo | None:
        nonlocal total
        member = tarfile.data_filter(member, path)
        if member is not None and member.isreg():
            total += member.size
            if total > budget:
                raise _TooLarge()
        return member

    try:
        with tarfile.open(archive, mode="r:*") as tf:
            tf.extractall(dest, filter=data_with_budget)
    except _TooLarge:
        raise InstallError("Not enough disk space to unpack the archive") from None
    except tarfile.FilterError as e:
        raise InstallError(f"Archive contains an unsafe file: {e}") from e
    except (tarfile.TarError, EOFError, OSError) as e:
        raise InstallError(f"Could not extract archive: {e}") from e


def _check_links(root: Path) -> None:
    """Refuse symlinks pointing outside `root`. Extraction only checked them
    against the staging folder; once a single top-level folder becomes the
    install dir, "app/link -> ../other" would point into a sibling app."""
    real_root = root.resolve()
    for dirpath, dirnames, filenames in os.walk(root):
        for name in dirnames + filenames:
            path = Path(dirpath, name)
            if path.is_symlink() and not path.resolve().is_relative_to(real_root):
                rel = path.relative_to(root).as_posix()
                raise InstallError(f"Archive contains an unsafe file: {rel!r} links outside the app folder")


def _content_root(extracted: Path) -> Path:
    """If the archive wrapped everything in one top-level folder, use that
    folder (the script's "subdir" mode); otherwise the files themselves
    ("flat" mode)."""
    entries = list(extracted.iterdir())
    if len(entries) == 1 and entries[0].is_dir() and not entries[0].is_symlink():
        return entries[0]
    return extracted


def install_archive(archive: Path, target: Path, keep_backup: bool = False) -> bool:
    """Replace `target` with the contents of `archive`.

    The current install (if any) becomes the backup, unless `keep_backup` and
    there already is one: then the backup stays and the current install is
    discarded (a reinstall of the same version shouldn't lose the older one).
    On any failure the current install and backup are left untouched.
    Returns True if the backup changed (a new one was made).
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    _recover(target)
    staging = Path(tempfile.mkdtemp(prefix=f".tarseer-{target.name}-", dir=target.parent))
    try:
        extracted = staging / "extracted"
        extracted.mkdir()
        extract_tarball(archive, extracted)
        root = _content_root(extracted)
        if not any(root.iterdir()):
            raise InstallError("Archive is empty")
        _check_links(root)
        # Owner must be able to enter/read the top folder whatever the archive says.
        root.chmod(0o755)

        backup = backup_path(target)
        had_old = target.exists()
        replace_backup = had_old and not (keep_backup and backup.exists())
        # What gets replaced waits in staging (deleted with it) until the new
        # version is in place, so a failed move can restore everything.
        old_backup = staging / "old-backup"
        old_current = staging / "old-current"
        moved_backup = moved_current = discarded_current = False
        try:
            if replace_backup:
                if backup.exists():
                    os.rename(backup, old_backup)
                    moved_backup = True
                os.rename(target, backup)
                moved_current = True
            elif had_old:
                os.rename(target, old_current)
                discarded_current = True
            os.rename(root, target)
        except OSError as e:
            if moved_current:
                os.rename(backup, target)
            if moved_backup:
                os.rename(old_backup, backup)
            if discarded_current:
                os.rename(old_current, target)
            raise InstallError(f"Could not move new version into place: {e}") from e
        return replace_backup
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def rollback(target: Path) -> None:
    """Swap the current install with its backup. Running it twice undoes it."""
    _recover(target)
    backup = backup_path(target)
    if not backup.is_dir():
        raise InstallError("No previous version to roll back to")
    swap = _swap_path(target)
    had_current = target.exists()
    if had_current:
        os.rename(target, swap)
    try:
        os.rename(backup, target)
    except OSError as e:
        if had_current:
            os.rename(swap, target)
        raise InstallError(f"Could not switch versions: {e}") from e
    if had_current:
        try:
            os.rename(swap, backup)
        except OSError as e:
            os.rename(target, backup)
            os.rename(swap, target)
            raise InstallError(f"Could not switch versions: {e}") from e


def remove(target: Path) -> None:
    for path in (target, backup_path(target), _swap_path(target)):
        if path.exists() or path.is_symlink():
            shutil.rmtree(path)
