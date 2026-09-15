"""Visor plugin — live visual display surface for Hermes.

Registers the ``visor_push`` tool: push blocks (sections, markdown findings,
todos, tables, stats, code, images, raw HTML) to a holographic infinite-canvas
webpage. Blocks appear/update in place over SSE — the page is never rewritten.

The server (~/code/holo/server.py, port 8900) is auto-started on first use if
down, so the tool is always usable without setup.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

ROOT = Path(os.environ.get("VISOR_HOME") or (Path.home() / "code" / "visor"))
PORT = int(os.environ.get("VISOR_PORT", "8900"))
BASE = f"http://127.0.0.1:{PORT}"
_PUBLIC = f"http://localhost:{PORT}"
MEDIA = ROOT / "data" / "media"
SERVER = ROOT / "server.py"
COMPOSE = ROOT / "docker-compose.yml"

_BLOCK_TYPES = ["section", "text", "todo", "table", "stat", "chart", "code",
                "image", "audio", "video", "html"]

VISOR_PUSH_SCHEMA: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "visor_push",
        "description": (
            "Show the user gathered info visually on the visor — a live holographic "
            "infinite-canvas webpage (http://localhost:8900). Push blocks that appear "
            "immediately and update in place (no page rewrite). Use proactively when the "
            "user should SEE results: research findings, comparison tables, todo lists, "
            "KPI stats, code, images, diagrams. Block types: section (page header), "
            "text (markdown), todo (clickable checklist), table (headers+rows), stat "
            "(KPI cards), code, image (local file path), html (raw markup)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "blocks": {
                    "type": "array",
                    "minItems": 1,
                    "description": "Blocks to push, in display order.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "type": {"type": "string", "enum": _BLOCK_TYPES},
                            "title": {"type": "string", "description": "Block title (shown in header bar)."},
                            "w": {
                                "type": "integer",
                                "minimum": 1,
                                "maximum": 3,
                                "description": "Width in card columns (1 = normal, 2 = wide). Use for content that needs the extra room (big tables, wide code).",
                            },
                            "data": {
                                "type": "object",
                                "description": (
                                    "section: {text, subtitle}. text: {text: markdown}. "
                                    "todo: {items: [{text, done}]}. "
                                    "table: {headers: [..], rows: [[..]]}. "
                                    "stat: {items: [{value, label, tone: ok|warn|bad|cy, sub}]}. "
                                    "chart: {kind: bar|line|donut|hbar, labels: [..], values: [..] "
                                    "or series: [{name, values: [..]}] for multi/line}. "
                                    "code: {lang, code}. image: {src: local path or URL, caption}. "
                                    "audio: {items: [{src: local path or URL, label, note}]} — "
                                    "a rack of labeled players with a 'play all in sequence' button "
                                    "(ideal for A/B comparisons, e.g. voice clones). "
                                    "video: {src: local path or URL, caption}. "
                                    "html: {html} (raw markup)."
                                ),
                            },
                        },
                        "required": ["type"],
                    },
                },
                "page_title": {
                    "type": "string",
                    "description": "Optional board title (top-left brand + tab title).",
                },
                "clear": {
                    "type": "boolean",
                    "description": "Clear the board first (start a fresh canvas).",
                },
                "board": {
                    "type": "string",
                    "description": "Board (tab) to push to. Default 'main'. The client keeps one board visible at a time; use distinct boards for unrelated topics so they coexist.",
                },
                "focus": {
                    "type": "boolean",
                    "description": "After pushing, center the camera on the LAST block. Useful for long content (video) pushed to a busy board.",
                },
                "links": {
                    "type": "array",
                    "description": (
                        "Optional connectors between blocks — curved arrows drawn on the canvas "
                        "that follow their cards. Each: {from: blockId, to: blockId, label?}. "
                        "Ids are the block ids returned by this or a prior visor_push (the response "
                        "lists each pushed block's id). Push the blocks first, then the links."
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "from": {"type": "string", "description": "Source block id."},
                            "to": {"type": "string", "description": "Target block id."},
                            "label": {"type": "string", "description": "Optional label shown on the arrow."},
                        },
                        "required": ["from", "to"],
                    },
                },
            },
            "required": ["blocks"],
        },
    },
}


def _http(method: str, path: str, payload: Optional[Dict[str, Any]] = None,
          timeout: float = 10.0) -> Dict[str, Any]:
    data = json.dumps(payload or {}).encode()
    req = urllib.request.Request(
        BASE + path, data=data if method in ("POST", "PATCH") else None,
        headers={"Content-Type": "application/json"}, method=method,
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read() or b"{}")


def _reachable() -> bool:
    try:
        with urllib.request.urlopen(BASE + "/api/state", timeout=2.0) as r:
            return r.status == 200
    except Exception:
        return False


def _wait_ready(timeout: float) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _reachable():
            return
        time.sleep(0.3)
    raise RuntimeError(f"visor server did not come up at {BASE} in {timeout:.0f}s")


def _docker_up() -> bool:
    """True if the Docker daemon is reachable and a 'visor' container exists."""
    try:
        r = subprocess.run(["docker", "ps", "-a", "--filter", "name=^visor$",
                            "--format", "{{.Names}}"],
                           capture_output=True, text=True, timeout=10)
        return r.returncode == 0 and bool(r.stdout.strip())
    except Exception:
        return False


def _docker_start() -> None:
    """Start the visor container (after reboot / docker restart)."""
    r = subprocess.run(["docker", "start", "visor"], capture_output=True,
                       text=True, timeout=30)
    if r.returncode != 0:
        raise RuntimeError(f"docker start visor failed: {r.stderr.strip() or r.stdout.strip()}")


def _bare_start() -> None:
    """Fallback: run server.py directly (no Docker)."""
    MEDIA.mkdir(exist_ok=True)
    log = open(ROOT / "visor.log", "ab")
    subprocess.Popen(
        [sys.executable, str(SERVER)], cwd=str(ROOT),
        env={**os.environ, "VISOR_PORT": str(PORT), "HOLO_PORT": str(PORT)},
        stdout=log, stderr=log, start_new_session=True,
    )


def ensure_server(timeout: float = 20.0) -> None:
    """Auto-start the visor server if it is down. Idempotent.

    Prefers the Docker container (`visor`, restart: unless-stopped — survives
    reboots). Falls back to a bare python3 process when Docker is unavailable.
    """
    if _reachable():
        return
    if not SERVER.exists():
        raise RuntimeError(f"visor server not found at {SERVER}")
    if _docker_up():
        try:
            _docker_start()
        except Exception as e:
            logger.warning("visor: docker start failed (%s); falling back to bare python", e)
            _bare_start()
    else:
        _bare_start()
    _wait_ready(timeout)


def _check_available() -> bool:
    """Gate: server reachable, or startable (server file present)."""
    if _reachable():
        return True
    return SERVER.exists()


def _stage_image(path: str) -> str:
    src = Path(path).expanduser()
    if src.exists():
        MEDIA.mkdir(exist_ok=True)
        name = uuid.uuid4().hex[:8] + src.suffix
        shutil.copyfile(src, MEDIA / name)
        return f"/media/{name}"
    return path  # already a URL or /media path — pass through


def _handle_visor_push(args: Dict[str, Any], **_kw: Any) -> str:
    # **_kw: the Hermes dispatcher may inject extra kwargs (e.g. task_id) that
    # are not part of the tool schema — absorb them instead of TypeError.
    try:
        ensure_server()
        out: Dict[str, Any] = {"url": _PUBLIC, "blocks": []}
        board = str(args.get("board") or "main").strip() or "main"
        focus = bool(args.get("focus"))
        q = f"?board={urllib.parse.quote(board)}"
        if args.get("page_title"):
            _http("POST", f"/api/meta{q}", {"title": args["page_title"]})
            out["page_title"] = args["page_title"]
        if args.get("clear"):
            _http("POST", f"/api/clear{q}", {})
            out["cleared"] = True
        blocks = args.get("blocks", [])
        for i, b in enumerate(blocks):
            btype = b.get("type")
            if btype not in _BLOCK_TYPES:
                out["blocks"].append({"error": f"bad type: {btype!r}",
                                      "valid": _BLOCK_TYPES})
                continue
            data = dict(b.get("data") or {})
            # LLMs sometimes nest the content under the type key — unwrap it
            if btype in data and isinstance(data[btype], dict):
                data = dict(data[btype])
            if btype == "image" and data.get("src"):
                data["src"] = _stage_image(data["src"])
            if btype == "video" and data.get("src"):
                data["src"] = _stage_image(data["src"])
            if btype == "audio":
                items = []
                for it in data.get("items", []):
                    it = dict(it)
                    if it.get("src"):
                        it["src"] = _stage_image(it["src"])
                    items.append(it)
                data["items"] = items
            if btype == "section":
                data.setdefault("text", b.get("title", ""))
            is_last = i == len(blocks) - 1
            payload = {"type": btype, "title": b.get("title", ""), "data": data}
            if b.get("w"):
                payload["w"] = int(b["w"])
            if focus and is_last:
                payload["focus"] = True
            res = _http("POST", f"/api/blocks{q}", payload)
            out["blocks"].append({"id": res.get("id"), "type": btype})
        for l in args.get("links", []) or []:
            src, dst = l.get("from"), l.get("to")
            if not src or not dst:
                out.setdefault("links", []).append({"error": "need from and to"})
                continue
            try:
                lr = _http("POST", f"/api/links{q}",
                           {"from": src, "to": dst, "label": l.get("label", "")})
                out.setdefault("links", []).append({"id": lr.get("id")})
            except Exception as e:
                out.setdefault("links", []).append(
                    {"error": f"{src}->{dst}: {e}"})
        out["board"] = board
        out["note"] = (
            f"Blocks are live on board {board!r} at {BASE}. Switch boards via the "
            "tabs in the top-left of the page, or push to another board with the "
            "\"board\" argument. To update one block in place later, PATCH "
            f"/api/blocks/<id>?board={board}."
        )
        return json.dumps(out, indent=2)
    except Exception as e:  # keep the tool total — report, don't crash the turn
        return json.dumps({"ok": False, "error": str(e)})


def register(ctx) -> None:
    ctx.register_tool(
        name="visor_push",
        toolset="visor",
        schema=VISOR_PUSH_SCHEMA,
        handler=_handle_visor_push,
        check_fn=_check_available,
        description="Push live visual blocks to the visor canvas.",
        emoji="🛰️",
    )
