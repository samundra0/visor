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
   "boards": {"<id>": {"title", "seq", "blocks": [...],
                       "links": [{"id", "from", "to", "label"}]}} }}
Store v1 ({"title","seq","blocks"}) is migrated to a single "main" board on load.
Links are board-level (cross-block relations): a link connects two block ids
on the same board and is pruned when either endpoint is deleted or the board
is cleared.
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
PORT = int(os.environ.get("VISOR_PORT", os.environ.get("HOLO_PORT", "8900")))
# Optional bearer auth: set VISOR_TOKEN to require `Authorization: Bearer <token>`
# on all /api/* and /media/* requests. Unset = open (localhost/LAN use).
TOKEN = os.environ.get("VISOR_TOKEN", "").strip()

LOCK = threading.Lock()
SSE_SUBS = []
SSE_LOCK = threading.Lock()

BOARD_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
DEFAULT_BOARD = "main"


def _new_board(title):
    return {"title": title, "seq": 0, "blocks": [], "links": []}


def load():
    """Load store, migrating v1 -> v2 in place."""
    if not os.path.exists(STORE):
        return {"v": 2, "order": [DEFAULT_BOARD],
                "boards": {DEFAULT_BOARD: _new_board("Visor")}}
    with open(STORE) as f:
        st = json.load(f)
    if st.get("v") == 2 and st.get("boards"):
        # normalize: older v2 boards may lack the "links" key
        for b in st["boards"].values():
            b.setdefault("links", [])
        return st
    # v1 (or empty) migration
    st = {"v": 2, "order": [DEFAULT_BOARD],
          "boards": {DEFAULT_BOARD: {
              "title": st.get("title", "Visor"),
              "seq": st.get("seq", 0),
              "blocks": st.get("blocks", []),
              "links": []}}}
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


def prune_links(b):
    """Drop links whose endpoints no longer exist (block deleted / cleared).
    Returns True if anything was removed."""
    ids = {x["id"] for x in b["blocks"]}
    keep = [l for l in b.get("links", []) if l["from"] in ids and l["to"] in ids]
    if len(keep) != len(b.get("links", [])):
        b["links"] = keep
        return True
    return False


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


