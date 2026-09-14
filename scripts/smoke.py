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

# cleanup
call("DELETE", f"/api/boards/{bq}")
print("cleaned up — SMOKE PASS")
