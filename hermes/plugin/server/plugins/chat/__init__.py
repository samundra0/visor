"""chat — live chat bridge to the connected Hermes agent (v1.7).

A `chat` block is a card with an input box. Sending a message:

  browser -> POST /api/plugins/chat/send  {message}
  this module -> POST <VISOR_CHAT_URL>  (Hermes webhook, HMAC V2 signed)

Hermes runs an agent turn on that message; the agent's prompt tells it to
push its reply onto the visor (visor_push, same board). The card renders
the transcript (user + agent turns) and polls for new agent replies while
a request is pending.

Webhook signing matches Hermes' generic V2 scheme (gateway/platforms/
webhook.py): X-Webhook-Timestamp = str(int(time)), X-Webhook-Signature-V2
= hex HMAC-SHA256(secret, f"{ts}.{raw_body}"). Replay window ~5min.

Config (env, read at call time):
  VISOR_CHAT_URL      e.g. http://host.docker.internal:8645/webhooks/visor-chat
  VISOR_CHAT_SECRET   the route's HMAC secret (hermes webhook list)
  VISOR_CHAT_REPLY    tag string the agent uses on reply blocks
                      (default "agent" — the prompt tells it to set
                      data.reply_tag to this)
Unconfigured: send() returns {sent:false, reason:"bridge not configured"}
and the card shows the hint instead of a dead input.

State (per chat block): pending message id + ts, transcript (capped 50).
"""
import hashlib
import hmac
import json
import os
import threading
import time
import urllib.request
import urllib.error
import uuid

NAME = "chat"
DESC = "live chat bridge to the connected agent (webhook -> agent -> visor_push)"

TIMEOUT = 10          # webhook POST timeout
MAX_MSG = 4000        # per-message cap
TRANSCRIPT_CAP = 50
REPLY_TAG_DEFAULT = "agent"

_state = {}           # block_id -> {pending: {id, text, ts}, transcript: [...]}
_lock = threading.Lock()
_CTX = None


def register(ctx):
    global _CTX
    _CTX = ctx
    d = ctx["plugin_dir"](NAME)
    os.makedirs(d, exist_ok=True)
    _load()
    return {"description": DESC, "card_types": {"chat": DESC}}


def _path():
    d = _CTX["plugin_dir"](NAME)
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "state.json")


def _load():
    try:
        with open(_path()) as f:
            with _lock:
                _state.update(json.load(f))
    except Exception:
        pass


def _persist():
    try:
        tmp = _path() + ".tmp"
        with _lock:
            data = json.dumps(_state)
        with open(tmp, "w") as f:
            f.write(data)
        os.replace(tmp, _path())
    except Exception:
        pass


def _cfg():
    url = os.environ.get("VISOR_CHAT_URL", "").strip()
    secret = os.environ.get("VISOR_CHAT_SECRET", "").strip()
    tag = os.environ.get("VISOR_CHAT_REPLY", "").strip() or REPLY_TAG_DEFAULT
    return url, secret, tag


def _board_reply_ts(board_id):
    """Latest ts of a reply_tag-tagged block on the board (the agent's
    answer, pushed via visor_push), or 0."""
    core = _CTX["core"]
    tag = _cfg()[2]
    with core.LOCK:
        st = core.load()
        b = core.get_board(st, board_id)
    if not b:
        return 0.0
    ts = 0.0
    for blk in b.get("blocks", []):
        if (blk.get("data") or {}).get("reply_tag") == tag and blk.get("ts", 0) > ts:
            ts = blk.get("ts", 0)
    return ts


def _signed_post(url, secret, payload):
    """POST payload as JSON with HMAC V2 signature -> (ok, detail)."""
    body = json.dumps(payload).encode()
    ts = str(int(time.time()))
    sig = hmac.new(secret.encode(), ts.encode() + b"." + body,
                   hashlib.sha256).hexdigest()
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "application/json",
                 "X-Webhook-Timestamp": ts,
                 "X-Webhook-Signature-V2": sig})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            raw = r.read(65536)
            try:
                return True, json.loads(raw)
            except Exception:
                return True, raw.decode("utf-8", "replace")[:300]
    except urllib.error.HTTPError as e:
        return False, f"webhook HTTP {e.code}: {e.read(300).decode('utf-8', 'replace')}"
    except Exception as e:
        return False, str(e)[:200]


