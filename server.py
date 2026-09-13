#!/usr/bin/env python3
"""visor — Jarvis-style infinite-canvas display surface for AI agents.

Serves index.html + a JSON block store with SSE live updates. The client
diffs by block id+version, so pushes update sections in place — the page is
never rewritten.

Multi-board: the store holds named boards (tabs in the UI). Every block
endpoint takes ?board=<id> (default "main"). SSE broadcasts carry the
board id so clients can update tab counts without a full refetch.

Store v2 schema:
  {"v": 2, "order": ["main", ...],
   "boards": {"<id>": {"title", "seq", "blocks": [...]} }}
Store v1 ({"title","seq","blocks"}) is migrated to a single "main" board on load.
"""
import json
import mimetypes
import os
import queue
import re
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

ROOT = os.path.dirname(os.path.abspath(__file__))
# Data dir (store.json + media) — a stable `data/` subdir by default so a bare run
# and a containerised run share one source of truth. Override via VISOR_DATA (the
# container mounts a host volume there, keeping the image stateless/rebuildable).
DATA_DIR = os.environ.get("VISOR_DATA") or os.path.join(ROOT, "data")
os.makedirs(os.path.join(DATA_DIR, "media"), exist_ok=True)
STORE = os.path.join(DATA_DIR, "store.json")
MEDIA_DIR = os.path.join(DATA_DIR, "media")
PORT = int(os.environ.get("HOLO_PORT", os.environ.get("VISOR_PORT", "8900")))

LOCK = threading.Lock()
SSE_SUBS = []
SSE_LOCK = threading.Lock()

BOARD_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
DEFAULT_BOARD = "main"


def _new_board(title):
    return {"title": title, "seq": 0, "blocks": []}


def load():
    """Load store, migrating v1 -> v2 in place."""
    if not os.path.exists(STORE):
        return {"v": 2, "order": [DEFAULT_BOARD],
                "boards": {DEFAULT_BOARD: _new_board("Visor")}}
    with open(STORE) as f:
        st = json.load(f)
    if st.get("v") == 2 and st.get("boards"):
        return st
    # v1 (or empty) migration
    st = {"v": 2, "order": [DEFAULT_BOARD],
          "boards": {DEFAULT_BOARD: {
              "title": st.get("title", "Visor"),
              "seq": st.get("seq", 0),
              "blocks": st.get("blocks", [])}}}
    save(st)
    return st


def save(state):
    tmp = STORE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, STORE)


def board_ids(st):
    return st["order"]


def get_board(st, bid):
    if not BOARD_RE.match(bid):
        return None
    return st["boards"].get(bid)


def ensure_board(st, bid, title_hint=""):
    """Return the board, creating it if missing. First-push-to-a-new-board-name
    auto-creates the board so agents (MCP/CLI/HTTP) don't need a create step."""
    b = get_board(st, bid)
    if b:
        return b
    title = str(title_hint or bid).strip() or "board"
    if len(title) > 60:
        title = title[:60]
    st["boards"][bid] = _new_board(title)
    st["order"].append(bid)
    return st["boards"][bid]


def board_title(st, bid):
    b = get_board(st, bid)
    return b["title"] if b else bid


def broadcast(event):
    msg = f"data: {json.dumps(event)}\n\n".encode()
    with SSE_LOCK:
        dead = []
        for q in SSE_SUBS:
            try:
                q.put_nowait(msg)
            except Exception:
                dead.append(q)
        for q in dead:
            SSE_SUBS.remove(q)


