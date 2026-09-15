#!/usr/bin/env python3
"""Local copy of the CI smoke test — run against a live visor on :8900."""
import json, urllib.request, urllib.parse

BASE = "http://127.0.0.1:8900"

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

# SSE stream reachable
import socket
s = socket.create_connection(("127.0.0.1", 8900), timeout=3)
s.sendall(b"GET /api/events HTTP/1.1\r\nHost: x\r\nAccept: text/event-stream\r\n\r\n")
data = s.recv(200).decode(errors="replace")
assert "event:" in data or "200" in data, data[:100]
print("sse OK")
s.close()

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
call("DELETE", f"/api/blocks/{b2['id']}?board={bq}")
stt = call("GET", f"/api/state?board={bq}")
assert stt.get("links", []) == [], stt
print("link prune on block delete OK")

# cleanup
call("DELETE", f"/api/boards/{bq}")
print("cleaned up — SMOKE PASS")
