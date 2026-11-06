# Build on macOS with: pyinstaller --noconfirm --clean WatchdogPerf-macOS.spec

from PyInstaller.utils.hooks import collect_data_files


datas = collect_data_files("matplotlib") + collect_data_files("watchdog_perf")

a = Analysis(
    ["watchdog_perf_app.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=[
        "watchdog_perf.gui",
        "matplotlib.backends.backend_agg",
        "matplotlib.backends.backend_pdf",
        "matplotlib.backends.backend_svg",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="WatchdogPerf",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="WatchdogPerf",
)

app = BUNDLE(
    coll,
    name="WatchdogPerf.app",
    bundle_identifier="com.watchdogperf.monitor",
    info_plist={
        "CFBundleDisplayName": "WatchdogPerf",
        "CFBundleName": "WatchdogPerf",
        "CFBundleShortVersionString": "0.2.3",
        "NSHighResolutionCapable": True,
        "LSMinimumSystemVersion": "11.0",
    },
)
