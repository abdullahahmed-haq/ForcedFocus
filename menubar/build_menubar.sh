#!/bin/bash
set -euo pipefail

APP_NAME="ForcedFocusBar"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
VERSION_FILE="$PROJECT_ROOT/VERSION"
PRODUCT_VERSION="$(tr -d '[:space:]' < "$VERSION_FILE")"
APP_DIR="$SCRIPT_DIR/$APP_NAME.app"
BIN_DIR="$APP_DIR/Contents/MacOS"
RES_DIR="$APP_DIR/Contents/Resources"
FONT_SOURCE_DIR="$PROJECT_ROOT/web/assets/fonts"
FONT_RES_DIR="$RES_DIR/Fonts"
PLIST="$APP_DIR/Contents/Info.plist"
SWIFT_SOURCE="$SCRIPT_DIR/forcefocus_menubar.swift"
SDK_PATH="$(xcrun --sdk macosx --show-sdk-path)"
BUILD_TMP="$(mktemp -d "${TMPDIR:-/tmp}/forcefocus-menubar.XXXXXX")"

trap 'rm -rf "$BUILD_TMP"' EXIT

echo "🔨 Building ForcedFocus Menu Bar App..."

# Create directory structure
mkdir -p "$BIN_DIR"
mkdir -p "$RES_DIR"
mkdir -p "$FONT_RES_DIR"

# Keep the native status item independent of user-installed fonts.
for font in "$FONT_SOURCE_DIR"/NaNSuperXSerifTextAR-TRIAL-*.ttf; do
    [[ -f "$font" ]] || continue
    cp "$font" "$FONT_RES_DIR/"
done

# Copy Icon
ICON_SRC="$PROJECT_ROOT/packaging/macos/assets/AppIcon.icns"
if [ -f "$ICON_SRC" ]; then
    cp "$ICON_SRC" "$RES_DIR/AppIcon.icns"
    echo "✅ Applied custom App Icon."
else
    echo "⚠️ No AppIcon.icns found in current directory. Using default."
fi

# Compile CSS
echo "🎨 Compiling Tailwind CSS..."
npm run --prefix "$PROJECT_ROOT/web" build:css

# Build each supported architecture explicitly, then merge them into the
# unsigned Universal executable. Signing/notarization remains a release step.
for arch in arm64 x86_64; do
    swiftc "$SWIFT_SOURCE" \
        -target "${arch}-apple-macos13.0" \
        -sdk "$SDK_PATH" \
        -framework AppKit \
        -framework WebKit \
        -o "$BUILD_TMP/$APP_NAME-$arch"
done
xcrun lipo -create \
    "$BUILD_TMP/$APP_NAME-arm64" \
    "$BUILD_TMP/$APP_NAME-x86_64" \
    -output "$BIN_DIR/$APP_NAME"
xcrun lipo -info "$BIN_DIR/$APP_NAME"

# Create Info.plist
cat <<EOF > "$PLIST"
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleIconFile</key>
    <string>AppIcon</string>
    <key>CFBundleExecutable</key>
    <string>$APP_NAME</string>
    <key>CFBundleIdentifier</key>
    <string>com.forcefocus.menubar</string>
    <key>CFBundleName</key>
    <string>$APP_NAME</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleShortVersionString</key>
    <string>$PRODUCT_VERSION</string>
    <key>CFBundleVersion</key>
    <string>$PRODUCT_VERSION</string>
    <key>LSUIElement</key>
    <true/>
    <key>NSHighResolutionCapable</key>
    <true/>
    <key>NSUserNotificationUsageDescription</key>
    <string>ForcedFocus needs to show notifications for session updates.</string>
    <key>NSAppTransportSecurity</key>
    <dict>
        <key>NSAllowsLocalNetworking</key>
        <true/>
    </dict>
</dict>
</plist>
EOF

echo "✅ Build complete: $APP_DIR"
echo "You can move it to /Applications or double-click to run!"
