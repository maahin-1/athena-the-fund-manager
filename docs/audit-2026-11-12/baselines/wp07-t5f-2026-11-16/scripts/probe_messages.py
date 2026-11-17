"""Print DuckDB's real lock and corruption messages for synthetic files in a temp dir (names include clock/block/lock). No real stores touched."""
import subprocess, sys, tempfile, time
from pathlib import Path
import duckdb

d = Path(tempfile.mkdtemp(prefix="athena-probe-")) / "clockblock-lock"
d.mkdir(parents=True)
src = d / "clock.duckdb"
c = duckdb.connect(str(src)); c.execute("CREATE TABLE bars (symbol VARCHAR, day DATE, close DOUBLE)"); c.close()
h = subprocess.Popen([sys.executable, "-c", f"import duckdb,time; c=duckdb.connect(r'{src}'); c.execute('SELECT 1'); print('held', flush=True); time.sleep(20)"], stdout=subprocess.PIPE, text=True)
assert h.stdout.readline().strip() == "held"
try:
    duckdb.connect(str(src), read_only=True)
except duckdb.IOException as e:
    print("LOCK:", type(e).__module__, type(e).__name__, "|", str(e))
h.kill(); time.sleep(0.3)
bad = d / "block.duckdb"
bad.write_bytes(b"this is not a database " * 100)
try:
    duckdb.connect(str(bad), read_only=True)
except Exception as e:
    print("CORRUPT:", type(e).__name__, "|", str(e))
try:
    duckdb.connect(str(d / "nothere" / "lock.duckdb"), read_only=True)
except Exception as e:
    print("MISSING:", type(e).__name__, "|", str(e))
class X(duckdb.IOException):
    def __init__(self, m):
        super().__init__(m)
        self.extra = 1
x = X("hello")
print("subclass ok:", isinstance(x, duckdb.IOException), str(x), x.extra)
