#!/usr/bin/env python3
"""visor CLI — push blocks to the visor.

Global flag:
  --board <id>     target board (default: main)

Blocks:
  visor.py title "Board title"
  visor.py section "Header" ["subtitle"]
  visor.py text "Title" "markdown body"
  visor.py todo "Title" "item1;item2;item3"   # prefix [x] to mark done
  visor.py table "Title" "h1|h2|h3;row1a|row1b|row1c;row2a|..."
  visor.py stat "Title" "value:label:tone[:sub];..."
  visor.py chart "Title" --kind bar|line|donut|hbar --labels "a;b;c" --values "10;20;30"
  visor.py code "Title" --lang py "code text"
  visor.py image "Title" /path/to/img.png ["caption"]
  visor.py video "Title" /path/to/clip.mp4 ["caption"]
  visor.py audio "Title" "label:src;label:src"   # per-item note via label|note
  visor.py html "Title" "<div>...</div>"

Boards:
  visor.py boards                       # list boards
  visor.py board new "Name"             # create a board
  visor.py board rename <id> "Name"     # rename
  visor.py board delete <id>            # delete (server refuses the last one)

Blocks (cont.):
  visor.py list
  visor.py rm <id>
  visor.py clear [type]
"""
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

PORT = int(os.environ.get("HOLO_PORT", os.environ.get("VISOR_PORT", "8900")))
BASE = f"http://127.0.0.1:{PORT}"


def post(path, payload=None):
    data = json.dumps(payload or {}).encode()
    req = urllib.request.Request(BASE + path, data=data,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.URLError as e:
        sys.exit(f"visor server not reachable at {BASE}: {e}")


def media_dir():
    d = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "media")
    os.makedirs(d, exist_ok=True)
    return d


def stage(src):
    import shutil, uuid
    src = os.path.expanduser(src)
    if not os.path.exists(src):
        sys.exit(f"no such file: {src}")
    ext = os.path.splitext(src)[1]
    name = uuid.uuid4().hex[:8] + ext
    shutil.copyfile(src, os.path.join(media_dir(), name))
    return f"/media/{name}"


