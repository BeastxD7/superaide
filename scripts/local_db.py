"""Start a local Postgres with no Docker, using the pgserver package.

Run with Python 3.12 (pgserver ships wheels up to 3.12):
    uv run --no-project --python 3.12 --with pgserver python scripts/local_db.py <data dir>
Prints a connection URI. The server keeps running after this script exits;
stop it with: scripts/local_db.py <data dir> --stop
"""

import sys
from pathlib import Path

import pgserver

data_dir = sys.argv[1]
if "--stop" in sys.argv:
    pgserver.pg_ctl(["-m", "fast", "stop"], pgdata=Path(data_dir))
    print("stopped")
else:
    server = pgserver.get_server(data_dir, cleanup_mode=None)
    print(server.get_uri())
