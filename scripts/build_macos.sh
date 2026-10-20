#!/bin/bash
# Builds dist/LocalPDF.app and dist/LocalPDF-<version>-macOS.dmg on a Mac.
set -euo pipefail
cd "$(dirname "$0")/.."
VERSION=$(python3 -c "import re;print(re.search(r'__version__ = \"(.+)\"', open('localpdf/__init__.py').read())[1])")

python3 -m venv .build-venv
.build-venv/bin/pip install -q --upgrade pip
.build-venv/bin/pip install -q -r requirements.txt pyinstaller

.build-venv/bin/pyinstaller --noconfirm --clean --windowed --name LocalPDF \
  --osx-bundle-identifier io.github.localpdf \
  --add-data "localpdf/web:localpdf/web" \
  --add-data "localpdf/fonts:localpdf/fonts" \
  --collect-submodules webview \
  run_localpdf.py

DMG="dist/LocalPDF-${VERSION}-macOS.dmg"
rm -f "$DMG"
STAGE=$(mktemp -d)
cp -R dist/LocalPDF.app "$STAGE/"
ln -s /Applications "$STAGE/Applications"
hdiutil create -volname "LocalPDF" -srcfolder "$STAGE" -ov -format UDZO "$DMG"
echo "Built $DMG"
