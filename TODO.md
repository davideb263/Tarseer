# Tarseer TODO

Ideas and known issues for after v1. Keep this file current: move items to
**Done** (with the commit) when finished, and add new ideas or issues
found while working.

## Next up
Nothing planned yet. Flathub is on hold (Davide decides later; see the
Flathub item below). FlatPark is being explored as an alternative: first step
is asking its maintainer (see the FlatPark item below).

## Features
- [ ] **Multiple app grid entries per app**, e.g. Calibre's E-book Viewer and
      Editor alongside the main program.
- [ ] **`StartupWMClass` detection/setting**, so running apps get the right icon
      in the dash instead of a generic or duplicate one (especially Java apps
      like Sparrow).
- [ ] **Pre-release channel** per app (use `/releases` instead of `/releases/latest`).
- [ ] **Adopt an existing install** (point at a folder + tell Tarseer its version)
      instead of reinstalling.
- [ ] **More languages** beyond Italian (Claude can draft them; ideally a native
      speaker reviews before release). See README "Translations".
- [ ] **Flathub submission (on hold since 2026-11-25).** Done so far: screenshots (`data/screenshots/`, URLs
      pinned to the `v1.0.0` tag, so they resolve only once it is pushed), store
      description, branding colors, permissions explained in README; linter clean
      except screenshot reachability. Flathub's Generative AI policy (checked
      2026-11-25, docs.flathub.org requirements) applies: AI-generated code must
      be disclosed and is accepted at reviewer discretion (may be rejected without
      review); the Flathub manifest must contain no AI-generated or AI-assisted
      content; AI must not open the PR or write its description, commits or
      replies. So Davide writes the Flathub manifest and the PR himself; Claude
      doesn't draft them.
- [ ] **FlatPark listing (alternative to Flathub, evaluated 2026-11-25).**
      flatpark.org is a community Flatpak remote (preinstalled in Bluefin's Bazaar,
      filtered by a small denylist only) that accepts AI-assisted apps. Feasible on
      paper: GNOME 51 is its current runtime, and Cellar (org.flatpark.Cellar) is a
      precedent for runtime downloads + `xdg-data/applications:create`. Open points:
      1. [ ] Ask the maintainer first (issue on github.com/flatpark/flatpark) whether
         an app installing GitHub tarballs into `~/.local/opt` is listable: their
         runbook wants writes in the app's own data dir, and "apps that manage the
         host" are borderline.
      2. [ ] Packaging model is extra-data: FlatPark's package downloads a release
         asset (sha256-pinned) and unpacks it in `apply_extra.sh`; it doesn't build
         from source. Needs a CI-built release tarball of the built `/app` tree
         (compiled Blueprint/gresource/gschema; check GSettings schema lookup under
         `/app/extra`). Desktop file, metainfo and icon live in the package itself.
      3. [ ] Their files: `registry/<id>/flatpark.yml`, `<id>.yml`, `apply_extra.sh`,
         `.desktop`, `.metainfo.xml`, icon, `resolve-update.sh`; PR branch
         `add/<app-id>`. Optional `flatpark/publish-action@v1` for instant updates.
      Trust note: a single maintainer (@jing2uo) merges and owns the signing
      infrastructure; the wrapper (manifest, `apply_extra.sh`, finish-args) is
      theirs, so review changes to `registry/io.github.davideb263.Tarseer/`.
- [ ] **Custom app Icon picking** Let the user pick a custom icon for the .desktop file.
      Put an option to pick a custom icon near or in the already existing dropdown where
      you choose between different icons in the app folder.

## Known issues / polish
- [ ] Not tested: background checks with automatic login, when the keyring is
      still locked. The keyring may ask to be unlocked; the check goes on without
      the token after 30 s.
- [ ] Icon dropdown shows long paths truncated (`resources/images/calib…`);
      consider showing the file name with the folder as subtitle.
