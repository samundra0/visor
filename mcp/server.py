#!/usr/bin/env python3
"""Visor MCP server — zero-dependency (Python stdlib only), JSON-RPC 2.0 over stdio.

Point any MCP-capable agent at this file and it gets a `visor_push` tool:

  Claude Code:   claude mcp add visor -- python3 /path/to/visor/mcp/server.py
  OpenCode:      opencode.json -> "mcp": {"visor": {"type":"local","command":["python3","/path/.../mcp/server.py"],"enabled":true}}
  Codex CLI:     ~/.codex/config.toml -> [mcp_servers.visor] command="python3" args=["/path/.../mcp/server.py"]

Env:
  VISOR_URL   base URL (default http://127.0.0.1:8900)
  VISOR_HOME  repo root, for staging local media into <home>/data/media (default: parent of mcp/)
  VISOR_AUTO_START=0  disable the automatic `docker start visor` on first failure
"""
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

BASE = os.environ.get("VISOR_URL", "http://127.0.0.1:8900").rstrip("/")
HOME = os.environ.get("VISOR_HOME") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MEDIA_DIR = os.path.join(HOME, "data", "media")
AUTO_START = os.environ.get("VISOR_AUTO_START", "1") != "0"
SERVER_INFO = {"name": "visor", "version": "1.4.0"}
PROTOCOL_FALLBACK = "2025-06-18"

BLOCK_TYPES = ["section", "text", "todo", "table", "stat", "chart", "code",
               "image", "audio", "video", "html"]

DATA_DOC = (
    "Block content — put these fields DIRECTLY in this object (NOT nested under "
    "the type name). section: {text, subtitle}. text: {text: markdown}. "
    "todo: {items: [{text, done}]}. table: {headers: [..], rows: [[..]]}. "
    "stat: {items: [{value, label, tone: ok|warn|bad|cy, sub}]}. "
    "chart: {kind: bar|line|donut|hbar, labels: [..], values: [..] "
    "or series: [{name, values: [..]}]}. code: {lang, code}. "
    "image: {src: local path or URL, caption}. "
    "audio: {items: [{src: local path or URL, label, note}]} — a rack of labeled "
    "players with a play-in-sequence button (A/B comparisons, e.g. voice clones). "
    "video: {src: local path or URL, caption}. html: {html}."
)

TOOL_PUSH = {
    "name": "visor_push",
    "description": (
        "Push info to the Visor live canvas the user watches: named boards (tabs), "
        "in-place SSE updates, charts with tooltips, audio/video playback, minimap. "
        "Use it to SHOW gathered info (research, comparisons, todos, stats, media) "
        "instead of only narrating it in chat. Local media files are auto-staged."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "blocks": {
                "type": "array", "minItems": 1,
                "description": "Blocks to push, in display order.",
                "items": {
                    "type": "object",
                    "properties": {
                        "type": {"type": "string", "enum": BLOCK_TYPES},
                        "title": {"type": "string", "description": "Block title (header bar)."},
                        "w": {"type": "integer", "minimum": 1, "maximum": 3,
                              "description": "Width in card columns (1 = normal, 2 = wide)."},
                        "data": {"type": "object", "description": DATA_DOC},
                    },
                    "required": ["type"],
                },
            },
            "board": {"type": "string",
                      "description": "Board (tab) to push to. Default 'main'. One board per topic."},
            "page_title": {"type": "string", "description": "Board title (top-left tab)."},
            "clear": {"type": "boolean", "description": "Clear the board first."},
            "focus": {"type": "boolean",
                      "description": "Center the camera on the last pushed block (videos on busy boards)."},
            "links": {
                "type": "array",
                "description": ("Optional connectors between blocks — curved arrows drawn on the "
                                 "canvas that follow their cards. Each: {from: blockId, to: blockId, "
                                 "label?}. Ids are the block ids returned by a prior visor_push "
                                 "(the response lists each pushed block's id). Push the blocks "
                                 "first, then the links."),
                "items": {
                    "type": "object",
                    "properties": {
                        "from": {"type": "string", "description": "Source block id."},
                        "to": {"type": "string", "description": "Target block id."},
                        "label": {"type": "string", "description": "Optional label on the arrow."},
                    },
                    "required": ["from", "to"],
                },
            },
        },
        "required": ["blocks"],
    },
}

TOOL_BOARDS = {
    "name": "visor_boards",
    "description": "List Visor boards, or create a new one ({\"create\": \"Name\"}).",
    "inputSchema": {
        "type": "object",
        "properties": {
            "create": {"type": "string", "description": "If set, create a board with this title."},
        },
    },
}


def _http(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data,
                                 headers={"Content-Type": "application/json"}, method=method)
    with urllib.request.urlopen(req, timeout=8) as r:
        return json.loads(r.read() or b"{}")


