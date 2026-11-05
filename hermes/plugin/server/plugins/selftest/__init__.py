"""selftest — the canonical minimal visor module.

Serves three purposes:
  1. The template new modules copy (server + client, both halves).
  2. The CI smoke fixture (scripts/smoke.py --with-modules selftest).
  3. Proof the plugin convention works end-to-end.

Server contract (what a module MAY define):
  register(ctx) -> metadata dict:
    {"description": "...", "card_types": {"selftest": "one-liner"}}
  transform(board_id, block) -> block|None   # rewrite pushes before storage
  route(method, sub_path, query, body, send) # handle /api/plugins/selftest/<sub>
Client contract (client.js):
  window.VisorModules.selftest = {cardTypes: [...], renderCard, onSSE?, onBoard?}
"""
import json
import os
import time

NAME = "selftest"
_HITS = 0


def register(ctx):
    global _HITS
    os.makedirs(ctx["plugin_dir"](NAME), exist_ok=True)
    return {
        "description": "self-test module: card type + transform + route",
        "card_types": {"selftest": "transform stamps data.touched"},
        "ctx": ctx,
    }


def transform(board_id, blk):
    """Tag every selftest push so the smoke test can assert transforms run."""
    if blk.get("type") == "selftest":
        data = dict(blk.get("data") or {})
        data["touched"] = True
        data["board"] = board_id
        blk = dict(blk, data=data)
    return blk


def route(method, sub_path, query, body, send):
    global _HITS
    if method == "GET" and sub_path == "ping":
        _HITS += 1
        send(200, {"pong": True, "hits": _HITS, "ts": time.time()})
    else:
        send(404, {"error": f"no route {method} {sub_path!r}"})
