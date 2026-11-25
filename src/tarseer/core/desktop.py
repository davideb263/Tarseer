"""Guessing the executable/icon of an extracted app and writing its menu entry."""

from __future__ import annotations

import os
import re
import struct
from pathlib import Path

from .i18n import _
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


_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


def is_safe_relpath(rel: str) -> bool:
    """True if `rel` can go in a menu entry: no control characters (a newline
    in an archive's file name would otherwise add lines to the .desktop file)."""
    return not _CONTROL_CHARS.search(rel)


def _walk(root: Path, max_depth: int, skip: set[str]):
    """Yield (path, depth) for regular files, skipping symlinked dirs, `skip`
    dirs, names unusable in a menu entry and symlinks leading outside `root`."""
    real_root = root.resolve()
    stack = [(root, 0)]
    while stack:
        current, depth = stack.pop()
        try:
            entries = sorted(current.iterdir())
        except OSError:
            continue
        for entry in entries:
            if not is_safe_relpath(entry.name):
                continue
            if entry.is_dir() and not entry.is_symlink():
                if depth + 1 < max_depth and entry.name.lower() not in skip:
                    stack.append((entry, depth + 1))
            elif entry.is_file():
                if entry.is_symlink() and not entry.resolve().is_relative_to(real_root):
                    continue
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


def _rank(scored: list[tuple[float, str]]) -> list[str]:
    scored.sort(key=lambda t: (-t[0], t[1]))
    return [rel for _, rel in scored]


def _score_executables(install_dir: Path, hint: str) -> list[tuple[float, str, bool]]:
    """(score, relative path, plausible-main-program) for every program found."""
    want = _simplify(hint)
    out = []
    for path, depth in _walk(install_dir, MAX_EXEC_DEPTH, EXEC_SKIP_DIRS):
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
        plausible = not EXEC_NOISE.search(path.name)
        if not plausible:
            score -= 1000
        out.append((score, path.relative_to(install_dir).as_posix(), plausible))
    return out


def rank_executables(install_dir: Path, hint: str) -> list[str]:
    """Every program in the install dir, most likely main program first."""
    return _rank([(s, rel) for s, rel, _ in _score_executables(install_dir, hint)])


def guess_executable(install_dir: Path, hint: str) -> str | None:
    """Relative path of the most likely main program, or None."""
    plausible = [(s, rel) for s, rel, ok in _score_executables(install_dir, hint) if ok]
    ranked = _rank(plausible)
    return ranked[0] if ranked else None


def _png_width(path: Path) -> int:
    try:
        with open(path, "rb") as f:
            header = f.read(24)
        if header[:8] == b"\x89PNG\r\n\x1a\n":
            return struct.unpack(">I", header[16:20])[0]
    except OSError:
        pass
    return 0


def _score_icons(install_dir: Path, hint: str) -> list[tuple[float, str, bool]]:
    """(score, relative path, looks-like-the-app-icon) for every usable image."""
    want = _simplify(hint)
    out = []
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
        # Anything else is probably a toolbar button or a doc image.
        relevant = score > 0
        if suffix == ".svg":
            score += 20
        else:
            width = _png_width(path)
            if width < 32:
                continue
            score += min(width, 512) / 32  # prefer large, up to 512px
        score -= depth
        out.append((score, path.relative_to(install_dir).as_posix(), relevant))
    return out


def rank_icons(install_dir: Path, hint: str) -> list[str]:
    """Every usable image in the install dir, most likely app icon first."""
    return _rank([(s, rel) for s, rel, _ in _score_icons(install_dir, hint)])


def guess_icon(install_dir: Path, hint: str) -> str | None:
    relevant = [(s, rel) for s, rel, ok in _score_icons(install_dir, hint) if ok]
    ranked = _rank(relevant)
    return ranked[0] if ranked else None


def _quote_exec_arg(arg: str) -> str:
    """Quote per the Desktop Entry spec, then escape for the key-file level.
    A literal '%' must be '%%' (even inside quotes), or it reads as a field code."""
    quoted = '"' + re.sub(r'(["`$\\])', r"\\\1", arg).replace("%", "%%") + '"'
    return quoted.replace("\\", "\\\\")


def _escape_value(value: str) -> str:
    return _CONTROL_CHARS.sub(" ", value.replace("\\", "\\\\"))


LEGACY_PREFIX = "tarseer-"  # names used before v0.1 switched to plain "<id>.desktop"


class DesktopConflictError(Exception):
    """The preferred .desktop name is taken by a file Tarseer did not create."""

    def __init__(self, path: Path, app_name: str):
        self.path = path
        shown = str(path).replace(str(Path.home()), "~", 1)
        super().__init__(
            _("{path} already exists and was not created by Tarseer. "
              "Remove or rename it so Tarseer can add {name} to the app grid.").format(
                path=shown, name=app_name)
        )


def preferred_desktop_path(applications_dir: Path, app: ManagedApp) -> Path:
    return applications_dir / f"{app.id}.desktop"


def legacy_desktop_path(applications_dir: Path, app: ManagedApp) -> Path:
    return applications_dir / f"{LEGACY_PREFIX}{app.id}.desktop"


def is_owned(path: Path, app_id: str) -> bool:
    """True if `path` is a menu entry Tarseer wrote for `app_id`."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return f"\nX-Tarseer-App={app_id}\n" in f"\n{text}"


def render_desktop_entry(app: ManagedApp, install_dir: Path) -> str:
    if not app.executable:
        raise ValueError(_("App has no executable set"))
    for rel in (app.executable, app.icon):
        if rel is not None and not is_safe_relpath(rel):
            raise ValueError(_("Unusable file name for a menu entry: {name!r}").format(name=rel))
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
    """Write the menu entry as "<id>.desktop", never overwriting a foreign file.

    If that name is taken by someone else, keep updating the file Tarseer
    already owns (e.g. a legacy "tarseer-<id>.desktop"); if there is none,
    raise DesktopConflictError. Updates `app.desktop_file` and removes the
    previously owned file when the entry moves to the preferred name.
    """
    applications_dir.mkdir(parents=True, exist_ok=True)
    preferred = preferred_desktop_path(applications_dir, app)
    owned = [p for p in (
        applications_dir / app.desktop_file if app.desktop_file else None,
        legacy_desktop_path(applications_dir, app),
    ) if p is not None and p != preferred and is_owned(p, app.id)]

    if not preferred.exists() or is_owned(preferred, app.id):
        target = preferred
    elif owned:
        target = owned[0]
    else:
        raise DesktopConflictError(preferred, app.name)

    tmp = target.with_name(f".{target.name}.tmp")
    tmp.write_text(render_desktop_entry(app, install_dir), encoding="utf-8")
    os.replace(tmp, target)
    for old in owned:
        if old != target:
            old.unlink(missing_ok=True)
    app.desktop_file = target.name
    return target


def remove_desktop_entry(applications_dir: Path, app: ManagedApp) -> None:
    """Delete every menu entry Tarseer wrote for `app`; leave foreign files alone."""
    candidates = {
        preferred_desktop_path(applications_dir, app),
        legacy_desktop_path(applications_dir, app),
    }
    if app.desktop_file:
        candidates.add(applications_dir / app.desktop_file)
    for path in candidates:
        if is_owned(path, app.id):
            path.unlink(missing_ok=True)
    app.desktop_file = None