def ensure_server():
    """Reachable? If not, try to start the Docker container (like the Hermes plugin)."""
    try:
        _http("GET", "/api/boards")
        return
    except Exception:
        pass
    if AUTO_START:
        try:
            subprocess.run(["docker", "start", "visor"], timeout=15,
                           capture_output=True)
        except Exception:
            pass
        for _ in range(10):
            try:
                _http("GET", "/api/boards")
                return
            except Exception:
                time.sleep(0.5)
    raise RuntimeError(
        f"Visor server not reachable at {BASE}. Start it: "
        f"`cd <visor-repo> && docker compose up -d` (or `python3 server.py`).")


def stage(src):
    src = os.path.expanduser(src)
    if os.path.exists(src):
        os.makedirs(MEDIA_DIR, exist_ok=True)
        name = uuid.uuid4().hex[:8] + os.path.splitext(src)[1]
        shutil.copyfile(src, os.path.join(MEDIA_DIR, name))
        return f"/media/{name}"
    return src  # URL or path the server will serve as-is


def tool_push(args):
    board = (args.get("board") or "main").strip()
    q = "?board=" + urllib.parse.quote(board)
    out = {"board": board, "blocks": []}
    if args.get("page_title"):
        _http("POST", f"/api/meta{q}", {"title": args["page_title"]})
        out["page_title"] = args["page_title"]
    if args.get("clear"):
        _http("POST", f"/api/clear{q}", {})
        out["cleared"] = True
    blocks = args.get("blocks") or []
    for i, b in enumerate(blocks):
        btype = b.get("type")
        if btype not in BLOCK_TYPES:
            out["blocks"].append({"error": f"bad type {btype!r}", "valid": BLOCK_TYPES})
            continue
        data = dict(b.get("data") or {})
        # LLMs sometimes nest the content under the type key
        # ({"section": {...}}) — unwrap that shape so it renders correctly.
        if btype in data and isinstance(data[btype], dict):
            data = dict(data[btype])
        if btype in ("image", "video") and data.get("src"):
            data["src"] = stage(data["src"])
        if btype == "audio":
            data["items"] = [dict(it, src=stage(it["src"])) if it.get("src") else it
                             for it in data.get("items", [])]
        if btype == "section":
            data.setdefault("text", b.get("title", ""))
        payload = {"type": btype, "title": b.get("title", ""), "data": data}
        if b.get("w"):
            payload["w"] = int(b["w"])
        if args.get("focus") and i == len(blocks) - 1:
            payload["focus"] = True
        res = _http("POST", f"/api/blocks{q}", payload)
        out["blocks"].append({"id": res.get("id"), "type": btype})
    for l in args.get("links") or []:
        src, dst = l.get("from"), l.get("to")
        if not src or not dst:
            out.setdefault("links", []).append({"error": "need from and to"})
            continue
        try:
            lr = _http("POST", f"/api/links{q}",
                       {"from": src, "to": dst, "label": l.get("label", "")})
            out.setdefault("links", []).append({"id": lr.get("id")})
        except Exception as e:
            out.setdefault("links", []).append({"error": f"{src}->{dst}: {e}"})
    out["note"] = (f"Live on board {board!r} at {BASE} — the user sees it on the "
                   "Visor canvas (tabs top-left). Update one block later: "
                   f"PATCH /api/blocks/<id>?board={board}.")
    return out


def tool_boards(args):
    if args.get("create"):
        r = _http("POST", "/api/boards", {"title": args["create"]})
        return {"created": r}
    return _http("GET", "/api/boards")


def call_tool(name, args):
    if name == "visor_push":
        return tool_push(args)
    if name == "visor_boards":
        return tool_boards(args)
    raise ValueError(f"unknown tool {name!r}")


# ---------------- MCP (JSON-RPC 2.0 over stdio) ----------------

def handle(req):
    """Return a response dict, or None for notifications."""
    rid, method = req.get("id"), req.get("method")
    params = req.get("params") or {}
    if method == "initialize":
        return {"protocolVersion": params.get("protocolVersion", PROTOCOL_FALLBACK),
                "capabilities": {"tools": {}}, "serverInfo": SERVER_INFO}
    if method in ("notifications/initialized", "notifications/cancelled"):
        return None
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": [TOOL_PUSH, TOOL_BOARDS]}
    if method == "tools/call":
        name, args = params.get("name"), params.get("arguments") or {}
        try:
            result = call_tool(name, args)
            return {"content": [{"type": "text", "text": json.dumps(result, indent=2)}]}
        except Exception as e:
            return {"content": [{"type": "text", "text": f"error: {e}"}], "isError": True}
    if method == "resources/list":
        return {"resources": []}
    if method == "prompts/list":
        return {"prompts": []}
    if rid is None:
        return None  # unknown notification
    return None, {"code": -32601, "message": f"method not found: {method}"}


def main():
    # errors go to stderr so stdout stays a clean JSON-RPC channel
    try:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                req = json.loads(line)
            except json.JSONDecodeError:
                continue
            out = handle(req)
            if out is None:
                continue
            if isinstance(out, tuple):
                rid, err = out
                out = {"jsonrpc": "2.0", "id": rid, "error": err}
            else:
                out = {"jsonrpc": "2.0", "id": req.get("id"), "result": out}
            sys.stdout.write(json.dumps(out) + "\n")
            sys.stdout.flush()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