def board_list(st):
    return [{"id": bid, "title": st["boards"][bid]["title"],
             "blocks": len(st["boards"][bid]["blocks"]),
             "ts": max([b.get("ts", 0) for b in st["boards"][bid]["blocks"]],
                       default=0)}
            for bid in st["order"]]


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)

    def _body(self):
        n = int(self.headers.get("Content-Length", 0) or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return {}

    def _board_param(self, body=None):
        q = parse_qs(urlparse(self.path).query)
        bid = (q.get("board") or [None])[0]
        if not bid and body:
            bid = body.get("board")
        return (bid or DEFAULT_BOARD).strip() or DEFAULT_BOARD

    def do_GET(self):
        u = urlparse(self.path)
        if u.path in ("/", "/index.html"):
            with open(os.path.join(ROOT, "index.html"), "rb") as f:
                self._send(200, f.read(), "text/html; charset=utf-8")
        elif u.path == "/api/state":
            bid = self._board_param()
            with LOCK:
                st = load()
                b = get_board(st, bid)
                if not b:
                    self._send(404, {"error": f"no board {bid!r}"})
                    return
                self._send(200, {"board": bid, **b})
        elif u.path == "/api/boards":
            with LOCK:
                st = load()
                self._send(200, {"boards": board_list(st)})
        elif u.path == "/api/events":
            q = queue.Queue()
            with SSE_LOCK:
                SSE_SUBS.append(q)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(b": ok\n\n")
            try:
                while True:
                    msg = q.get()
                    self.wfile.write(msg)
                    self.wfile.flush()
            except Exception:
                pass
            finally:
                with SSE_LOCK:
                    if q in SSE_SUBS:
                        SSE_SUBS.remove(q)
        elif u.path.startswith("/media/"):
            name = os.path.basename(u.path)
            p = os.path.join(MEDIA_DIR, name)
            if os.path.isfile(p):
                ct = mimetypes.guess_type(p)[0] or "application/octet-stream"
                with open(p, "rb") as f:
                    self._send(200, f.read(), ct)
            else:
                self._send(404, {"error": "not found"})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        u = urlparse(self.path)
        body = self._body()
        if u.path == "/api/blocks":
            bid = self._board_param(body)
            focus = bool(body.get("focus"))
            with LOCK:
                st = load()
                b = ensure_board(st, bid, body.get("title", ""))
                b["seq"] = b.get("seq", 0) + 1
                blk = {
                    "id": uuid.uuid4().hex[:10],
                    "type": body.get("type", "text"),
                    "title": body.get("title", ""),
                    "seq": b["seq"],
                    "x": body.get("x"),
                    "y": body.get("y"),
                    "w": max(1, min(3, int(body.get("w", 1)))),
                    "data": body.get("data", {}),
                    "v": 1,
                    "ts": time.time(),
                }
                b["blocks"].append(blk)
                save(st)
                broadcast({"op": "boards"})
                broadcast({"op": "upsert", "id": blk["id"], "board": bid,
                           "focus": focus})
            blk["board"] = bid
            self._send(200, blk)
        elif u.path == "/api/clear":
            bid = self._board_param(body)
            with LOCK:
                st = load()
                b = ensure_board(st, bid)
                t = body.get("type")
                if t:
                    b["blocks"] = [x for x in b["blocks"] if x["type"] != t]
                else:
                    b["blocks"] = []
                save(st)
                broadcast({"op": "boards"})
                broadcast({"op": "clear", "board": bid})
            self._send(200, {"ok": True})
        elif u.path == "/api/meta":
            bid = self._board_param(body)
            with LOCK:
                st = load()
                b = ensure_board(st, bid, body.get("title", ""))
                if "title" in body:
                    b["title"] = body["title"]
                save(st)
                broadcast({"op": "boards"})
                broadcast({"op": "meta", "board": bid,
                           "title": b["title"]})
            self._send(200, {"ok": True})
        elif u.path == "/api/boards":
            title = str(body.get("title", "")).strip() or "board"
            with LOCK:
                st = load()
                base = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:40] or "board"
                candidate, n = base, 2
                while candidate in st["boards"]:
                    candidate = f"{base}-{n}"
                    n += 1
                st["boards"][candidate] = _new_board(title)
                st["order"].append(candidate)
                save(st)
                broadcast({"op": "boards"})
                created = {"id": candidate, "title": title}
            self._send(200, created)
        else:
            self._send(404, {"error": "not found"})

    def do_PATCH(self):
        u = urlparse(self.path)
        m = re.match(r"^/api/blocks/([\w-]+)$", u.path)
        if not m:
            bm = re.match(r"^/api/boards/([\w-]+)$", u.path)
            if bm:
                bid = bm.group(1)
                body = self._body()
                with LOCK:
                    st = load()
                    b = get_board(st, bid)
                    if not b:
                        self._send(404, {"error": "no board"})
                        return
                    if "title" in body:
                        b["title"] = str(body["title"])
                    save(st)
                    broadcast({"op": "boards", "board": bid})
                    self._send(200, {"id": bid, "title": b["title"]})
                return
            self._send(404, {"error": "not found"})
            return
        bid = m.group(1)
        body = self._body()
        target = self._board_param(body)
        with LOCK:
            st = load()
            b = get_board(st, target)
            if not b:
                self._send(404, {"error": f"no board {target!r}"})
                return
            blk = next((x for x in b["blocks"] if x["id"] == bid), None)
            if not blk:
                self._send(404, {"error": "no block"})
                return
            for k in ("title", "type", "w", "x", "y", "data"):
                if k in body:
                    blk[k] = body[k]
            blk["v"] += 1
            blk["ts"] = time.time()
            save(st)
            broadcast({"op": "upsert", "id": bid, "board": target})
            self._send(200, blk)

    def do_DELETE(self):
        u = urlparse(self.path)
        m = re.match(r"^/api/blocks/([\w-]+)$", u.path)
        if not m:
            bm = re.match(r"^/api/boards/([\w-]+)$", u.path)
            if bm:
                bid = bm.group(1)
                with LOCK:
                    st = load()
                    if bid not in st["boards"]:
                        self._send(404, {"error": "no board"})
                        return
                    if len(st["order"]) <= 1:
                        self._send(409, {"error": "cannot delete the last board"})
                        return
                    del st["boards"][bid]
                    st["order"].remove(bid)
                    save(st)
                    broadcast({"op": "boards", "gone": bid})
                    self._send(200, {"ok": True})
                return
            self._send(404, {"error": "not found"})
            return
        bid = m.group(1)
        target = self._board_param()
        with LOCK:
            st = load()
            b = get_board(st, target)
            if not b:
                self._send(404, {"error": f"no board {target!r}"})
                return
            before = len(b["blocks"])
            b["blocks"] = [x for x in b["blocks"] if x["id"] != bid]
            if len(b["blocks"]) == before:
                self._send(404, {"error": "no block"})
                return
            save(st)
            broadcast({"op": "del", "id": bid, "board": target})
            self._send(200, {"ok": True})


def main():
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    srv.daemon_threads = True
    print(f"visor: http://localhost:{PORT}  (store: {STORE})", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
