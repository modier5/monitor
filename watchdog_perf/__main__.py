try:
    from .cli import main
except ImportError:
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from watchdog_perf.cli import main

raise SystemExit(main())
