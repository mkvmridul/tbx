"""finassist/db.py — a thin sqlite3-shaped wrapper around PyMySQL.

The engine was written against `sqlite3.Connection`. Rather than rewrite every
`.execute(sql, [...])` site, this module exposes a Connection whose surface
matches what the rest of finassist uses:

    cx = connect()
    cx.execute("SELECT ... WHERE x=?", [v]).fetchone()
    cx.executemany("INSERT ... VALUES (?,?)", rows)
    cx.executescript("CREATE TABLE ...; CREATE INDEX ...;")

Rows behave like sqlite3.Row — both `row["col"]` and `row[0]` work, and
`tuple(row)` gives positional values (needed by intents.many).

`?` param markers are translated to `%s` at execute time. The rest of the code
never needs to know we swapped drivers.
"""
from __future__ import annotations

import os
import re
import threading
from decimal import Decimal
from pathlib import Path

import pymysql
import pymysql.cursors

_ROOT = Path(__file__).resolve().parent.parent


def _load_env() -> None:
    """Read .env into os.environ (values already set take precedence)."""
    p = _ROOT / ".env"
    if not p.exists():
        return
    for raw in p.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip().strip('"').strip("'")
        os.environ.setdefault(k, v)


_load_env()


def _from_url(url: str) -> dict | None:
    m = re.match(r"mysql://([^:@]+)(?::([^@]*))?@([^:/]+)(?::(\d+))?/([^?]+)", url or "")
    if not m:
        return None
    u, pw, h, port, db = m.groups()
    return dict(host=h, port=int(port or 3306), user=u, password=pw or "", database=db,
                ssl_disabled=True)


def dsn() -> dict:
    """Connection kwargs for pymysql (the WRITABLE store the engine reads). Reads
    FINASSIST_DB (mysql://...) if set, otherwise the MYSQL_* variables. SSL is disabled:
    both the local and the hackathon servers present self-signed certificates."""
    d = _from_url(os.environ.get("FINASSIST_DB", ""))
    if d:
        return d
    return dict(
        host=os.environ.get("MYSQL_HOST", "127.0.0.1"),
        port=int(os.environ.get("MYSQL_PORT", "3306")),
        user=os.environ.get("MYSQL_USER", "root"),
        password=os.environ.get("MYSQL_PASSWORD", ""),
        database=os.environ.get("MYSQL_DB", "finance"),
        ssl_disabled=True,
    )


def source_dsn() -> dict:
    """Where enrichment READS the raw bank/account/transaction tables. Defaults to dsn().
    Set FINASSIST_SOURCE_DB when the raw data lives in a read-only database (the hackathon
    server grants SELECT only), so derived tables are written to dsn() instead."""
    return _from_url(os.environ.get("FINASSIST_SOURCE_DB", "")) or dsn()


def dsn_label() -> str:
    d = dsn()
    return f"mysql://{d['user']}@{d['host']}:{d['port']}/{d['database']}"


# --------------------------------------------------------------------------
# Row: dict-like with positional access, matching sqlite3.Row semantics
# --------------------------------------------------------------------------

def _norm(v):
    """Normalise DB values so JSON serialisation and % formatting behave.

    Decimals lose their exact-precision guarantee here; that is intentional --
    every downstream user rounds to 2 dp for money already, and JSON has no
    Decimal type, so leaving them in would surface as quoted strings in the UI.
    """
    if isinstance(v, Decimal):
        return float(v)
    return v


class Row(dict):
    __slots__ = ("_values",)

    def __init__(self, cols, values):
        vals = tuple(_norm(x) for x in values)
        super().__init__(zip(cols, vals))
        self._values = vals

    def __getitem__(self, k):
        if isinstance(k, int):
            return self._values[k]
        return super().__getitem__(k)

    def __iter__(self):
        # sqlite3.Row iterates VALUES, not keys. Match that so tuple(row) works.
        return iter(self._values)


# --------------------------------------------------------------------------
# Cursor / Connection wrappers
# --------------------------------------------------------------------------

_PARAM_RE = re.compile(r"\?")


def _rewrite(sql: str) -> str:
    return _PARAM_RE.sub("%s", sql)


class _Cursor:
    def __init__(self, pymy_cursor):
        self._c = pymy_cursor

    def _cols(self):
        d = self._c.description
        return [x[0] for x in d] if d else []

    def fetchone(self):
        r = self._c.fetchone()
        if r is None:
            return None
        return Row(self._cols(), r)

    def fetchall(self):
        cols = self._cols()
        return [Row(cols, r) for r in self._c.fetchall()]

    def __iter__(self):
        cols = self._cols()
        for r in self._c.fetchall():
            yield Row(cols, r)

    def close(self):
        self._c.close()


class Connection:
    """sqlite3-shaped facade over a single pymysql connection.

    Access is serialised with a lock so the ThreadingHTTPServer request threads
    can share one connection safely. That is fine at hackathon scale; the
    hotspot is SQL, not concurrency.
    """

    def __init__(self, **kw):
        self._conn = pymysql.connect(autocommit=True, charset="utf8mb4", **kw)
        self._lock = threading.Lock()

    # sqlite3 attribute the codebase sets after connect(); harmless no-op here
    # because we always return Row-shaped results.
    @property
    def row_factory(self):
        return None

    @row_factory.setter
    def row_factory(self, _value):
        pass

    def cursor(self):
        return _Cursor(self._conn.cursor())

    def execute(self, sql: str, params=()):
        with self._lock:
            c = self._conn.cursor()
            c.execute(_rewrite(sql), tuple(params) if params else None)
        return _Cursor(c)

    def executemany(self, sql: str, seq):
        with self._lock:
            c = self._conn.cursor()
            c.executemany(_rewrite(sql), list(seq))
            c.close()

    def executescript(self, script: str):
        """Run multiple `;`-separated statements. Strips `-- line comments` and
        `/* block comments */` first; our DDL has no `;` inside string literals,
        so a plain split is safe here."""
        # remove /* ... */ blocks
        script = re.sub(r"/\*.*?\*/", "", script, flags=re.S)
        # remove -- line comments (to end of line)
        script = re.sub(r"(?m)--[^\n]*", "", script)
        with self._lock:
            c = self._conn.cursor()
            for stmt in script.split(";"):
                s = stmt.strip()
                if s:
                    c.execute(s)
            c.close()

    def commit(self):
        self._conn.commit()

    def close(self):
        self._conn.close()


def connect() -> Connection:
    return Connection(**dsn())


def connect_source() -> Connection:
    return Connection(**source_dsn())


def source_label() -> str:
    d = source_dsn()
    return f"mysql://{d['user']}@{d['host']}:{d['port']}/{d['database']}"
