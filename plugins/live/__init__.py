"""live — auto-refreshing data blocks (v1.6).

A `live` block carries a URL to poll on an interval:

    {"type": "live", "data": {"url": "http://host:port/metrics",
                               "interval": 30,        # seconds (min 5, def 30)
                               "extract": "spend.total",  # dot-path; "" = raw
                               "as": "stat|text|table|spark"}}

The module runs one shared scheduler thread that fetches due blocks and
stores the fetched value + history back into the block's data (via
transform on create, direct store+upsert on refresh), emitting SSE so the
card updates in place. History is capped (500 pts for spark, 2 for delta).

Uses `ctx["core"]` (the canonical server module) for store access — NOT
`import server` — because the server runs as `__main__`, so a plain import
would load a second module copy with its own LOCK (lost updates).

Failure handling: keep the last value, mark data.error + consecutive
failures; the card dims and shows the error after 10.

Fetch safety: http(s) only, 10s timeout, 1MB response cap. Local addresses
are allowed by default (personal canvas); VISOR_LIVE_LOCAL_HOSTS="a,b,c"
restricts to an explicit allowlist for networked installs.
"""
import json
import os
import re
import socket
import threading
import time
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor

NAME = "live"
DESC = "auto-refreshing data blocks (poll a URL, render as stat/text/table/spark)"

TICK = 2.0          # scheduler cadence (seconds)
MIN_INTERVAL = 5
DEF_INTERVAL = 30
MAX_RESPONSE = 1024 * 1024
TIMEOUT = 10
HIST_CAP = {"spark": 500, "stat": 2}
DIM_AFTER = 10      # consecutive failures before the card dims

_state = {}         # block_id -> {value, history, ts, err, fails, last_url, interval, extract, as}
_state_lock = threading.Lock()
_thread = None
_stop = threading.Event()
_CTX = None
_last_save = [0.0]  # throttle state persistence


def register(ctx):
    global _CTX, _thread
    _CTX = ctx
    d = ctx["plugin_dir"](NAME)
    os.makedirs(d, exist_ok=True)
    _load_state()
    _thread = threading.Thread(target=_run, name="live-scheduler", daemon=True)
    _thread.start()
    return {"description": DESC, "card_types": {"live": DESC}}


# ---------------- persistence ----------------

def _path():
    d = _CTX["plugin_dir"](NAME)
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "state.json")


def _load_state():
    try:
        with open(_path()) as f:
            _state.update(json.load(f))
    except Exception:
        pass


def _persist():
    now = time.time()
    if now - _last_save[0] < 2.0:
        return
    _last_save[0] = now
    try:
        tmp = _path() + ".tmp"
        with open(tmp, "w") as f:
            json.dump(_state, f)
        os.replace(tmp, _path())
    except Exception:
        pass


# ---------------- fetching ----------------

def _allow_host(host):
    allow = os.environ.get("VISOR_LIVE_LOCAL_HOSTS", "").strip()
    if not allow:
        return True
    hosts = {h.strip() for h in allow.split(",") if h.strip()}
    try:
        ip = socket.gethostbyname(host)
    except Exception:
        ip = host
    return host in hosts or ip in hosts