- [ ] "Check for updates" errors are still toasts (one alert per app would
      pile up), so long ones get cut off; the full text is only in the row's ⚠
      tooltip. Consider an "error details" action. (Rate limit and rejected
      token errors are already alerts, shown once per check.)

- [ ] **Updates for Tarseer itself: self-hosted Flatpak repo on GitHub Pages**
      (planned 2026-11-25). Safest distribution option: only Davide builds and
      signs; `flatpak update` picks up new releases. Doesn't rule out FlatPark/Flathub
      later (each install updates from the remote it came from).
      1. [x] Signing key made by Davide (fingerprint `C751…E9FEC5A1`, backup in his
         password manager), public half in `data/flatpak/tarseer.gpg`; secret
         `GPG_PRIVATE_KEY` in the `github-pages` environment (only `v*` tags and
         `main`); Pages enabled with Actions as source.
      2. [x] Workflow `.github/workflows/flatpak-repo.yml` (release + manual run):
         tests and version check, signed build in the GNOME 51 CI container, branch
         `master`, fresh repo each run, deployed to Pages. Tested locally in the same
         image with a throwaway key: signature checked (wrong key refused), install,
         and `flatpak update` across a rebuilt-from-scratch repo all work.
      3. [x] Bundle built with `--repo-url` + `--gpg-keys` and attached to the release
         by a separate job (the only one with `contents: write`); a bundle install
         gets a `tarseer-origin` remote pointing at the repository.
      4. [x] `data/flatpak/`: `.flatpakrepo`, `.flatpakref`, landing `index.html`;
         README "Installing" rewritten (migration: `remote-add` + `install --reinstall tarseer <id>`; `--reinstall` with the .flatpakref refuses an installed app). Davide's install switched 2026-11-25.
      5. [ ] Verify end to end: add the remote, install, then a later release shows
         up in `flatpak update` (and check whether Bazaar lists it).

## Other desktops (low priority)
Tarseer targets GNOME; see README "Known limitations". If support is broadened:
- [ ] **Test on KDE Plasma and a tiling WM** (e.g. Sway) in a VM or second session;
      fix what breaks.
- [ ] **Autostart fallback** when the Background portal is missing or refuses:
      offer to write `~/.config/autostart/<app id>.desktop` directly (needs
      `--filesystem=xdg-config/autostart:create`).
- [ ] **Token fallback** when no Secret Service is available: offer to keep the
      token in a file only the user can read, with a warning.
- [ ] **GNOME-specific wording** ("app grid", …) in labels: make it neutral.

## Done
- [x] v1.0.1: new app icon without the empty margin, 128×128 (`1e8aecc`).
- [x] Fixes from the 2026-11-25 code and security reviews of `main..dev`:
      English throughout when the system locale is missing (no half-translated
      window), remaining untranslated errors and the menu entry comment,
      whole HTTP error messages instead of combined fragments, no `!r`
      placeholders, no `_` throwaway names (`4d3ae23`).
- [x] v1.0.0: translations (Italian first), store metadata and screenshots,
      install instructions in README; Flathub left for later (`05d8350`).
- [x] **Translations:** `po/` + Meson i18n, core messages translatable
      (`core/i18n.py`), Italian translation (208 strings). Verified in the
      Flatpak with `LANG=it_IT.UTF-8`: main window, Add App, details, remove
      alert, Preferences and a core error message (`e0c2f49`).
