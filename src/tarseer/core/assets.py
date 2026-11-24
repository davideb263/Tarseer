"""Choosing the right tarball among a release's files."""

from __future__ import annotations

import platform
import re

from .models import Asset

TARBALL_SUFFIXES = (
    ".tar.gz", ".tgz",
    ".tar.xz", ".txz",
    ".tar.bz2", ".tbz2", ".tbz",
    ".tar.zst", ".tzst",
    ".tar",
)

# Tokens identifying a CPU architecture, keyed by normalized machine name.
ARCH_TOKENS = {
    "x86_64": {"x86_64", "amd64", "x64", "linux64", "64bit"},
    "aarch64": {"aarch64", "arm64", "armv8"},
    # armv6 builds run on armv7 too.
    "armv7l": {"armv7", "armv7l", "armv7hf", "armhf", "arm", "armv6", "armv6l", "armel"},
    "i686": {"i386", "i686", "x86", "386", "32bit", "linux32"},
    # Rust targets spell these out: powerpc64le-unknown-linux-gnu, powerpc64-...
    "ppc64le": {"ppc64le", "ppc64el", "powerpc64le"},
    "ppc64": {"ppc64", "powerpc64"},
    "s390x": {"s390x"},
    "riscv64": {"riscv64"},  # also riscv64gc etc., see _COMPOUND_ARCH
    "loongarch64": {"loongarch64", "loong64"},
    "mips64": {"mips64", "mips64el"},
    "mips": {"mips", "mipsel"},
}
FOREIGN_OS_TOKENS = {
    "windows", "win", "win32", "win64", "macos", "mac", "osx", "darwin", "apple",
    "freebsd", "openbsd", "netbsd", "android", "ios",
}
UNWANTED_TOKENS = {"src", "source", "sources", "debug", "dbg", "dbgsym", "symbols"}


def normalize_machine(machine: str | None = None) -> str:
    m = (machine or platform.machine()).lower()
    return {"amd64": "x86_64", "arm64": "aarch64"}.get(m, m)


def is_tarball(name: str) -> bool:
    return name.lower().endswith(TARBALL_SUFFIXES)


# Arch spellings that contain a separator or carry an ISA extension suffix
# (Rust's riscv64gc); collapsed to one token before splitting.
_COMPOUND_ARCH = (
    (re.compile(r"x86[-_]64"), "amd64"),
    (re.compile(r"aarch[-_]64"), "aarch64"),
    (re.compile(r"riscv64[a-z0-9]*"), "riscv64"),
)


def _tokens(name: str) -> set[str]:
    name = name.lower()
    for regex, token in _COMPOUND_ARCH:
        name = regex.sub(token, name)
    return {t for t in re.split(r"[^a-z0-9]+", name) if t}


def score_asset(name: str, machine: str | None = None, repo: str | None = None) -> int | None:
    """Higher is better; None means the file is not usable on this machine.

    `repo` ("owner/name") breaks ties between editions of the same release,
    e.g. prefers "sparrowwallet-*" over "sparrowserver-*" for sparrowwallet/sparrow.
    """
    if not is_tarball(name):
        return None
    machine = normalize_machine(machine)
    tokens = _tokens(name)
    if tokens & FOREIGN_OS_TOKENS:
        return None
    own = ARCH_TOKENS.get(machine, {machine})
    foreign = set().union(*(t for m, t in ARCH_TOKENS.items() if m != machine)) - own
    if tokens & foreign and not tokens & own:
        return None
    score = 0
    if tokens & own:
        score += 10
    if "linux" in tokens:
        score += 5
    if tokens & UNWANTED_TOKENS:
        score -= 20
    if repo:
        if tokens & _tokens(repo):
            score += 3
    return score


def candidate_assets(
    assets: list[Asset], machine: str | None = None, repo: str | None = None
) -> list[Asset]:
    """Usable tarballs, best guess first."""
    scored = [(score_asset(a.name, machine, repo), i, a) for i, a in enumerate(assets)]
    usable = [(s, i, a) for s, i, a in scored if s is not None]
    usable.sort(key=lambda t: (-t[0], t[1]))
    return [a for _, _, a in usable]


def selectable_assets(
    assets: list[Asset], machine: str | None = None, repo: str | None = None
) -> list[Asset]:
    """Files to offer the user: usable tarballs best first or, if nothing
    looks right for this machine, every tarball so the user can decide."""
    return candidate_assets(assets, machine, repo) or [a for a in assets if is_tarball(a.name)]


def validate_pattern(pattern: str) -> str:
    """Raise ValueError unless `pattern` is a usable regular expression."""
    if not pattern.strip():
        raise ValueError("The release file pattern cannot be empty")
    try:
        re.compile(pattern)
    except re.error as e:
        raise ValueError(f"Invalid release file pattern {pattern!r}: {e}") from e
    return pattern


def _version_from_tag(tag: str) -> str:
    """'v7.2.0' -> '7.2.0', 'release-1.4' -> '1.4', '2.4.2' -> '2.4.2'."""
    return re.sub(r"^[^0-9]*", "", tag)


def _version_wildcard(version: str) -> str:
    """A character class matching versions shaped like `version`.

    Only allows what the version itself contains (plus digits and dots), so
    '1.2' -> '[0-9.]+' won't swallow a variant suffix such as '1.3-musl'.
    """
    chars = "0-9."
    if any(c.isalpha() for c in version):
        chars += "A-Za-z"
    for c in sorted({c for c in version if not c.isalnum() and c != "."}):
        chars += re.escape(c)
    return f"[{chars}]+"


def pattern_from_name(name: str, tag: str) -> str:
    """Turn a concrete file name into a regex that also matches future releases.

    'calibre-8.1.0-x86_64.txz' (tag v8.1.0) -> '^calibre\\-[0-9.]+\\-x86_64\\.txz$'
    """
    version = _version_from_tag(tag)
    if version and version in name:
        parts = name.split(version)
        return "^" + _version_wildcard(version).join(re.escape(p) for p in parts) + "$"
    # Version not literally in the name: wildcard every dotted number instead.
    out, last = [], 0
    for m in re.finditer(r"\d+(?:\.\d+)+", name):
        out.append(re.escape(name[last:m.start()]))
        out.append(r"[\d.]+")
        last = m.end()
    out.append(re.escape(name[last:]))
    return "^" + "".join(out) + "$"


def match_asset(assets: list[Asset], pattern: str) -> Asset | None:
    regex = re.compile(pattern)
    for asset in assets:
        if regex.fullmatch(asset.name):
            return asset
    return None
