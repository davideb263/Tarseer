"""Data types shared by the core modules."""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path


@dataclass
class Paths:
    """Every host location Tarseer reads or writes. Injectable for tests."""

    install_root: Path
    applications_dir: Path
    config_file: Path

    @classmethod
    def default(cls) -> "Paths":
        # Development: keep everything inside one throwaway folder.
        if dev_root := os.environ.get("TARSEER_DEV_ROOT"):
            root = Path(dev_root)
            return cls(root / "opt", root / "applications", root / "config" / "apps.json")
        home = Path.home()
        # Inside Flatpak, XDG_CONFIG_HOME points to ~/.var/app/<id>/config,
        # which is exactly where our own config should live.
        config_home = Path(os.environ.get("XDG_CONFIG_HOME") or home / ".config")
        return cls(
            install_root=home / ".local" / "opt",
            # Deliberately NOT $XDG_DATA_HOME: Flatpak redirects that into the
            # sandbox, but menu entries must land in the real ~/.local/share.
            applications_dir=home / ".local" / "share" / "applications",
            config_file=config_home / "tarseer" / "apps.json",
        )


@dataclass
class ManagedApp:
    """An app installed and tracked by Tarseer."""

    id: str                          # slug, also the folder name in install_root
    name: str                        # display name
    repo: str                        # "owner/repo"
    asset_pattern: str               # regex matched against release file names
    installed_tag: str | None = None
    previous_tag: str | None = None  # tag of the .bak copy, used by rollback
    executable: str | None = None    # path relative to the install dir
    icon: str | None = None          # path relative to the install dir

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "ManagedApp":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})


@dataclass
class Asset:
    """A file attached to a GitHub release."""

    name: str
    url: str
    size: int = 0


@dataclass
class Release:
    tag: str
    name: str = ""
    html_url: str = ""
    assets: list[Asset] = field(default_factory=list)
