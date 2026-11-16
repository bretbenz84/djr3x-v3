#!/bin/bash
# Build "Rex Call.app" into apps/telepresence/build/ (ignored): a release build of the RexCall
# target, an Info.plist with the microphone and local-network reasons macOS asks for, and an ad-hoc
# signature so the microphone permission sticks to the app. Run from anywhere:
#   apps/telepresence/build.sh            then open "apps/telepresence/build/Rex Call.app"
set -euo pipefail
cd "$(dirname "$0")"
swift build -c release --product RexCall
APP="build/Rex Call.app"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$(swift build -c release --show-bin-path)/RexCall" "$APP/Contents/MacOS/RexCall"
cp Resources/AppIcon.icns "$APP/Contents/Resources/AppIcon.icns"   # Redraw with icon/make_icon.py.
cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key><string>Rex Call</string>
    <key>CFBundleDisplayName</key><string>Rex Call</string>
    <key>CFBundleIdentifier</key><string>local.djr3x.rexcall</string>
    <key>CFBundleExecutable</key><string>RexCall</string>
    <key>CFBundleIconFile</key><string>AppIcon</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>CFBundleShortVersionString</key><string>0.1.0</string>
    <key>CFBundleVersion</key><string>1</string>
    <key>LSMinimumSystemVersion</key><string>14.0</string>
    <key>NSHighResolutionCapable</key><true/>
    <key>NSPrincipalClass</key><string>NSApplication</string>
    <key>NSMicrophoneUsageDescription</key><string>Your voice plays from Rex's speaker while you are unmuted.</string>
    <key>NSLocalNetworkUsageDescription</key><string>Rex Call connects to the robot on your home network.</string>
</dict>
</plist>
PLIST
codesign --force --sign - --timestamp=none "$APP"
echo "Built $(pwd)/$APP"
