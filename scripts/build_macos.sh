#!/bin/bash
# Builds "dist/Inkwell PDF.app" and dist/InkwellPDF-<version>-macOS-<arch>.dmg on a Mac.
# Uses python3.12/3.13 if installed (best PyInstaller support), else python3.
set -euo pipefail
cd "$(dirname "$0")/.."
VERSION=$(sed -n 's/^__version__ = "\(.*\)"/\1/p' inkwellpdf/__init__.py)
ARCH=$(uname -m)
PY=$(command -v python3.13 || command -v python3.12 || command -v python3)
APP="dist/Inkwell PDF.app"
BIN="$APP/Contents/MacOS/Inkwell PDF"
echo "Building Inkwell PDF $VERSION for $ARCH with $($PY --version)"

rm -rf .build-venv build dist
"$PY" -m venv .build-venv
.build-venv/bin/pip install -q --upgrade pip
.build-venv/bin/pip install -q -r requirements.txt pyinstaller pillow

.build-venv/bin/pyinstaller --noconfirm --clean --windowed --name "Inkwell PDF" \
  --icon assets/icon.png \
  --osx-bundle-identifier io.github.inkwellpdf \
  --add-data "inkwellpdf/web:inkwellpdf/web" \
  --add-data "inkwellpdf/fonts:inkwellpdf/fonts" \
  --collect-submodules webview \
  --exclude-module pymupdf.layout --exclude-module pymupdf_layout --exclude-module pymupdf4llm \
  --exclude-module onnxruntime --exclude-module networkx --exclude-module tabulate --exclude-module numpy \
  run_inkwell.py

# fail the build if the packaged app can't open, edit and save a PDF
"$BIN" --self-test

# open the real window with the sample, edit, save, quit (warns only: CI may have no screen)
"$BIN" --probe "$PWD/docs/sample-invoice.pdf" > build/probe.log 2>&1 & PROBE=$!
for _ in $(seq 1 45); do sleep 1; kill -0 $PROBE 2>/dev/null || break; done
kill $PROBE 2>/dev/null || true
if grep -q "probe save: OK" build/probe.log; then echo "Window probe: OK"; else echo "WARNING: window probe didn't finish:"; tail -5 build/probe.log; fi

# app metadata Finder shows
/usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString $VERSION" "$APP/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Add :NSHumanReadableCopyright string 'Free software, GNU AGPL v3'" "$APP/Contents/Info.plist" || true
codesign --force --deep --sign - "$APP"   # ad-hoc signature (needed after editing Info.plist)

DMG="dist/InkwellPDF-${VERSION}-macOS-${ARCH}.dmg"
STAGE=$(mktemp -d)
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
hdiutil create -volname "Inkwell PDF" -srcfolder "$STAGE" -ov -format UDZO "$DMG" >/dev/null
rm -rf "$STAGE"
echo "Built $DMG"
