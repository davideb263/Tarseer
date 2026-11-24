#!/usr/bin/env bash
# Build a distributable single-file Flatpak bundle: Tarseer-<version>.flatpak
#
# Usage: tools/build-bundle.sh [--install]
#   --install  also install the freshly built app for the current user
#
# The version is read from meson.build. Output goes to the repo root.
set -euo pipefail

APP_ID=io.github.davideb263.Tarseer
cd "$(dirname "$0")/.."

VERSION=$(sed -nE "s/^\s*version:\s*'([^']+)'.*/\1/p" meson.build | head -n1)
[[ -n "$VERSION" ]] || { echo "Could not read version from meson.build" >&2; exit 1; }
BUNDLE="Tarseer-${VERSION}.flatpak"

INSTALL=()
[[ "${1:-}" == "--install" ]] && INSTALL=(--user --install)

echo "==> Building $APP_ID $VERSION"
flatpak-builder --force-clean --repo=repo "${INSTALL[@]}" build-dir "$APP_ID.json"

echo "==> Exporting bundle $BUNDLE"
flatpak build-bundle \
    --runtime-repo=https://dl.flathub.org/repo/flathub.flatpakrepo \
    repo "$BUNDLE" "$APP_ID"

echo "==> Done: $(pwd)/$BUNDLE ($(du -h "$BUNDLE" | cut -f1))"
