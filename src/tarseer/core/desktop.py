"""Guessing the executable/icon of an extracted app and writing its menu entry."""

from __future__ import annotations

import os
import re
import struct
from pathlib import Path

from .models import ManagedApp

MAX_EXEC_DEPTH = 3
MAX_ICON_DEPTH = 5
ICON_SUFFIXES = (".svg", ".png")
# Folders never holding the main program (bundled runtimes, libraries).
EXEC_SKIP_DIRS = {"lib", "lib64", "libexec", "share", "locale", "plugins", "__pycache__"}
ICON_SKIP_DIRS = {"doc", "docs", "locale", "__pycache__"}
EXEC_NOISE = re.compile(
    r"(chrome[-_]sandbox|crashpad|crash[-_]?handler|uninstall|update|helper|"
    r"debug|java$|jspawnhelper|keytool|\.so(\.|$))",
    re.IGNORECASE,
)


def _simplify(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _walk(root: Path, max_depth: int, skip: set[str]):
    """Yield (path, depth) for regular files, skipping symlinked dirs and `skip` dirs."""
    stack = [(root, 0)]
    while stack:
        current, depth = stack.pop()
        try:
            entries = sorted(current.iterdir())
        except OSError:
            continue
        for entry in entries:
            if entry.is_dir() and not entry.is_symlink():
                if depth + 1 < max_depth and entry.name.lower() not in skip:
                    stack.append((entry, depth + 1))
            elif entry.is_file():
                yield entry, depth


def _is_program(path: Path) -> str | None:
    """'elf', 'script', or None if not an executable program."""
    try:
        if not os.access(path, os.X_OK):
            return None
        with open(path, "rb") as f:
            magic = f.read(4)
    except OSError:
        return None
    if magic == b"\x7fELF":
        return "elf"
    if magic.startswith(b"#!"):
        return "script"
    return None


def guess_executable(install_dir: Path, hint: str) -> str | None:
    """Relative path of the most likely main program, or None."""
    want = _simplify(hint)
    best: tuple[int, str] | None = None
    for path, depth in _walk(install_dir, MAX_EXEC_DEPTH, EXEC_SKIP_DIRS):
        if EXEC_NOISE.search(path.name):
            continue
        kind = _is_program(path)
        if kind is None:
            continue
        name = _simplify(path.stem if kind == "script" else path.name)
        score = 0
        if want and name == want:
            score += 100
        elif want and name.startswith(want):
            score += 30
        elif want and want in name:
            score += 15
        score += 10 if depth == 0 else -3 * depth
        if depth == 1 and path.parent.name == "bin":
            score += 12
        if kind == "script":
            score -= 2
        rel = path.relative_to(install_dir).as_posix()
        if best is None or score > best[0]:
            best = (score, rel)
    return best[1] if best else None


def _png_width(path: Path) -> int:
    try:
        with open(path, "rb") as f:
            header = f.read(24)
        if header[:8] == b"\x89PNG\r\n\x1a\n":
            return struct.unpack(">I", header[16:20])[0]
    except OSError:
        pass
    return 0


def guess_icon(install_dir: Path, hint: str) -> str | None:
    want = _simplify(hint)
    best: tuple[float, str] | None = None
    for path, depth in _walk(install_dir, MAX_ICON_DEPTH, ICON_SKIP_DIRS):
        suffix = path.suffix.lower()
        if suffix not in ICON_SUFFIXES:
            continue
        name = _simplify(path.stem)
        score = 0.0
        if want and name == want:
            score += 60
        elif want and want in name:
            score += 30
        elif "icon" in name or "logo" in name:
            score += 10
        else:
            continue  # random images (toolbar buttons, docs) are not app icons
        if suffix == ".svg":
            score += 20
        else:
            width = _png_width(path)
            if width < 32:
                continue
            score += min(width, 512) / 32  # prefer large, up to 512px
        score -= depth
        rel = path.relative_to(install_dir).as_posix()
        if best is None or score > best[0]:
            best = (score, rel)
    return best[1] if best else None


def _quote_exec_arg(arg: str) -> str:
    """Quote per the Desktop Entry spec, then escape for the key-file level."""
    quoted = '"' + re.sub(r'(["`$\\])', r"\\\1", arg) + '"'
    return quoted.replace("\\", "\\\\")


def _escape_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", " ").replace("\r", " ").replace("\t", " ")


def desktop_file_path(applications_dir: Path, app: ManagedApp) -> Path:
    return applications_dir / f"tarseer-{app.id}.desktop"


def render_desktop_entry(app: ManagedApp, install_dir: Path) -> str:
    if not app.executable:
        raise ValueError("App has no executable set")
    lines = [
        "[Desktop Entry]",
        "Type=Application",
        f"Name={_escape_value(app.name)}",
        f"Comment={_escape_value(f'Installed by Tarseer from github.com/{app.repo}')}",
        f"Exec={_quote_exec_arg(str(install_dir / app.executable))}",
        f"Path={_escape_value(str(install_dir))}",
        f"Icon={_escape_value(str(install_dir / app.icon)) if app.icon else 'application-x-executable'}",
        "Terminal=false",
        f"X-Tarseer-App={app.id}",
    ]
    return "\n".join(lines) + "\n"


def write_desktop_entry(applications_dir: Path, app: ManagedApp, install_dir: Path) -> Path:
    applications_dir.mkdir(parents=True, exist_ok=True)
    path = desktop_file_path(applications_dir, app)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(render_desktop_entry(app, install_dir), encoding="utf-8")
    os.replace(tmp, path)
    return path


def remove_desktop_entry(applications_dir: Path, app: ManagedApp) -> None:
    desktop_file_path(applications_dir, app).unlink(missing_ok=True)
