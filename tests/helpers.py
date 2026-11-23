"""Builders for fake tarballs and app trees used across tests."""

import io
import stat
import tarfile
from pathlib import Path

ELF = b"\x7fELF" + b"\0" * 60
PNG_256 = b"\x89PNG\r\n\x1a\n" + b"\0\0\0\rIHDR" + (256).to_bytes(4, "big") * 2 + b"\0" * 16


def make_tarball(path: Path, files: dict[str, bytes], mode: str = "w:gz",
                 executables: tuple[str, ...] = (), symlinks: dict[str, str] | None = None) -> Path:
    """files maps archive member names to content."""
    with tarfile.open(path, mode) as tf:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o755 if name in executables else 0o644
            tf.addfile(info, io.BytesIO(data))
        for name, target in (symlinks or {}).items():
            info = tarfile.TarInfo(name)
            info.type = tarfile.SYMTYPE
            info.linkname = target
            tf.addfile(info)
    return path


def write_tree(root: Path, files: dict[str, bytes], executables: tuple[str, ...] = ()) -> Path:
    for name, data in files.items():
        p = root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        if name in executables:
            p.chmod(p.stat().st_mode | stat.S_IXUSR)
    return root
