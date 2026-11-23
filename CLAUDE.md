# Tarseer

GNOME/libadwaita desktop app that installs and updates software distributed as
tarballs on GitHub Releases ("Gear Lever, but for tarballs"). It replaces the
bash script in `reference/tar-update` (kept only as a reference; do not modify).

## Stack
Python + PyGObject, GTK4 + libadwaita 1.10, Blueprint UI files, Meson, Flatpak
(GNOME 51 runtime, Python 3.14). App ID: `io.github.davideb263.Tarseer`.

## Commands
- Build + install + run:
  `flatpak-builder --user --install --force-clean build-dir io.github.davideb263.Tarseer.json && flatpak run io.github.davideb263.Tarseer`
- Unit tests (host, no GTK needed): `PYTHONPATH=src python3 -m unittest discover -s tests -t .`
- Dev sandbox: `TARSEER_DEV_ROOT=<dir>` redirects install root, menu entries and
  config into `<dir>` (never test against the real `~/.local/opt`). The Flatpak
  needs access: `flatpak run --filesystem=<dir> --env=TARSEER_DEV_ROOT=<dir> io.github.davideb263.Tarseer`
- Screenshots for checking UI changes: `tools/dev_screenshot.py` (usage in its
  docstring; supports `--check`, `--update <id>`, `--progress-shot`,
  `--add <repo> --add-shots <prefix>` to walk the Add App dialog). Read the PNG
  to verify layout instead of assuming it looks right.
- `meson`/`blueprint-compiler` are NOT on the host (Bluefin); they exist only inside
  `org.gnome.Sdk//51`. To run an SDK tool: `flatpak run --filesystem="$PWD" --command=<tool> org.gnome.Sdk//51 ...`

## Layout
- `src/tarseer/core/` — pure logic. **Must not import gi/GTK** so it stays testable
  on the host. `manager.AppManager` is the only entry point the UI should use; it
  wraps `github`, `assets`, `installer`, `desktop` and `config`. Host paths come
  from `models.Paths` (injected in tests — never write to the real home in tests).
- `src/tarseer/ui/` — widgets; each has a `.py` + `.blp`. New `.blp` files must be
  added to `src/meson.build` (blueprints list) and `src/tarseer.gresource.xml`.
- `bin/tarseer.in` — launcher; lives in `bin/` because a `src/tarseer` build
  output would clash with the Blueprint output directory.
- `data/` — .desktop, metainfo, gschema, icon.

## Conventions
- Blocking work (network, disk) runs in a worker thread; results go back to the
  UI with `GLib.idle_add`. Never block the GTK main loop.
- Managed apps install to `~/.local/opt/<id>/`, backup at `~/.local/opt/<id>.bak/`.
  Menu entries go to `~/.local/share/applications/tarseer-<id>.desktop`.
- The installed version is the release tag Tarseer recorded at install time;
  never run managed apps' binaries to detect versions (doesn't work in the sandbox).
- Any new host path the app touches needs a matching `--filesystem` in the manifest.
