#!/usr/bin/env python3
"""api/server.py — the assistant over HTTP, standard library only.

No FastAPI, no uvicorn, no pip install. A judge clones the repo and runs:

    python -m api.server

and gets the chat UI on http://localhost:8720 plus a JSON API. The only optional
dependency in the whole project is `openai`, and it is needed solely for the language
model; every number, guardrail and table works without it.

Endpoints
    GET  /                      the chat page
    GET  /health                liveness + dataset stats
    GET  /stats                 dataset stats
    POST /ask   {question, session_id}      -> full Answer object
    GET  /export.csv?session_id=&n=         -> the last answer's breakdown table as CSV
    POST /demo/fabrication      -> the validator rejecting a doctored answer
"""
from __future__ import annotations

import csv
import io
import json
import os
import sys
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from finassist.engine import Engine, Session   # noqa: E402

DB = os.environ.get("FINASSIST_DB", str(_ROOT / "data" / "finance.sqlite"))
PORT = int(os.environ.get("PORT", "8720"))
USE_LLM = os.environ.get("FINASSIST_LLM", "auto") != "off"

_engine: Engine | None = None
_sessions: dict[str, Session] = {}
# session_id -> list of past answers. Exports are addressed by TURN, not "the last one":
# a follow-up with no breakdown table would otherwise break the export link on the answer
# above it, which the user is still looking at.
_turns: dict[str, list[dict]] = {}
_lock = threading.Lock()


def engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = Engine(DB, use_llm=USE_LLM)
    return _engine


def session_for(sid: str) -> Session:
    with _lock:
        if sid not in _sessions:
            _sessions[sid] = Session()
        return _sessions[sid]


def _rows_to_csv(columns, rows) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(columns)
    w.writerows(rows)
    return buf.getvalue()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):       # keep the console readable
        if "/ask" in (args[0] if args else ""):
            sys.stderr.write("  %s\n" % (fmt % args))

    # ---------------------------------------------------------------- helpers

    def _send(self, code: int, body: bytes, ctype: str, extra: dict | None = None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "content-type")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj):
        self._send(code, json.dumps(obj, default=str).encode(), "application/json")

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n) or b"{}")
        except json.JSONDecodeError:
            return {}

    # ---------------------------------------------------------------- routes

    def do_OPTIONS(self):
        self._send(204, b"", "text/plain")

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)

        if u.path in ("/", "/index.html"):
            page = _ROOT / "ui" / "index.html"
            if not page.exists():
                return self._send(500, b"ui/index.html is missing", "text/plain")
            return self._send(200, page.read_bytes(), "text/html; charset=utf-8")

        if u.path == "/health":
            try:
                return self._json(200, {"status": "ok", **engine().stats()})
            except Exception as e:
                return self._json(500, {"status": "error", "error": str(e)})

        if u.path == "/stats":
            return self._json(200, engine().stats())

        if u.path == "/export.csv":
            sid = (q.get("session_id") or [""])[0]
            turns = _turns.get(sid) or []
            try:
                idx = int((q.get("turn") or ["-1"])[0])
            except ValueError:
                idx = -1
            if not turns or not (-len(turns) <= idx < len(turns)):
                return self._send(404, b"no such turn in this session", "text/plain")
            ans = turns[idx]
            if not ans.get("rows"):
                return self._send(404, b"that answer has no breakdown table", "text/plain")
            body = _rows_to_csv(ans["columns"], ans["rows"]).encode()
            name = f"{ans.get('intent','breakdown')}.csv"
            return self._send(200, body, "text/csv; charset=utf-8",
                              {"Content-Disposition": f'attachment; filename="{name}"'})

        return self._send(404, b"not found", "text/plain")

    def do_POST(self):
        u = urlparse(self.path)

        if u.path == "/ask":
            body = self._body()
            question = (body.get("question") or "").strip()
            if not question:
                return self._json(400, {"error": "question is required"})
            sid = body.get("session_id") or str(uuid.uuid4())
            try:
                ans = engine().ask(question, session_for(sid)).as_dict()
            except Exception as e:
                return self._json(500, {"error": str(e)})
            ans["session_id"] = sid
            ans["exportable"] = bool(ans.get("rows"))
            with _lock:
                _turns.setdefault(sid, []).append(ans)
                ans["turn"] = len(_turns[sid]) - 1
            return self._json(200, ans)

        if u.path == "/demo/fabrication":
            body = self._body()
            q = (body.get("question") or "How much did we spend last month?").strip()
            try:
                return self._json(200, engine().fabrication_demo(q).as_dict())
            except Exception as e:
                return self._json(500, {"error": str(e)})

        if u.path == "/reset":
            sid = (self._body().get("session_id") or "")
            with _lock:
                _sessions.pop(sid, None)
                _turns.pop(sid, None)
            return self._json(200, {"status": "reset"})

        return self._send(404, b"not found", "text/plain")


def main():
    try:
        st = engine().stats()
    except Exception as e:
        print(f"\n  cannot start: {e}\n")
        sys.exit(1)
    print(f"\n  finassist — TBX finance assistant")
    print(f"  db          {st['database']}")
    print(f"  data        {st['transactions']:,} transactions · {st['counterparties']} counterparties")
    print(f"  coverage    {st['coverage_start']} to {st['coverage_end']}  (data clock {st['data_clock']})")
    print(f"  model       {'enabled' if (os.environ.get('LLM_API_KEY') or os.environ.get('OPENAI_API_KEY')) else 'not configured — deterministic answers only'}")
    print(f"\n  ->  http://localhost:{PORT}\n")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
