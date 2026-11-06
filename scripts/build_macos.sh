#!/usr/bin/env bash
set -euo pipefail

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "This build must run on macOS; PyInstaller cannot cross-build a .app."
  exit 2
fi

python3 -m venv .venv-build-macos
source .venv-build-macos/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements-build.txt
python -c "import matplotlib, psutil, tkinter; print('matplotlib', matplotlib.__version__); print('psutil', psutil.__version__); print('tk', tkinter.TkVersion)"
python -m unittest discover -s tests -v
python -m PyInstaller --noconfirm --clean WatchdogPerf-macOS.spec

dist/WatchdogPerf.app/Contents/MacOS/WatchdogPerf --version
find dist/WatchdogPerf.app \( -name "libpython*.dylib" -o -path "*/Python.framework/Versions/*/Python" \) -print -quit | grep .
find dist/WatchdogPerf.app -name "*_tkinter*" -print -quit | grep .
find dist/WatchdogPerf.app -iname "*psutil*" -print -quit | grep .
find dist/WatchdogPerf.app -path "*matplotlib*" -print -quit | grep .
find dist/WatchdogPerf.app -name "echarts.min.js" -print -quit | grep .
ARCH="$(uname -m)"
ditto -c -k --sequesterRsrc --keepParent \
  dist/WatchdogPerf.app "dist/WatchdogPerf-macOS-${ARCH}.zip"
echo "Created dist/WatchdogPerf-macOS-${ARCH}.zip"
