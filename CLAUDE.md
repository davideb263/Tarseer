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
  It also gives GSettings its own keyfile (`<dir>/settings.ini`, group `[tarseer]`) and
  skips the Background portal (autostart is only logged; `TARSEER_DEV_DENY_BACKGROUND=1`
  acts like a refusal), so the real setup is never touched. That means dev runs never
  exercise the real portal calls: after changing `ui/portal.py`, test them with a
  small script run inside the Flatpak (`--command=python3`), then undo what it changed.
- Background checks: `tarseer --background [--check-now]`; `TARSEER_CHECK_INTERVAL=<s>`
  overrides the interval. A resident instance survives `timeout` on `flatpak run`:
  stop it with `flatpak kill io.github.davideb263.Tarseer` (and mind the 60/hour
  anonymous quota when testing short intervals).
  A dev `flatpak run` while the user's real Tarseer is running hands off to it
  (same app id) and silently does nothing: check `flatpak ps`, or run through a
  script that sets `Gio.ApplicationFlags.NON_UNIQUE` (as `dev_screenshot.py` does).
- Screenshots for checking UI changes: `tools/dev_screenshot.py` (usage in its
  docstring; supports `--check`, `--update <id>`, `--progress-shot`,
  `--add <repo> --add-shots <prefix>` to walk the Add App dialog, `--background-check`,
  `--preferences <png> [--pref-background on|off --pref-frequency <i>]`, `--details <id>`
  with `--details-action rollback|remove-confirm|remove`). Read the PNG
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
  Menu entries go to `~/.local/share/applications/<id>.desktop` (user's choice:
  clean names). Tarseer only ever modifies/deletes .desktop files containing
  `X-Tarseer-App=<id>`; a foreign file with the same name is a conflict reported
  to the user, never overwritten. Legacy `tarseer-<id>.desktop` files are
  migrated at startup.
- `TODO.md` is the backlog. Read it when planning work; when you finish an item,
  move it to **Done** with the commit hash; add ideas/issues you discover. Commit
  it together with the related change.
- Messages the user must act on go in an `Adw.AlertDialog`, not a toast
  (toasts truncate to one line).
- The installed version is the release tag Tarseer recorded at install time;
  never run managed apps' binaries to detect versions (doesn't work in the sandbox).
- Any new host path the app touches needs a matching `--filesystem` in the manifest.
