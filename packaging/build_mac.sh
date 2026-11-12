#!/bin/bash
# The Mac client deliverable: the suite, then PyInstaller, then a .pkg installer.
# Like build.ps1 it refuses to build a release from a red suite, or from a
# bundle that fails its own checks. Output: dist/FacebookAutoPoster-Setup-<v>.pkg
#
#   packaging/build_mac.sh                   everything
#   packaging/build_mac.sh --clean           start from an empty build folder
#   packaging/build_mac.sh --skip-installer  the .app only
#   packaging/build_mac.sh --force           package a failing build anyway,
#                                            for looking at it, never for a client
#
# The installer shows the account blocking policy (policy.rtf) and will not
# install until it is accepted, as the Windows one does. It is not signed: a
# client's Mac calls it from an unidentified developer until it is signed and
# notarised with an Apple Developer ID.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PY="$ROOT/.venv/bin/python"
BUILD="$ROOT/build/mac"
DIST="$ROOT/dist"
APP_NAME="FacebookAutoPoster"
BUNDLE_ID="com.fbautomation.autoposter"

CLEAN=0; SKIP_INSTALLER=0; FORCE=0
for arg in "$@"; do
  case "$arg" in
    --clean) CLEAN=1 ;;
    --skip-installer) SKIP_INSTALLER=1 ;;
    --force) FORCE=1 ;;
    *) echo "Unknown option: $arg" >&2; exit 2 ;;
  esac
done

fail() {
  echo "[fail] $1" >&2
  if [ "$FORCE" -eq 1 ]; then
    echo "       --force: carrying on anyway. Never hand this build to a client." >&2
  else
    exit 1
  fi
}

[ "$(uname)" = "Darwin" ] || { echo "This builds the Mac app; run it on a Mac." >&2; exit 2; }
[ -x "$PY" ] || { echo "No .venv here. Make one first (see CLAUDE.md, Commands)." >&2; exit 2; }

# The release number is the Windows installer's, so both builds share one.
VERSION="$(sed -n 's/^#define AppVersion "\(.*\)"/\1/p' packaging/installer.iss)"
[ -n "$VERSION" ] || { echo "Could not read AppVersion from installer.iss" >&2; exit 2; }
ARCH="$(uname -m)"
echo "== Facebook Auto-Poster $VERSION for macOS ($ARCH)"

if [ "$CLEAN" -eq 1 ]; then rm -rf "$BUILD"; fi
mkdir -p "$BUILD" "$DIST"

echo "== 1/4 The test suite"
"$PY" -m pytest tests/ -q -n auto -p no:cacheprovider || fail "The suite is red. No release from a red suite."

echo "== 2/4 The icon"
ICONSET="$BUILD/app.iconset"
rm -rf "$ICONSET"; mkdir -p "$ICONSET"
for size in 16 32 64 128 256; do
  sips -z "$size" "$size" packaging/app.png --out "$ICONSET/icon_${size}x${size}.png" >/dev/null
done
cp "$ICONSET/icon_32x32.png" "$ICONSET/icon_16x16@2x.png"
cp "$ICONSET/icon_64x64.png" "$ICONSET/icon_32x32@2x.png"
cp "$ICONSET/icon_256x256.png" "$ICONSET/icon_128x128@2x.png"
rm "$ICONSET/icon_64x64.png"
iconutil -c icns "$ICONSET" -o "$BUILD/app.icns"

echo "== 3/4 The app"
FBPOSTER_ICNS="$BUILD/app.icns" FBPOSTER_VERSION="$VERSION" \
  "$PY" -m PyInstaller packaging/fbposter.spec --noconfirm \
  --distpath "$BUILD/dist" --workpath "$BUILD/work"
APP="$BUILD/dist/$APP_NAME.app"
[ -d "$APP" ] || { echo "PyInstaller made no $APP_NAME.app" >&2; exit 1; }

# The things a frozen build loses without a word (packaging/CLAUDE.md).
INSIDE="$APP/Contents"
find "$INSIDE" -path "*playwright/driver/node" -type f | grep -q . || fail "Playwright's node is missing."
find "$INSIDE" -type d -name tzdata | grep -q . || fail "tzdata is missing."
find "$INSIDE" -name check.svg | grep -q . || fail "qtui/assets/check.svg is missing."
find "$INSIDE" -name Bellefair-Regular.ttf | grep -q . || fail "The title font is missing."
find "$INSIDE" -name "chevron-up-light.svg" | grep -q . || fail "The arrow icons are missing."

echo "   --selftest"
"$INSIDE/MacOS/$APP_NAME" --selftest --quiet || fail "The app's own --selftest failed."

if [ "$SKIP_INSTALLER" -eq 1 ]; then
  echo "== Done (no installer): $APP"
  exit 0
fi

echo "== 4/4 The installer"
STAGE="$BUILD/stage"
RESOURCES="$BUILD/resources"
rm -rf "$STAGE" "$RESOURCES"; mkdir -p "$STAGE" "$RESOURCES"
ditto "$APP" "$STAGE/$APP_NAME.app"
cp packaging/policy.rtf "$RESOURCES/policy.rtf"

# Installed in /Applications, always: a "relocatable" package would instead
# update whatever copy of the app macOS finds elsewhere on the disk.
COMPONENTS="$BUILD/components.plist"
pkgbuild --analyze --root "$STAGE" "$COMPONENTS" >/dev/null
plutil -replace 0.BundleIsRelocatable -bool NO "$COMPONENTS"
pkgbuild --root "$STAGE" --component-plist "$COMPONENTS" \
  --install-location /Applications --identifier "$BUNDLE_ID" --version "$VERSION" \
  "$BUILD/app.pkg" >/dev/null

cat > "$BUILD/distribution.xml" <<XML
<?xml version="1.0" encoding="utf-8"?>
<installer-gui-script minSpecVersion="2">
    <title>Facebook Auto-Poster</title>
    <license file="policy.rtf"/>
    <options customize="never" require-scripts="false" hostArchitectures="$ARCH"/>
    <domains enable_localSystem="true"/>
    <choices-outline>
        <line choice="default"><line choice="$BUNDLE_ID"/></line>
    </choices-outline>
    <choice id="default"/>
    <choice id="$BUNDLE_ID" visible="false">
        <pkg-ref id="$BUNDLE_ID"/>
    </choice>
    <pkg-ref id="$BUNDLE_ID" version="$VERSION" onConclusion="none">app.pkg</pkg-ref>
</installer-gui-script>
XML

OUT="$DIST/$APP_NAME-Setup-$VERSION-mac-$ARCH.pkg"
productbuild --distribution "$BUILD/distribution.xml" --resources "$RESOURCES" \
  --package-path "$BUILD" "$OUT" >/dev/null
echo "== Done: $OUT"
