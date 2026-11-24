# Tarseer

**Install and update tarball apps from GitHub Releases.**

Some Linux apps ship only as a `.tar.gz`/`.tar.xz` on their GitHub Releases
page: no Flatpak, no AppImage, no distro package. Installing them by hand means
downloading the archive, extracting it somewhere, writing a `.desktop` file so
the app shows up in the app grid, and doing it all over again for every new
version.

Tarseer does this for you. It's a GNOME app (GTK4 + libadwaita), similar to
[Gear Lever](https://github.com/mijorus/gearlever) but for tarballs.

### About the name

*Tarseer* is a play on the **tarsier**, a small primate with huge eyes, and
**seer**, someone who sees what others miss. With eyes like that, it spots new
releases of your tarballs as soon as they come out.

> **Status:** early development (0.1.0). It works, but it isn't on Flathub
> yet. See [TODO.md](TODO.md) for what's planned.

## Features

- **Add an app from its repository.** Paste `owner/name` or a GitHub URL.
  Tarseer gets the latest release and picks the right tarball for your CPU
  architecture. It skips Windows/macOS builds, source archives and debug
  symbols. You can pick a different file if the guess is wrong.
- **App grid entry.** Tarseer finds the program and an icon inside the
  extracted files and writes a menu entry for it. You can change the program,
  icon and name later.
- **Updates.** *Check for Updates* (<kbd>Ctrl</kbd>+<kbd>R</kbd> /
  <kbd>F5</kbd>) asks GitHub for each app's latest release. *Update* downloads
  the release file, matched by a pattern Tarseer learned from the file you
  first picked (e.g. `calibre-7.2.0-x86_64.txz` matches next month's
  `calibre-7.3.0-x86_64.txz`).
- **Rollback.** The previous version is kept as a backup. *Restore* swaps
  back to it, and you can swap again afterwards.
- **Safe on disk.** Archives are extracted to a staging folder and moved
  into place in one step, so a failed or interrupted update leaves the
  current version alone. Archive entries that would write outside the install
  folder (absolute paths, `..`, links pointing outside, device files) are
  refused.
- **Doesn't touch other files.** Tarseer only edits or deletes menu entries it
  created itself. If a `.desktop` file with the same name already exists,
  Tarseer tells you about the conflict and doesn't overwrite it.

Supported formats: `.tar.gz`/`.tgz`, `.tar.xz`/`.txz`, `.tar.bz2`/`.tbz2`,
`.tar.zst`/`.tzst` and plain `.tar`.

## Where things go

| What | Location |
| --- | --- |
| Installed app | `~/.local/opt/<id>/` |
| Previous version (for rollback) | `~/.local/opt/<id>.bak/` |
| Menu entry | `~/.local/share/applications/<id>.desktop` |
| List of managed apps | `~/.var/app/io.github.davideb263.Tarseer/config/tarseer/apps.json` |

The installed version is the release tag Tarseer recorded when it installed
the app. Tarseer never runs the apps it manages to find out their version.

## Building

Tarseer is built as a Flatpak against the GNOME 51 runtime:

```sh
flatpak install flathub org.gnome.Platform//51 org.gnome.Sdk//51
flatpak-builder --user --install --force-clean build-dir io.github.davideb263.Tarseer.json
flatpak run io.github.davideb263.Tarseer
```

The Flatpak has network access plus write access to `~/.local/opt` and
`~/.local/share/applications`, and nothing else on the host filesystem.

## Development

**Stack:** Python 3.14, PyGObject, GTK4, libadwaita 1.10, Blueprint, Meson.

```
src/tarseer/core/   logic only, no GTK: GitHub API, file picking, install, menu entries
src/tarseer/ui/     windows and dialogs (.py + .blp)
data/               .desktop, metainfo, GSettings schema, icon
tests/              unit tests for core/
tools/              dev helpers (e.g. dev_screenshot.py)
```

The code in `core/` doesn't import GTK, so the tests run on the host without
the SDK:

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -t .
```

To try the app without touching your real `~/.local`, set `TARSEER_DEV_ROOT`
to a throwaway folder. Tarseer then keeps installs, menu entries and its config
inside that folder:

```sh
DEV=$HOME/tarseer-dev
mkdir -p "$DEV"
flatpak run --filesystem="$DEV" --env=TARSEER_DEV_ROOT="$DEV" io.github.davideb263.Tarseer
```

## License

[GPL-3.0-or-later](LICENSE)