def main():
    a = sys.argv[1:]
    board = "main"
    if "--board" in a:
        i = a.index("--board")
        board = a[i + 1]
        del a[i:i + 2]
    bq = f"?board={urllib.parse.quote(board)}"

    if not a:
        sys.exit(__doc__)
    cmd, rest = a[0], a[1:]

    if cmd == "boards":
        with urllib.request.urlopen(BASE + "/api/boards", timeout=10) as resp:
            st = json.loads(resp.read())
        for b in st["boards"]:
            mark = "*" if b["id"] == board else " "
            print(f"{mark} {b['id']:20} {b['blocks']:>3} blocks  {b['title']}")
        return
    if cmd == "board":
        sub, r2 = rest[0], rest[1:]
        if sub == "new":
            r = post("/api/boards", {"title": r2[0] if r2 else "board"})
            print(f"created board {r['id']!r}")
        elif sub == "rename":
            post(f"/api/boards/{r2[0]}", {"title": r2[1]})
            print(f"renamed {r2[0]} -> {r2[1]}")
        elif sub == "delete":
            req = urllib.request.Request(BASE + f"/api/boards/{r2[0]}", method="DELETE")
            urllib.request.urlopen(req, timeout=10)
            print(f"deleted {r2[0]}")
        else:
            sys.exit(__doc__)
        return

    if cmd == "title":
        post(f"/api/meta{bq}", {"title": rest[0] if rest else "Visor"})
    elif cmd == "section":
        post(f"/api/blocks{bq}", {"type": "section", "data": {"text": rest[0],
                                                          "subtitle": rest[1] if len(rest) > 1 else ""}})
    elif cmd == "text":
        post(f"/api/blocks{bq}", {"type": "text", "title": rest[0] if rest else "",
                             "data": {"text": rest[1] if len(rest) > 1 else ""}})
    elif cmd == "todo":
        items = []
        for it in rest[1].split(";"):
            it = it.strip()
            if not it:
                continue
            done = it.startswith("[x]")
            if it.startswith("[x]") or it.startswith("[ ]"):
                it = it[3:].strip()
            items.append({"text": it, "done": done})
        post(f"/api/blocks{bq}", {"type": "todo", "title": rest[0],
                             "data": {"items": items}})
    elif cmd == "table":
        parts = rest[1].split(";")
        headers = [c.strip() for c in parts[0].split("|")]
        rows = [[c.strip() for c in p.split("|")] for p in parts[1:] if p.strip()]
        post(f"/api/blocks{bq}", {"type": "table", "title": rest[0],
                             "data": {"headers": headers, "rows": rows}})
    elif cmd == "stat":
        items = []
        for it in rest[1].split(";"):
            bits = [x.strip() for x in it.split(":")]
            if len(bits) < 2:
                continue
            items.append({"value": bits[0], "label": bits[1],
                          "tone": bits[2] if len(bits) > 2 else "cy",
                          "sub": bits[3] if len(bits) > 3 else ""})
        post(f"/api/blocks{bq}", {"type": "stat", "title": rest[0],
                             "data": {"items": items}})
    elif cmd == "chart":
        kind, labels, values = "bar", [], []
        def grab(flag):
            nonlocal labels, values, rest
            if flag in rest:
                i = rest.index(flag)
                v = rest[i+1]
                rest = rest[:i] + rest[i+2:]
                return v
            return None
        k = grab("--kind"); kind = k or "bar"
        lb = grab("--labels"); labels = [x.strip() for x in lb.split(";")] if lb else []
        vv = grab("--values"); values = [float(x) if '.' in x else int(x) for x in vv.split(";")] if vv else []
        post(f"/api/blocks{bq}", {"type": "chart", "title": rest[0],
                             "data": {"kind": kind, "labels": labels, "values": values}})
    elif cmd == "code":
        lang = ""
        if "--lang" in rest:
            i = rest.index("--lang")
            lang = rest[i + 1]
            rest = rest[:i] + rest[i + 2:]
        post(f"/api/blocks{bq}", {"type": "code", "title": rest[0],
                             "data": {"lang": lang, "code": rest[1] if len(rest) > 1 else ""}})
    elif cmd == "image":
        post(f"/api/blocks{bq}", {"type": "image", "title": rest[0],
                             "data": {"src": stage(rest[1]),
                                      "caption": rest[2] if len(rest) > 2 else ""}})
    elif cmd == "video":
        post(f"/api/blocks{bq}", {"type": "video", "title": rest[0],
                             "data": {"src": stage(rest[1]),
                                      "caption": rest[2] if len(rest) > 2 else ""}})
    elif cmd == "audio":
        items = []
        for it in rest[1].split(";"):
            it = it.strip()
            if not it:
                continue
            label, src = (x.strip() for x in it.split(":", 1))
            items.append({"label": label, "src": stage(src)})
        post(f"/api/blocks{bq}", {"type": "audio", "title": rest[0],
                             "data": {"items": items}})
    elif cmd == "html":
        post(f"/api/blocks{bq}", {"type": "html", "title": rest[0],
                             "data": {"html": rest[1] if len(rest) > 1 else ""}})
    elif cmd == "list":
        with urllib.request.urlopen(BASE + f"/api/state{bq}", timeout=10) as r:
            st = json.loads(r.read())
        for b in st["blocks"]:
            t = b.get("title") or (b.get("data") or {}).get("text", "")[:40]
            print(f"{b['id']}  {b['type']:8}  {b['seq']:>3}  {t}")
        if not st["blocks"]:
            print("(empty)")
    elif cmd == "rm":
        req = urllib.request.Request(BASE + f"/api/blocks/{rest[0]}{bq}", method="DELETE")
        urllib.request.urlopen(req, timeout=10)
    elif cmd == "clear":
        post(f"/api/clear{bq}", {"type": rest[0]} if rest else {})
    else:
        sys.exit(__doc__)
    time.sleep(0.15)


if __name__ == "__main__":
    main()