def _fetch(url):
    """Fetch a URL -> (ok, payload_or_err). http(s) only, 1MB cap."""
    m = re.match(r"^https?://([^/]+)", url)
    if not m:
        return False, "url must be http(s)"
    host = m.group(1).split(":")[0]
    if not _allow_host(host):
        return False, f"host {host!r} not in VISOR_LIVE_LOCAL_HOSTS"
    req = urllib.request.Request(url, headers={"User-Agent": "visor-live/1.6"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            raw = r.read(MAX_RESPONSE + 1)
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}"
    except Exception as e:
        return False, str(e)[:120]
    if len(raw) > MAX_RESPONSE:
        return False, "response > 1MB"
    try:
        return True, json.loads(raw)
    except Exception:
        return True, raw.decode("utf-8", "replace")  # non-JSON: raw text


def _extract_value(payload, path):
    """Dot-path into JSON (spend.total). Empty path -> the whole payload.
    For non-JSON payloads, the text itself."""
    if not path:
        return payload
    cur = payload
    for part in path.split("."):
        if isinstance(cur, dict):
            if part not in cur:
                raise KeyError(part)
            cur = cur[part]
        elif isinstance(cur, list):
            try:
                cur = cur[int(part)]
            except (ValueError, IndexError):
                raise KeyError(part)
        else:
            raise KeyError(f"{part} (not indexable)")
    return cur


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ---------------- transform (create) ----------------

def transform(board_id, blk):
    """Register a new live block for polling. Refreshes happen in the
    scheduler thread (it can also see blocks pushed before the module
    started, since it scans the store every tick)."""
    if blk.get("type") != "live":
        return blk
    data = dict(blk.get("data") or {})
    interval = data.get("interval") or DEF_INTERVAL
    try:
        interval = max(MIN_INTERVAL, min(300, int(interval)))
    except (TypeError, ValueError):
        interval = DEF_INTERVAL
    data["interval"] = interval
    data.setdefault("as", "stat")
    with _state_lock:
        _state[blk["id"]] = {
            "value": None, "history": [], "ts": 0, "err": None, "fails": 0,
            "last_url": data.get("url", ""), "extract": data.get("extract", ""),
            "as": data.get("as", "stat"),
        }
    blk = dict(blk, data=data)
    _persist()
    return blk


# ---------------- scheduler ----------------

def _live_blocks():
    """Scan the store for live blocks. Returns [(board_id, blk), ...]."""
    core = _CTX["core"]
    st = core.load()
    out = []
    for bid in st["order"]:
        for b in st["boards"][bid].get("blocks", []):
            if b.get("type") == "live":
                out.append((bid, b))
    return out


def _refresh(bid, blk):
    data = blk.get("data") or {}
    url = data.get("url")
    if not url:
        return
    with _state_lock:
        rec = _state.setdefault(blk["id"], {
            "value": None, "history": [], "ts": 0, "err": None, "fails": 0,
            "last_url": "", "extract": "", "as": "stat"})
    if url != rec.get("last_url"):
        rec.update({"last_url": url, "extract": data.get("extract", ""),
                    "as": data.get("as", "stat"), "value": None, "history": [],
                    "ts": 0, "err": None, "fails": 0})
    ok, payload = _fetch(url)
    if ok:
        try:
            val = _extract_value(payload, data.get("extract", ""))
        except Exception as e:
            ok, payload = False, f"extract failed: {e}"
    if ok:
        rec["fails"] = 0
        rec["err"] = None
        rec["value"] = val
        rec["ts"] = time.time()
        cap = HIST_CAP.get(rec.get("as", "stat"), 0)
        if cap:
            rec["history"].append(_num(val) if _num(val) is not None else val)
            rec["history"] = rec["history"][-cap:]
    else:
        rec["fails"] = rec.get("fails", 0) + 1
        rec["err"] = payload
        rec["ts"] = time.time()
    # write the display state back into the stored block so /api/state,
    # exports, and SSE resyncs all carry the latest value.
    core = _CTX["core"]
    newdata = dict(data)
    newdata["live"] = {"value": rec["value"], "ts": rec["ts"],
                       "error": rec["err"], "fails": rec["fails"],
                       "history": rec["history"]}
    with core.LOCK:
        st = core.load()
        b = core.get_board(st, bid)
        if not b:
            return
        blk2 = next((x for x in b["blocks"] if x["id"] == blk["id"]), None)
        if not blk2:
            return
        blk2["data"] = newdata
        blk2["v"] += 1
        blk2["ts"] = time.time()
        core.save(st)
    _CTX["emit"]({"op": "upsert", "id": blk["id"], "board": bid})
    _persist()


def _run():
    while not _stop.wait(TICK):
        try:
            due = []
            for bid, blk in _live_blocks():
                data = blk.get("data") or {}
                if not data.get("url"):
                    continue
                with _state_lock:
                    rec = _state.get(blk["id"])
                    last = rec["ts"] if rec else 0
                interval = data.get("interval", DEF_INTERVAL)
                try:
                    interval = max(MIN_INTERVAL, min(300, int(interval)))
                except (TypeError, ValueError):
                    interval = DEF_INTERVAL
                if time.time() - last >= interval:
                    due.append((bid, blk))
            # Fetch due blocks concurrently (bounded pool) so one slow or
            # dead endpoint with a 10s connect timeout can't starve the
            # others — a serial loop turns N dead endpoints into N*10s of
            # other-block delay per cycle.
            if due:
                with ThreadPoolExecutor(max_workers=min(8, len(due)),
                                        thread_name_prefix="live-fetch") as ex:
                    list(ex.map(lambda t: _refresh(t[0], t[1]), due))
            _prune()
        except Exception:
            import traceback
            print(f"live: scheduler tick failed:", flush=True)
            traceback.print_exc()


def _prune():
    """Drop state for blocks that no longer exist in any board."""
    live_ids = {blk["id"] for _, blk in _live_blocks()}
    with _state_lock:
        stale = [k for k in _state if k not in live_ids]
        for k in stale:
            del _state[k]
    if stale:
        _persist()


def route(method, sub_path, query, body, send):
    if method == "GET" and sub_path == "status":
        with _state_lock:
            items = []
            for bid, blk in _live_blocks():
                rec = _state.get(blk["id"], {})
                items.append({"block": blk["id"], "board": bid,
                              "url": blk.get("data", {}).get("url", ""),
                              "ts": rec.get("ts", 0), "fails": rec.get("fails", 0)})
        send(200, {"blocks": items})
    else:
        send(404, {"error": f"no route {method} {sub_path!r}"})


def shutdown():
    _stop.set()