- [x] v0.3.0 "Updates come to you" (plan agreed 2026-11-24):
      1. [x] **Check logic in core.** `AppManager.check_updates()` returns a release or
             error per app (4 lookups at a time), stops early on rate limit / rejected
             token, and caches the results; rows and the window read from the cache (`850bcb0`).
      2. [x] **Use the rate limit carefully.** ETag + `If-None-Match` per repo, last
             replies cached in `$XDG_CACHE_HOME/tarseer/releases.json` (pruned to managed
             repos at startup); `AppManager.has_quota_for()` (every app counted as a
             request + 10 spare) for background runs to call first. Without a token a 304
             still costs a request (verified live); with a token it's free (verified
             manually with a real token: the quota in Preferences stays the same) (`d6654c6`).
      3. [x] **Background service** (`ui/background.py`). `tarseer --background`: no window,
             first check 2 min after login (`--check-now`: right away); a second instance
             hands off and exits. `check-interval-hours` 0 = at login only (check, retry
             up to 3× every 5 min if offline, exit), otherwise resident: `hold()` and a
             15-min tick that checks when due by wall clock (`core/schedule.py`, so
             suspend counts). A normal launch also starts the resident service if
             enabled; closing the window keeps it running, Ctrl+Q quits. Skips if the
             quota is low or the window is busy; waits up to 30 s for the keyring, then
             goes on without the token; errors only logged. The app now owns the manager
             and token loading; an open window refreshes after a background check.
             `ui/portal.py` wraps the Background portal (autostart + status); calling it
             to register autostart is step 5 (`7c6a19e`).
      4. [x] **Notifications.** One `Gio.Notification` per background run ("6 Updates
             Available" / "Alpha, Bravo, Charlie, Delta and 2 more"), not while the window
             has focus. Only for tags not announced before (`notified-tags`; updates seen
             in a window check count as announced; `core.schedule.notification_state`).
             Withdrawn when a check finds no updates, or once every announced app has a
             result and none has an update left. Clicking runs `app.show-updates`: opens
             the window and checks if this process has no results. `DBusActivatable=true`
             + D-Bus service file, so the click works after a login-only run exited.
             Verified with faked GitHub + `dbus-monitor` (add / quiet / add / remove) (`dd30b6d`).
      5. [x] **Preferences "Updates" group.** Switch "Check in the Background" (opt-in;
             asks the Background portal for autostart, a refusal or error is an alert and
             the switch goes back off), frequency dropdown (at login only / 6 h / 12 h /
             once a day → `check-interval-hours`), "Last Checked" (live). Changes call
             `BackgroundService.restart()`. Successful window checks also set `last-check`,
             so the background doesn't repeat them. Verified in a dev sandbox
             (on/off/frequency/refusal via `TARSEER_DEV_DENY_BACKGROUND`). The first real
             test failed (libportal from Python: callback got an extra argument, and the
             autostart Exec lost its command line); the portal is now called over D-Bus
             and was verified for real (correct `Exec=… --command=tarseer … --background`,
             removed again on disable) (`d09f95f`). Verified manually: the switch stays on, Tarseer shows in Quick Settings'
             background apps with a periodic frequency, and "At login only" exits after its check (`6c8002b`).
      6. [x] **Dev/test.** Done: `TARSEER_CHECK_INTERVAL=<s>`, `--check-now`, dev settings
             keyfile, `dev_screenshot.py --background-check` and `--pref-background`, real
             portal on/off, Quick Settings entry, login-only run, a real notification and
             clicking it both with Tarseer running and after it exited (D-Bus activation)
             (manual, 2026-11-24, with a fake-GitHub dev sandbox). Locked keyring / auto-login
             not tested: see Known issues.
      7. [x] **Small extras.**
         - [x] Arch names: `riscv64gc` (any `riscv64*`) and Rust-style `powerpc64`,
               `powerpc64le`, `loongarch64`/`loong64`, MIPS are known, so they're no longer
               offered on other machines (ripgrep offered riscv64gc and powerpc64 on x86_64) (`0b9845c`).
         - [x] "Reinstall now": after changing the release file while the installed tag
               is the latest, an alert offers Reinstall / Later. Reinstall runs as a row
               update (progress, cancel, backup kept for rollback) and closes the details
               dialog. Verified with ripgrep 16.0.0 gnu → musl (static binary installed,
               gnu kept in `.bak`) and Later (pattern saved, files untouched) (`e13b523`).
      8. [x] **Release 0.3.0.** Version bump, metainfo notes, README, merge to `main`, tag (`5a9c5ec`).
- [x] Fixes from the 2026-11-24 review of `dev`: a check or update finishing
      after its window closed no longer crashes (the window keeps its
      application) and still records `last-check`/`notified-tags`; a background
      check no longer stores a release from an app's old repository after
      `set_repo`; a fixed `apps.json` is reread on the next activation, and no
      resident service runs without a config; launcher warnings from startup show
      once per run, not in every reopened window; armv6l is its own architecture
      (armv6/arm/armel), and on armv7l those builds rank below armv7 ones;
      reinstalling the same tag keeps the older backup; the periodic service
      stops if the interval is set to 0 (or checks turned off) outside
      Preferences; the release cache is read and pruned on the first lookup, in
      a worker thread, not at startup (`4e8e513`).
- [x] v0.2.1: review and security review fixes; version bump and metainfo
      release notes (`abcf182`).
- [x] Fixes from the 2026-11-24 security review: symlinks escaping the install
      dir after a single top-level folder is flattened are refused; file names
      with control characters are never offered or written to a menu entry;
      HTTPS-only opener that drops `Authorization` on cross-host redirects;
      downloads checked against the asset's `size` and `sha256` digest; app ids
      validated on load (an empty id made Remove delete all of `~/.local/opt`);
      extraction stops before leaving less than 512 MiB free (`ac86e90`).
- [x] Fixes from the v0.1.0..v0.2.0 review: `selectable_assets` test was never
      collected; rate limit / rejected token errors from a check are one alert,
      not a toast per app; rows no longer stay "Queued" after a details-dialog
      operation; a late keyring read no longer overwrites a token set in
      Preferences (Preferences refreshes and checks wait for it); 401 blamed on
      the token only when one was sent; cancel hidden at extraction even without
      Content-Length; release file / repo picks dropped because the app got busy
      now say so (`7d04e30`).
- [x] v0.2.0 "Update all, safely": re-pick release file, Update All, cancel
      downloads, GitHub token; version bump and metainfo release notes (`e82bbb8`).
- [x] Optional GitHub token in Preferences, validated against GitHub, stored
      with libsecret (Secret portal), sent only to api.github.com (`a312cd0`).
      Saving a real token was tested with a faked GitHub reply only; test
      manually with a real token.
- [x] Cancel a running download: ✕ next to a row's progress bar, Cancel on the
      Add App progress page, and Update All turns into Stop while it runs.
      Extraction can't be cancelled, so the buttons hide once the download ends (`e76ea68`).
- [x] "Update All" in the header bar (2+ updates): one app at a time, queued rows
      marked, failures collected into one alert at the end (`a047284`).
- [x] Re-pick the release file from the details dialog ("Change…"), and pick it
      again when a new repository names its files differently (`daff575`).
      Cancel and confirm paths on repository change verified manually.
- [x] Homepage, bug tracker and source URLs in the metainfo and About dialog (`d83cfa4`).
- [x] Real app icon in `data/icons/` (`a401572` first draft; vector SVG in `4df7f2e`).
- [x] v1: scaffold, core logic, main window, Add App dialog, details dialog
      (`f471394` … `ac25af6`).
- [x] Menu entries named `<id>.desktop` with conflict detection and migration of
      `tarseer-<id>.desktop` (`a088463`).
- [x] Code review of v1 (`/code-review src/`, 2026-11-23): asset name
      tokenizing, version wildcard in patterns, repo change keeping the old
      release, half-registered installs, rollback launcher paths, rename
      recovery in install/rollback, disk work off the main loop, error alerts,
      `%%` in Exec (`8eb7670` … `40aa0af`).
- [x] The "Open Folder" button has not been tested by Claude (it would open a
      window on the user's desktop); verified manually: Test OK
