#!/bin/zsh
set -eu
PROJECT_ROOT="${0:A:h:h}"
APP_DIR="$PROJECT_ROOT/build/Degent.app"
PYTHON_BIN="$PROJECT_ROOT/.venv/bin/python"
if [[ ! -x "$PYTHON_BIN" ]]; then
  python3 -m venv "$PROJECT_ROOT/.venv"
fi
"$PYTHON_BIN" -c "import bs4" || { print "Install project dependencies: .venv/bin/python -m pip install -e ."; exit 1; }
mkdir -p "$APP_DIR/Contents/MacOS" "$APP_DIR/Contents/Resources" /tmp/degent-swift-module-cache
swiftc "$PROJECT_ROOT/desktop/Degent.swift" -o "$APP_DIR/Contents/MacOS/Degent" \
  -target "$(uname -m)-apple-macosx12.0" \
  -framework AppKit -framework WebKit -module-cache-path /tmp/degent-swift-module-cache
"$PYTHON_BIN" - "$APP_DIR" "$PROJECT_ROOT" "$PYTHON_BIN" <<'PY'
import os, plistlib, sys
from pathlib import Path
app, root, python = sys.argv[1:]
info = {
    'CFBundleName': 'Degent', 'CFBundleDisplayName': 'Degent', 'CFBundleIdentifier': 'local.degent.research',
    'CFBundleVersion': '2', 'CFBundleShortVersionString': '0.2.0', 'CFBundlePackageType': 'APPL',
    'CFBundleExecutable': 'Degent', 'LSMinimumSystemVersion': '12.0', 'NSHighResolutionCapable': True,
    'DegentProjectRoot': root, 'DegentPython': python,
    'DegentCommandPath': os.environ.get('PATH', '/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin'),
    'NSAppTransportSecurity': {'NSAllowsLocalNetworking': True},
}
with (Path(app) / 'Contents/Info.plist').open('wb') as stream:
    plistlib.dump(info, stream)
PY
codesign --force --sign - "$APP_DIR"
print "Built $APP_DIR"
