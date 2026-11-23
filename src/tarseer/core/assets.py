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
    "armv7l": {"armv7", "armv7l", "armhf", "arm"},
    "i686": {"i386", "i686", "x86", "386", "32bit", "linux32"},
    "ppc64le": {"ppc64le", "ppc64el"},
    "s390x": {"s390x"},
    "riscv64": {"riscv64"},
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


# Arch spellings that contain a separator; collapsed to one token before splitting.
_COMPOUND_ARCH = ((re.compile(r"x86[-_]64"), "amd64"), (re.compile(r"aarch[-_]64"), "aarch64"))


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


def _version_from_tag(tag: str) -> str:
    """'v7.2.0' -> '7.2.0', 'release-1.4' -> '1.4', '2.4.2' -> '2.4.2'."""
    return re.sub(r"^[^0-9]*", "", tag)


def pattern_from_name(name: str, tag: str) -> str:
    """Turn a concrete file name into a regex that also matches future releases.

    'calibre-8.1.0-x86_64.txz' (tag v8.1.0) -> '^calibre\\-.+?\\-x86_64\\.txz$'
    """
    version = _version_from_tag(tag)
    if version and version in name:
        parts = name.split(version)
        return "^" + ".+?".join(re.escape(p) for p in parts) + "$"
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