_NUM_ABORT = object()
_BODY_ABORT = object()
MAX_BODY = 10 * 1024 * 1024  # 10MB — a client shouldn't POST more than this


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
        """Parse a JSON request body. Returns {} for an empty body (most
        mutations are optional), and _BODY_ABORT (with 400/413 already sent)
        for malformed or oversized bodies — callers must `is _BODY_ABORT`.
        (Old code returned {} for malformed JSON, so a POST with a broken
        body silently created an empty block with HTTP 200.)"""
        try:
            n = int(self.headers.get("Content-Length", 0) or 0)
        except (TypeError, ValueError):
            n = 0
        if n <= 0:
            return {}
        if n > MAX_BODY:
            self._send(413, {"error": f"body too large ({n} bytes > {MAX_BODY})"})
            return _BODY_ABORT
        raw = self.rfile.read(n)
        try:
            parsed = json.loads(raw or b"{}")
        except Exception:
            self._send(400, {"error": "body is not valid JSON"})
            return _BODY_ABORT
        if not isinstance(parsed, dict):
            self._send(400, {"error": "body must be a JSON object"})
            return _BODY_ABORT
        return parsed

    def _authed(self):
        """Optional bearer auth (VISOR_TOKEN). Always True when unset. /api/*
        and /media/* are gated; /, /index.html and /api/health are open so the
        page loads and healthchecks pass. Accepted via `Authorization: Bearer
        <t>` header OR a `?token=*** query param (EventSource can't set
        headers, so the SSE stream uses the query)."""
        if not TOKEN:
            return True
        got = (self.headers.get("Authorization") or "").removeprefix("Bearer ").strip()
        if got == TOKEN:
            return True
        q = parse_qs(urlparse(self.path).query)
        return (q.get("token") or [""])[0] == TOKEN

    def _need_auth(self):
        """Gate for /api/* and /media/*. Returns True (and has sent 401) when
        the request should stop here. Only meaningful when VISOR_TOKEN is set."""
        if self._authed():
            return False
        self._send(401, {"error": "unauthorized (set Authorization: Bearer <VISOR_TOKEN>)"})
        return True

    def do_OPTIONS(self):
        # CORS preflight. Without this a browser cross-origin write dies with
        # 501 (BaseHTTPRequestHandler has no default). Responses already carry
        # Access-Control-Allow-Origin: *, so answering preflight lets same-origin
        # and explicitly-permitted origins actually write.
        origin = self.headers.get("Origin", "")
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", origin or "*")
        self.send_header("Access-Control-Allow-Methods",
                         "GET, POST, PATCH, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers",
                         "Content-Type, Authorization")
        self.send_header("Access-Control-Max-Age", "86400")
        self.end_headers()

    def _board_param(self, body=None):
        q = parse_qs(urlparse(self.path).query)
        bid = (q.get("board") or [None])[0]
        if not bid and body:
            bid = body.get("board")
        bid = (bid or DEFAULT_BOARD).strip() or DEFAULT_BOARD
        # Validate at the API edge: an id that can't be read back would create
        # an orphaned board (push succeeds, /api/state 404s, no delete path).
        if not BOARD_RE.match(bid):
            self._send(400, {"error": f"bad board id {bid!r} "
                                      "(want ^[a-z0-9][a-z0-9_-]{0,63}$)"})
            return None
        return bid

    def _num(self, v, lo, hi, field, default=None):
        """Coerce a client-supplied number to an int in [lo, hi].
        Missing (None) -> default. Bad type or range -> 400 sent + _NUM_ABORT
        (callers must check with `is _NUM_ABORT` — a plain None can be a valid
        stored value, e.g. an unset x/y)."""
        if v is None:
            return default
        try:
            n = int(v)
        except (TypeError, ValueError):
            self._send(400, {"error": f"{field} must be an integer, got {v!r}"})
            return _NUM_ABORT
        if not lo <= n <= hi:
            self._send(400, {"error": f"{field} must be {lo}..{hi}, got {n}"})
            return _NUM_ABORT
        return n

    def do_GET(self):
        u = urlparse(self.path)
        if u.path in ("/", "/index.html"):
            with open(os.path.join(ROOT, "index.html"), "rb") as f:
                self._send(200, f.read(), "text/html; charset=utf-8")
        elif u.path == "/api/health":
            # Open (no auth) so compose healthchecks and agents can probe
            # readiness without a token or parsing the board list.
            self._send(200, {"ok": True, "auth": bool(TOKEN)})
        elif self._need_auth():
            return
        elif u.path == "/api/state":
            bid = self._board_param()
            if bid is None:
                return
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
        if self._need_auth():
            return
        body = self._body()
        if body is _BODY_ABORT:
            return
        if u.path == "/api/blocks":
            bid = self._board_param(body)
            if bid is None:
                return
            focus = bool(body.get("focus"))
            w = self._num(body.get("w", 1), 1, 3, "w", default=1)
            if w is _NUM_ABORT:
                return
            x = self._num(body.get("x"), -100000, 100000, "x")
            if x is _NUM_ABORT:
                return
            y = self._num(body.get("y"), -100000, 100000, "y")
            if y is _NUM_ABORT:
                return
            with LOCK:
                st = load()
                b = ensure_board(st, bid, body.get("title", ""))
                b["seq"] = b.get("seq", 0) + 1
                blk = {
                    "id": uuid.uuid4().hex[:10],
                    "type": body.get("type", "text"),
                    "title": body.get("title", ""),
                    "seq": b["seq"],
                    "x": x,
                    "y": y,
                    "w": w,
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
            if bid is None:
                return
            with LOCK:
                st = load()
                b = ensure_board(st, bid)
                t = body.get("type")
                if t:
                    b["blocks"] = [x for x in b["blocks"] if x["type"] != t]
                else:
                    b["blocks"] = []
                if not t:
                    b["links"] = []
                else:
                    prune_links(b)
                save(st)
                broadcast({"op": "boards"})
                broadcast({"op": "links", "board": bid})
                broadcast({"op": "clear", "board": bid})
            self._send(200, {"ok": True})
        elif u.path == "/api/meta":
            bid = self._board_param(body)
            if bid is None:
                return
            with LOCK:
                st = load()
                b = ensure_board(st, bid, body.get("title", ""))
                if "title" in body:
                    # empty/whitespace title -> fall back to the board id
                    # (parity with POST /api/boards; otherwise the tab shows blank)
                    b["title"] = str(body["title"]).strip() or bid
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
        elif u.path == "/api/links":
            bid = self._board_param(body)
            if bid is None:
                return
            src, dst = str(body.get("from") or ""), str(body.get("to") or "")
            label = str(body.get("label") or "")
            with LOCK:
                st = load()
                b = get_board(st, bid)
                if not b:
                    self._send(404, {"error": f"no board {bid!r}"})
                    return
                ids = {x["id"] for x in b["blocks"]}
                if not src or not dst:
                    self._send(400, {"error": "need from and to block ids"})
                    return
                if src == dst:
                    self._send(400, {"error": "self-link"})
                    return
                if src not in ids or dst not in ids:
                    self._send(404, {"error": "unknown block id"})
                    return
                if any(l["from"] == src and l["to"] == dst for l in b["links"]):
                    self._send(409, {"error": "link already exists"})
                    return
                link = {"id": uuid.uuid4().hex[:10], "from": src,
                        "to": dst, "label": label}
                b["links"].append(link)
                save(st)
                broadcast({"op": "links", "board": bid})
            self._send(200, link)
        else:
            self._send(404, {"error": "not found"})

    def do_PATCH(self):
        u = urlparse(self.path)
        if self._need_auth():
            return
        lm = re.match(r"^/api/links/([\w-]+)$", u.path)
        if lm:
            lid = lm.group(1)
            body = self._body()
            if body is _BODY_ABORT:
                return
            target = self._board_param(body)
            if target is None:
                return
            with LOCK:
                st = load()
                b = get_board(st, target)
                if not b:
                    self._send(404, {"error": f"no board {target!r}"})
                    return
                link = next((l for l in b["links"] if l["id"] == lid), None)
                if not link:
                    self._send(404, {"error": "no link"})
                    return
                if "label" in body:
                    link["label"] = str(body["label"])
                save(st)
                broadcast({"op": "links", "board": target})
                self._send(200, link)
            return
        m = re.match(r"^/api/blocks/([\w-]+)$", u.path)
        if not m:
            bm = re.match(r"^/api/boards/([\w-]+)$", u.path)
            if bm:
                bid = bm.group(1)
                body = self._body()
                if body is _BODY_ABORT:
                    return
                with LOCK:
                    st = load()
                    b = get_board(st, bid)
                    if not b:
                        self._send(404, {"error": "no board"})
                        return
                    if "title" in body:
                        b["title"] = str(body["title"]).strip() or bid
                    save(st)
                    broadcast({"op": "boards", "board": bid})
                    self._send(200, {"id": bid, "title": b["title"]})
                return
            self._send(404, {"error": "not found"})
            return
        bid = m.group(1)
        body = self._body()
        if body is _BODY_ABORT:
            return
        target = self._board_param(body)
        if target is None:
            return
        # Validate the same scalars POST does, so an update can't store
        # out-of-range values (w:7 etc.) that the client renders badly.
        new = {}
        for k, (lo, hi) in (("w", (1, 3)), ("x", (-100000, 100000)),
                            ("y", (-100000, 100000))):
            if k in body:
                v = self._num(body[k], lo, hi, k)
                if v is _NUM_ABORT:
                    return
                new[k] = v
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
            for k in ("title", "type", "data"):
                if k in body:
                    blk[k] = body[k]
            blk.update(new)
            blk["v"] += 1
            blk["ts"] = time.time()
            save(st)
            broadcast({"op": "upsert", "id": bid, "board": target})
            self._send(200, blk)

    def do_DELETE(self):
        u = urlparse(self.path)
        if self._need_auth():
            return
        lm = re.match(r"^/api/links/([\w-]+)$", u.path)
        if lm:
            lid = lm.group(1)
            target = self._board_param()
            if target is None:
                return
            with LOCK:
                st = load()
                b = get_board(st, target)
                if not b:
                    self._send(404, {"error": f"no board {target!r}"})
                    return
                before = len(b["links"])
                b["links"] = [l for l in b["links"] if l["id"] != lid]
                if len(b["links"]) == before:
                    self._send(404, {"error": "no link"})
                    return
                save(st)
                broadcast({"op": "links", "board": target})
                self._send(200, {"ok": True})
            return
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
        if target is None:
            return
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
            pruned = prune_links(b)
            save(st)
            if pruned:
                broadcast({"op": "links", "board": target})
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
