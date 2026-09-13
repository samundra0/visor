# Agent setup — point your agent at a running visor

Give your agent this section. It's written to be pasted as-is into a system
prompt, AGENTS.md, or a session opener.

---

You have a **visor** — a live infinite-canvas webpage at `http://localhost:8900`
that the user watches. Use it proactively whenever the user should *see*
information you gathered: research findings, comparisons, tables, stats,
checklists, code, images, audio/video (e.g. A/B voice samples), charts.
Don't wait to be asked — if you just did substantial work and there's a
watchable result, push it.

## How to push

The visor HTTP API is at `http://127.0.0.1:8900` (same host). If `GET
/api/state` fails, the server is down — start it:
`cd <visor dir> && docker compose up -d` (or `python3 server.py`), wait for
`/api/state` to answer, then push.

**Boards** are tabs. One board per topic; don't mix unrelated topics.
Default board is `main`. List with `GET /api/boards`, create with
`POST /api/boards {"title":"..."}`. If a board for your topic exists, reuse it.
For a fresh take on a topic, `POST /api/clear?board=<id>` first.

Push blocks in order:

```
POST /api/blocks?board=<id>
Content-Type: application/json

{"type": "<block>", "title": "short card title", "data": {...},
 "focus": true}        # focus=true → camera centers on this block (last block only)
```

Set the board title once: `POST /api/meta?board=<id> {"title":"Topic"}`.

### Block types

- `section` — `{"text": "Header", "subtitle": "context line"}`. Always start a
  board with one.
- `text` — `{"text": "markdown"}` (bold, code, lists, links, ### headers).
- `todo` — `{"items": [{"text": "…", "done": false}]}`. The user can click
  items in the browser to toggle them.
- `table` — `{"headers": ["a","b"], "rows": [["1","2"]]}`. Cell values are
  markdown.
- `stat` — `{"items": [{"value": "42", "label": "users", "tone": "ok", "sub": "optional"}]}`.
  tone: `ok|warn|bad|cy`.
- `chart` — `{"kind": "bar|line|donut|hbar", "labels": [...], "values": [...]}`
  or `"series": [{"name": "...", "values": [...]}]` for multi/line. Use charts
  over tables when the point is magnitude or trend.
- `code` — `{"lang": "python", "code": "..."}`.
- `image` — `{"src": "/abs/path.png", "caption": "..."}`. The server serves
  files from its `data/media/` dir: **copy** the file there first (any name),
  then use `{"src": "/media/<name>"}`. URLs pass through as-is.
- `audio` — `{"items": [{"src": "/media/a.wav", "label": "what this is", "note": "optional"}]}`.
  Renders labeled players + a "play all in sequence" button. Use for A/B
  comparisons (e.g. voice clones: reference clip vs fresh clone line).
- `video` — `{"src": "/media/clip.mp4", "caption": "..."}` (same media rule).
  Add `"focus": true` so the camera centers on it.
- `html` — `{"html": "<div>…</div>"}`. Escape hatch only; prefer typed blocks.

Every block also accepts `"x": n, "y": n` (world coords) and `"w": 1|2|3`
(column width). Omit → the client auto-lays out on a grid. Use `"w": 2` for
content that needs the room — wide tables, long code, anything that would
crowd a single column.

### Update in place

`PATCH /api/blocks/<id>?board=<id>` with the fields to change (e.g.
`{"data": {"items": [...]}}`). The card updates in place; the camera doesn't
move. This is how you keep a live counter or refresh a table while working.
`DELETE /api/blocks/<id>?board=<id>` removes a card.

## Style rules

- One section header + the findings that read best in the *right* block type.
  Never dump raw JSON as `text`.
- Prefer charts for numbers, tables for lookup data, todos for next actions.
- Keep board titles short (they're tabs).
- Push incrementally if the work is long: findings as you get them, then
  update. The page never rewrites, so partial boards are fine.
- When done with a session's board, leave it — the user may come back. Use
  `clear` only when re-doing the same topic.

## Quick example (verify this works, then show the user)

```sh
curl -s -X POST 'http://127.0.0.1:8900/api/meta?board=demo' -d '{"title":"Demo"}'
curl -s -X POST 'http://127.0.0.1:8900/api/blocks?board=demo' \
  -d '{"type":"section","data":{"text":"Visor works","subtitle":"pushed by your agent"}}'
curl -s -X POST 'http://127.0.0.1:8900/api/blocks?board=demo' \
  -d '{"type":"stat","data":{"items":[{"value":"1","label":"boards","tone":"ok"}]}}'
curl -s -X POST 'http://127.0.0.1:8900/api/blocks?board=demo' \
  -d '{"type":"chart","data":{"kind":"bar","labels":["a","b","c"],"values":[3,7,5]}}'
```

Then tell the user: *open http://localhost:8900, the “Demo” tab*.

---

That's the whole contract. Everything else (pan/zoom, drag, grid, tabs) is for
the human watching, not the agent.
