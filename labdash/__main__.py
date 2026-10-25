import sys
from .cli import main
from .server import serve
from pathlib import Path

if len(sys.argv) == 1:
    # bare `python -m labdash` serves the dashboard, as before
    cfg = Path("dashboard.json")
    serve(cfg if cfg.exists() else None, 8787, True)
else:
    sys.exit(main())
