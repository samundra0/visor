#!/usr/bin/env python3
"""Local copy of the CI smoke test — run against a live visor on :8900.

Usage:
  python3 scripts/smoke.py                    # core cases (module-off server)
  python3 scripts/smoke.py --base URL         # against a different instance
  python3 scripts/smoke.py --with-modules selftest
      # additionally asserts the plugin paths work end-to-end (server must
      # have run with VISOR_MODULES including selftest)
"""
import argparse, json, urllib.request, urllib.parse

ap = argparse.ArgumentParser()
ap.add_argument("--base", default="http://127.0.0.1:8900")
ap.add_argument("--with-modules", default="",
                help="comma list of modules to assert plugin cases for")
ARGS = ap.parse_args()
BASE = ARGS.base.rstrip("/")
WITH_MODULES = [m for m in ARGS.with_modules.split(",") if m]

def call(method, path, obj=None):
    data = json.dumps(obj).encode() if obj is not None else None
    req = urllib.request.Request(BASE + path, data=data,
        headers={"Content-Type": "application/json"} if data else {}, method=method)
    return json.loads(urllib.request.urlopen(req).read())

# create board
r = call("POST", "/api/boards", {"title": "ci-local-smoke"})
bid = r["id"]
print("board:", bid)
bq = urllib.parse.quote(bid)

# push a block
r = call("POST", f"/api/blocks?board={bq}",
         {"type": "stat", "title": "ci", "data": {"items": [{"value": "1", "label": "ok", "tone": "ok"}]}})
blk = r["id"]
print("block:", blk)

# read state back
st = call("GET", f"/api/state?board={bq}")
assert len(st["blocks"]) == 1 and st["blocks"][0]["type"] == "stat", st
print("state OK")

# patch in place
call("PATCH", f"/api/blocks/{blk}?board={bq}",
     {"data": {"items": [{"value": "2", "label": "ok", "tone": "ok"}]}})
st = call("GET", f"/api/state?board={bq}")
assert st["blocks"][0]["data"]["items"][0]["value"] == "2", st
print("patch OK")

# client page reachable and branded
page = urllib.request.urlopen(BASE + "/").read().decode()
assert "VISOR" in page
print("page OK")

# SSE stream reachable (host/port from --base, not hardcoded)
import socket
from urllib.parse import urlparse as _up
_h, _p = _up(BASE).hostname, _up(BASE).port
s = socket.create_connection((_h, _p), timeout=3)
s.sendall(b"GET /api/events HTTP/1.1\r\nHost: x\r\nAccept: text/event-stream\r\n\r\n")
data = s.recv(200).decode(errors="replace")
assert "event:" in data or "200" in data, data[:100]
print("sse OK")
s.close()

# health endpoint (open, used by the compose healthcheck)
with urllib.request.urlopen(BASE + "/api/health") as r:
    h = json.loads(r.read())
assert h.get("ok") is True, h
print("health OK")

# malformed JSON body must 400, not create an empty block with 200
import urllib.error
req = urllib.request.Request(BASE + f"/api/blocks?board={bq}", data=b"not json",
    headers={"Content-Type": "application/json"}, method="POST")
try:
    with urllib.request.urlopen(req) as r:
        code = r.status
except urllib.error.HTTPError as e:
    code = e.code
assert code == 400, f"malformed JSON body -> {code}, expected 400"
print("malformed JSON rejected (400) OK")

# links: create between two blocks, verify state, errors, prune
import urllib.error
def call_expect(code, method, path, obj=None):
    """Call and assert the exact status code (a smoke test that can't fail
    on its error cases proves nothing)."""
    data = json.dumps(obj).encode() if obj is not None else None
    req = urllib.request.Request(BASE + path, data=data,
        headers={"Content-Type": "application/json"} if data else {}, method=method)
    try:
        with urllib.request.urlopen(req) as r:
            st = r.status
    except urllib.error.HTTPError as e:
        st = e.code
    assert st == code, f"{method} {path} -> {st}, expected {code}"
    return st

# --- input validation (regressions: orphan boards + connection reset) ---
st = call_expect(400, "POST", "/api/blocks?board=BAD!!ID",
                 {"type": "text", "title": "orphan?", "data": {"text": "x"}})
blist = call("GET", "/api/boards")
assert not any(b["id"] == "BAD!!ID" for b in blist["boards"]), \
    f"malformed board persisted: {blist}"