def _rec(block_id):
    with _lock:
        return _state.setdefault(block_id, {"pending": None, "transcript": []})


def transform(board_id, blk):
    if blk.get("type") != "chat":
        return blk
    _rec(blk["id"])
    blk = dict(blk, data=dict(blk.get("data") or {}))
    blk["data"].setdefault("placeholder", "ask the agent…")
    _persist()
    return blk


def _qstr(query, key, default=""):
    """Core passes query params as lists (parse_qs) — normalize to str."""
    v = (query or {}).get(key, default)
    if isinstance(v, (list, tuple)):
        return str(v[0]) if v else default
    return str(v) if v is not None else default


def route(method, sub_path, query, body, send):
    body = body or {}
    board = _qstr(query, "board")
    if method == "GET" and sub_path == "config":
        url, secret, tag = _cfg()
        # never expose the secret to the client — only a configured flag
        send(200, {"configured": bool(url and secret), "reply_tag": tag})
        return
    if method == "POST" and sub_path == "send":
        msg = str(body.get("message", "")).strip()
        if not msg:
            send(400, {"error": "message is empty"})
            return
        if len(msg) > MAX_MSG:
            send(400, {"error": f"message > {MAX_MSG} chars"})
            return
        block_id = str(body.get("block", ""))
        url, secret, tag = _cfg()
        if not (url and secret):
            # Unconfigured: the attempt still lands in the transcript (the
            # card shows the user tried; the hint is in the response), so
            # record the user line before returning.
            rec = _rec(block_id)
            with _lock:
                rec["transcript"].append({"who": "user", "text": msg, "ts": time.time()})
                rec["transcript"] = rec["transcript"][-TRANSCRIPT_CAP:]
            _persist()
            send(200, {"sent": False, "reason": "bridge not configured (VISOR_CHAT_URL + VISOR_CHAT_SECRET)"})
            return
        mid = uuid.uuid4().hex[:8]
        ok, detail = _signed_post(url, secret, {
            "message": msg, "board": board,
            "id": mid, "ts": time.time()})
        rec = _rec(block_id)
        with _lock:
            rec["pending"] = {"id": mid, "text": msg, "ts": time.time()} if ok else None
            rec["transcript"].append({"who": "user", "text": msg, "ts": time.time()})
            rec["transcript"] = rec["transcript"][-TRANSCRIPT_CAP:]
            if not ok:
                rec["transcript"].append({"who": "error",
                                          "text": detail, "ts": time.time()})
                rec["transcript"] = rec["transcript"][-TRANSCRIPT_CAP:]
        _persist()
        # notify the client to re-render (transcript + waiting state)
        if block_id:
            _CTX["emit"]({"op": "chat", "board": board,
                          "id": block_id, "msg": mid, "sent": ok})
        send(200, {"sent": ok, "id": mid, "detail": detail if not ok else None})
        return
    if method == "POST" and sub_path == "reply":
        # agent reply registration: {id, text, tag?} — appends to the
        # block transcript and clears pending when tag matches.
        block_id = str(body.get("block", ""))
        text = str(body.get("text", "")).strip()
        if not block_id or not text:
            send(400, {"error": "block + text required"})
            return
        tag = body.get("tag") or _cfg()[2]
        rec = _rec(block_id)
        with _lock:
            rec["transcript"].append({"who": "agent", "text": text,
                                      "ts": time.time(), "tag": tag})
            rec["transcript"] = rec["transcript"][-TRANSCRIPT_CAP:]
            if rec["pending"]:
                rec["pending"] = None
        _persist()
        _CTX["emit"]({"op": "chat", "board": board,
                      "id": block_id, "reply": True})
        send(200, {"ok": True})
        return
    if method == "GET" and sub_path == "status":
        board = board
        with _lock:
            pendings = {k: v["pending"] for k, v in _state.items() if v.get("pending")}
            # only transcript tail (last 10) — the card shows recent history;
            # full agent answers live in their own reply_tag blocks.
            transcripts = {k: v["transcript"][-10:] for k, v in _state.items()
                           if v.get("transcript")}
        url, secret, tag = _cfg()
        send(200, {"pending": pendings,
                   "transcripts": transcripts,
                   "configured": bool(url and secret),
                   "reply_ts": _board_reply_ts(board) if board else 0.0})
        return
    send(404, {"error": f"no route {method} {sub_path!r}"})