print("bad board id rejected, not persisted OK")
st = call_expect(400, "POST", f"/api/blocks?board={bq}",
                 {"type": "text", "title": "w?", "w": {"a": 1}})
print("malformed w rejected (400, not reset) OK")

b2 = call("POST", f"/api/blocks?board={bq}",
          {"type": "text", "title": "b2", "data": {"text": "x"}})
lk = call("POST", f"/api/links?board={bq}",
          {"from": blk, "to": b2["id"], "label": "rel"})
lid = lk["id"]
stt = call("GET", f"/api/state?board={bq}")
assert any(l["id"] == lid for l in stt.get("links", [])), stt
print("link create + state OK")
call_expect(409, "POST", f"/api/links?board={bq}", {"from": blk, "to": b2["id"]})
call_expect(400, "POST", f"/api/links?board={bq}", {"from": blk, "to": blk})
call_expect(404, "POST", f"/api/links?board={bq}", {"from": blk, "to": "nope"})
print("link errors (409/400/404) OK")
lk2 = call("PATCH", f"/api/links/{lid}?board={bq}", {"label": "renamed"})
stt = call("GET", f"/api/state?board={bq}")
assert stt["links"][0]["label"] == "renamed", stt
print("link patch OK")
# patch validation: out-of-range w must 400, not be stored (parity with POST)
call_expect(400, "PATCH", f"/api/blocks/{blk}?board={bq}", {"w": 7})
stt = call("GET", f"/api/state?board={bq}")
assert stt["blocks"][0]["w"] != 7, stt
call_expect(200, "PATCH", f"/api/blocks/{blk}?board={bq}", {"w": 2})
stt = call("GET", f"/api/state?board={bq}")
assert stt["blocks"][0]["w"] == 2, stt
print("patch w validation OK")
call("DELETE", f"/api/blocks/{b2['id']}?board={bq}")
stt = call("GET", f"/api/state?board={bq}")
assert stt.get("links", []) == [], stt
print("link prune on block delete OK")

# --- plugins (v1.5) ---
# The module manifest is an open route; core-only servers return [].
man = json.loads(urllib.request.urlopen(BASE + "/api/modules").read())
mods = [m["name"] for m in man.get("modules", [])]
assert isinstance(man.get("modules"), list), man
print("modules manifest OK:", mods or "none")

# A module-off push of its type must 400 (same as unknown types).
if not (set(mods) & {"selftest"}):
    code = call_expect(400, "POST", f"/api/blocks?board={bq}",
                       {"type": "selftest", "title": "off", "data": {}})
    print("module-off type rejected (400) OK")
if "selftest" in WITH_MODULES and "selftest" in mods:
    # transform hook: the stored block must be rewritten by the module
    r = call("POST", f"/api/blocks?board={bq}",
             {"type": "selftest", "title": "on", "data": {"html": "hi"}})
    sid = r["id"]
    assert r["data"].get("touched") is True, r
    assert r["data"].get("board") == bid, r
    stt = call("GET", f"/api/state?board={bq}")
    stored = next(x for x in stt["blocks"] if x["id"] == sid)
    assert stored["data"]["touched"] is True, stored
    print("module transform applied on push + persisted OK")
    # module route: /api/plugins/selftest/ping
    pong = call("GET", "/api/plugins/selftest/ping")
    assert pong.get("pong") is True, pong
    print("module route OK")
    # module client.js is served for enabled modules
    js = urllib.request.urlopen(BASE + "/plugins/selftest/client.js").read().decode()
    assert "VisorModules" in js, js[:80]
    print("module client.js served OK")
    call("DELETE", f"/api/blocks/{sid}?board={bq}")

if "live" in WITH_MODULES and "live" in mods:
    import time as _time
    # happy path: poll our own /api/health (open endpoint), extract "ok"
    r = call("POST", f"/api/blocks?board={bq}",
             {"type": "live", "title": "health",
              "data": {"url": BASE + "/api/health", "interval": 5,
                       "extract": "ok", "as": "stat"}})
    lid = r["id"]
    assert r["data"].get("interval") == 5, r
    # first refresh happens on the next scheduler tick (~2s) + a bit
    _time.sleep(6)
    stt = call("GET", f"/api/state?board={bq}")
    stored = next(x for x in stt["blocks"] if x["id"] == lid)
    lv = stored["data"].get("live", {})
    # poll until the scheduler has written back (first tick ~2s + fetch)
    for _ in range(15):
        if lv.get("value") is not None or lv.get("error"):
            break
        _time.sleep(2)
        stt = call("GET", f"/api/state?board={bq}")
        stored = next(x for x in stt["blocks"] if x["id"] == lid)
        lv = stored["data"].get("live", {})
    assert lv.get("value") is True, stored
    assert lv.get("ts", 0) > 0, stored
    assert lv.get("error") is None, stored
    print("live poll + extract + state writeback OK")
    # error path: unreachable port -> error recorded, value kept as-is
    r = call("POST", f"/api/blocks?board={bq}",
             {"type": "live", "title": "broken",
              "data": {"url": "http://127.0.0.1:1/nope", "interval": 5,
                       "as": "text"}})
    bid2 = r["id"]
    # closed-port connect can be instant (refused) or slow (filtered);
    # a single 6s sleep raced the tick cadence + fetch time. Poll instead.
    lv = {}
    for _ in range(20):
        _time.sleep(2)
        stt = call("GET", f"/api/state?board={bq}")
        stored = next(x for x in stt["blocks"] if x["id"] == bid2)
        lv = stored["data"].get("live", {})
        if lv.get("error"):
            break
    assert lv.get("error"), stored
    assert lv.get("fails", 0) >= 1, stored
    print("live error path (unreachable -> error + fails) OK")
    call("DELETE", f"/api/blocks/{lid}?board={bq}")
    call("DELETE", f"/api/blocks/{bid2}?board={bq}")

if "chat" in WITH_MODULES and "chat" in mods:
    # config reflects env (CI runs with VISOR_CHAT_URL unset -> configured=false)
    cfg = call("GET", "/api/plugins/chat/config")
    assert cfg.get("configured") in (True, False), cfg
    print("chat config OK:", cfg)
    # client.js is served for the module
    js = urllib.request.urlopen(BASE + "/plugins/chat/client.js").read().decode()
    assert "VisorModules" in js, js[:80]
    print("chat client.js served OK")
    # push a chat card (transform adds the placeholder)
    r = call("POST", f"/api/blocks?board={bq}", {"type": "chat", "title": "ci chat", "data": {}})
    cid = r["id"]
    assert "placeholder" in (r.get("data") or {}), r
    # unconfigured send: sent=false + a "not configured" reason, no 500;
    # the user line still lands in the transcript
    if not cfg["configured"]:
        s = call("POST", f"/api/plugins/chat/send?board={bq}",
                 {"message": "hi", "block": cid})
        assert s.get("sent") is False, s
        assert "configured" in (s.get("reason") or "").lower(), s
        print("chat send (unconfigured) OK")
    else:
        call("POST", f"/api/plugins/chat/send?board={bq}",
             {"message": "hi", "block": cid})
    stt = call("GET", f"/api/plugins/chat/status?board={bq}")
    tx = (stt.get("transcripts") or {}).get(cid) or []
    assert any(m.get("who") == "user" for m in tx), stt
    print("chat transcript (user line) OK")
    # agent reply registration: appends agent line, clears pending
    call("POST", "/api/plugins/chat/reply",
         {"block": cid, "text": "pong"})
    stt = call("GET", f"/api/plugins/chat/status?board={bq}")
    tx = (stt.get("transcripts") or {}).get(cid) or []
    assert any(m.get("who") == "agent" and m.get("text") == "pong" for m in tx), stt
    assert cid not in (stt.get("pending") or {}), stt
    print("chat reply route (agent line + pending clear) OK")
    # reply_tag tracking: a block the agent pushed (reply_tag) sets reply_ts
    call("POST", f"/api/blocks?board={bq}",
         {"type": "text", "title": "agent",
          "data": {"reply_tag": "agent", "text": "pong"}})
    stt = call("GET", f"/api/plugins/chat/status?board={bq}")
    assert (stt.get("reply_ts") or 0) > 0, stt
    print("chat reply_tag tracking (reply_ts) OK")
    call("DELETE", f"/api/blocks/{cid}?board={bq}")

# cleanup
call("DELETE", f"/api/boards/{bq}")
print("cleaned up — SMOKE PASS")
