# Changelog

All notable changes to Visor. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/).

## [1.7.0] — 2026-09-20 (unreleased)

- **`chat` module: agent chat bridge.** `{"type": "chat"}` card = a
  transcript + input box on the canvas. Sending a message POSTs to a
  Hermes **webhook route** (signed Standard/generic-V2:
  `X-Webhook-Timestamp` + `X-Webhook-Signature-V2` = hex HMAC-SHA256 of
  `<ts>.<body>` — verified against `gateway/platforms/webhook.py`), which
  runs a full agent turn; the agent answers by pushing a normal block
  with `data.reply_tag` (default `agent`) via `visor_push`, and the card
  picks it up from board state — so replies work across agent restarts
  and every reply is a real, persisted block.
  - Config via env only: `VISOR_CHAT_URL` (route URL), `VISOR_CHAT_SECRET`
    (route secret), `VISOR_CHAT_REPLY` (tag, default `agent`). Unset URL =
    card renders with a "bridge not configured" hint; nothing crashes.
  - `GET /api/plugins/chat/status?board=` → per-board transcripts +
    pending sends; `GET /api/plugins/chat/config` → configured state.
  - Setup recipe in the README (enable `platforms.webhook`, create the
    route with `hermes webhook subscribe`, run the gateway, point the
    visor at it). End-to-end verified: card send → webhook 202 → agent
    turn → `agent` reply block on the board.
  - `visor.py chat [title]` CLI verb pushes a chat card.
  - `docker-compose.yml` passes the three `VISOR_CHAT_*` vars through.

## [1.6.1] — 2026-09-20

- **In-place content updates (no more card jumps).** SSE upserts that
  changed only a block's content (same type + width) now swap the card
  body in place (`swapBody`) instead of remove + rebuild + re-append —
  which replayed the pop animation and, worse, re-ran placement with a
  stale cached height so the card painted in its initial slot and then
  jumped to the final one on the next re-measure. `placeAll()` now only
  runs when the element's height actually changed.
- **Board deep links.** `?board=<id>` is honored at boot (switches after
  the initial load) — previously it was silently ignored.

## [1.6.0] — 2026-09-20

- **`live` module: auto-refreshing data blocks.** `{"type": "live",
  "data": {"url", "interval", "extract", "as"}}` polls a URL on an
  interval and renders as `stat` (value + delta), `text`, `table`, or
  `spark` (SVG line of history). Values are extracted by dot-path
  (`spend.total`) and written back into the stored block, so
  `/api/state`, exports, and SSE resyncs all carry the latest value.
  - Server-side scheduler (2s tick) with a **concurrent bounded fetch
    pool** — one dead/slow endpoint (10s connect timeout) can't starve
    the others. Stale state is pruned when blocks are deleted.
  - Failure handling: last value kept, `error` + consecutive `fails`
    recorded; the card shows the error and dims after 10 straight
    failures. `GET /api/plugins/live/status` lists poll targets + health.
  - Fetch safety: http(s) only, 10s timeout, 1MB response cap, optional
    `VISOR_LIVE_LOCAL_HOSTS` allowlist for networked installs.
  - `docker-compose.yml` adds `extra_hosts: host.docker.internal:
    host-gateway` so in-container modules can reach host services
    (live poll targets, the chat bridge, TTS endpoints).

## [1.5.0] — 2026-09-19

- **Plugin/module architecture.** Optional modules in `plugins/<name>/` now
  extend the core without touching it. A module provides a server half
  (`register()` → card types, plus optional `transform` push-rewrite and
  `route()` for `/api/plugins/<name>/*` endpoints) and a client half
  (`client.js` registering `window.VisorModules.<name>` with a `renderCard`).
  Enabled via `VISOR_MODULES=<comma list>` (empty = core only, unchanged).
  Missing or crashing modules are logged and skipped — the core still boots.
  See `docs/design/plugins-v1.5.md` for the full contract.
  - New open `GET /api/modules` manifest (name, client.js src, card_types).
  - New `GET /plugins/<name>/client.js` (served from the image, enabled only).
  - Block types are now a closed set: core 11 + whatever module types are
    enabled. POST/PATCH of a type no enabled module claims → 400. The MCP
    server and Hermes plugin read the manifest so they accept module types too.
  - `selftest` module ships as the canonical template + CI fixture.
- **`html` block scripts now run.** Card bodies are injected via `innerHTML`,
  which parses `<script>` but never executes it — so interactive mocks and
  live visualizations pushed as `html` blocks rendered as dead/blank boxes.
  `makeEl` now re-runs any `<script>` in a card body (html blocks and
  module-rendered cards). Verified live via CDP (a canvas+SVG mock went from
  0 rendered nodes to animating).
- **CI** gains a `smoke-modules` job (builds with `VISOR_MODULES=selftest`,
  asserts manifest/transform/route/client.js) and lints every module
  `client.js`. `scripts/smoke.py` takes `--base` / `--with-modules`.
- **Dockerfile/compose** copy `plugins/` into the image and pass
  `VISOR_MODULES` through.

## [1.4.6] — 2026-09-15

- **SSE dead on load (regression from 1.4.5).** The optional-auth wrapper
  replaced `window.EventSource` with an arrow function, but the client
  constructs it with `new EventSource(...)`, and an arrow is not a
  constructor — so `connect()` threw `TypeError: EventSource is not a
  constructor` at boot and the page sat at "connecting" forever (initial
  state rendered, but no live updates, no resync-on-reconnect). CI's
  `node --check` only validates syntax, so this shipped green. Fixed by
  making the wrapper a plain function. Caught by a live CDP screenshot —
  the one thing a syntax lint cannot see.

## [1.4.5] — 2026-09-15

- **Second self-audit (the surface left after the v1.4.3/4 hardening):**
  - **Malformed JSON body → silent 200.** `_body()` swallowed parse errors
    and returned `{}`, so `POST /api/blocks` with a broken body created an
    empty block and answered 200. Now 400 (`body is not valid JSON`), 413
    over 10MB, 400 if the top level isn't an object. Empty body (no
    Content-Length) still means "no body" → `{}`.
  - **No CORS preflight.** Browsers cross-origin writes died with 501
    (`BaseHTTPRequestHandler` has no `do_OPTIONS`). Added one, answering with
    the standard `Access-Control-Allow-*` headers (204).
  - **Empty board title stored as `''`.** `/api/meta` and board-rename
    accepted a blank title (the tab then showed the id fallback). Both now
    fall back to the board id, matching `POST /api/boards`.
- **Optional bearer auth (`VISOR_TOKEN`).** When set, `/api/*` and `/media/*`
  require `Authorization: Bearer <token>` (or `?token=*** for the SSE
  stream, which can't set headers). `/`, the page, and `/api/health` stay
  open. Unset = open, the default. The CLI, MCP server, and Hermes plugin all
  read the same env var and send the header; the web UI reads it from
  `?token=*** in the URL. This is the change that lets visor run on a
  public VPS instead of just LAN.
- **`GET /api/health`** — a stable, minimal, auth-free readiness endpoint for
  the compose healthcheck and for agents to probe, instead of everyone
  polling `/api/boards` and parsing the full list. The Hermes plugin's
  liveness probe now uses it (it used `/api/state`, which is auth-gated now).
- **Compose healthcheck** — `docker ps` now reports healthy/unhealthy (the
  server is the container, so a live server = healthy).
- **Smoke suite** gained a health check and a malformed-JSON-body 400
  regression.
- README: `VISOR_TOKEN` in the env table; "Design notes" now documents the
  optional auth and the deliberate permissive block-`type` behavior (an
  unknown type stores fine and renders as pretty-printed JSON — forward
  compat for new block types).

## [1.4.4] — 2026-09-15

- **Follow-up hardening (self-audit of the surface the external review
  didn't reach — CLI and PATCH path):**
  - **CLI hid server 4xx/5xx.** `post()` only caught `URLError`, and
    `HTTPError` is a subclass of it — so the server's helpful error bodies
    (like the v1.4.3 board-id 400) came out as *"server not reachable"*.
    A new `request(method, path, payload)` helper catches `HTTPError` first
    and prints the server's JSON error; every CLI call (boards, board
    rename/delete, list, links, unlink, rm) now routes through it.
    Previously `list`/`links` on a missing board dumped a raw Python
    traceback.
  - **PATCH accepted what POST rejected.** Updates stored raw `w`/`x`/`y` —
    `PATCH {"w":7}` persisted `w:7`. Now validated with the same `_num()`
    as POST (400, not stored); `{"x": null}` still clears a position.
  - **PATCH/DELETE fell through on a 400'd board id.** `_board_param()` now
    400s at the edge, but the link-PATCH and both DELETE branches kept
    going into the lock with `target=None`. All three return early now.
  - **CLI `image`/`video`/`audio` rejected URLs** (`stage()` demanded a local
    file) while the MCP server accepted them — same block, different
    front-ends, inconsistent behavior. URLs and `/media/` paths now pass
    through. An audio item without `label:src` crashed with a raw
    `ValueError`; now a clean one-line error.
  - Smoke suite gained a PATCH `w` validation regression (400 not stored /
    200 stored).
- Client re-audited (SSE upsert content/position diff, all `bodyHTML`
  renderers): no issues found — strict position compare, content diff
  includes `w`, all user content escaped.

## [1.4.3] — 2026-09-15

- **Hardening pass from external review.** A third-party review (clone +
  local run of server and MCP) verified six concrete bugs, all fixed here:
  - **Invalid board ids created orphaned boards.** `ensure_board()`
    auto-created a board for any id, even ones `get_board()`'s regex rejects
    — so `POST /api/blocks?board=BAD!!ID` succeeded, `/api/state` then 404'd,
    and the board was unreadable and undeletable. Board ids are now
    validated at the API edge in `_board_param()` (400 with a hint).
  - **Malformed `w`/`x`/`y` reset the connection.** `int(dict)` raised
    `TypeError` in the handler → silent connection reset (curl 000). Now a
    clean 400 with the offending value in the message (`_num()`). Caught
    during this pass: `_num()` must signal "400 sent" with a sentinel
    (`_NUM_ABORT`), not `None` — a missing `x`/`y` legitimately stores `None`,
    and conflating the two made *every* push without coordinates silently die.
  - **MCP error responses lost the request id.** `method not found` returned
    `id: null`, which strict JSON-RPC clients can't correlate — now echoes
    the request id.
  - **MCP `ensure_server()` was dead code** while the README claimed
    auto-start. Now called at the top of every `tools/call` (like the Hermes
    plugin); `VISOR_AUTO_START=0` still disables it, and the README/docs
    state the real behavior.
  - **Smoke test asserted nothing on its error cases.**
    `call_expect(code, ...)` ignored `code` and callers discarded the
    returned status — the advertised 409/400/404 coverage was a no-op that
    always passed. It now asserts the exact status, and the suite gained
    regressions for the two server bugs above (bad board id must 400 and not
    persist; malformed `w` must 400, not reset).
  - **`javascript:` URLs passed the markdown renderer.** The link rule had no
    scheme filter, so agent-pushed `[x](javascript:...)` rendered as a live
    link — a real issue for scraped/researched content. Now an allowlist
    (`https?:`, `mailto:`, `/`, `#`); anything else renders as plain text,
    and links get `rel="noopener noreferrer"`.
- **CI lints the client JS** (extracts the `<script>` blob, checks syntax)
  and compiles the Python files — the "extract and `node --check` by hand"
  step from the README is now automated.
- **`_docker_up()` returned a string** instead of the bool its annotation
  promised — now an explicit `bool()`.
- **README accuracy:** the `VISOR_HOME` default was documented as
  `~/code/visor`; it actually defaults to the repo root (parent of `mcp/`).
- Review observations intentionally **not** changed this round (documented
  trade-offs, not bugs): SSE queues unbounded (fine at stated scale),
  whole-store read/write per request (acknowledged in design notes), compose
  publishing `0.0.0.0` (opt-in for LAN is the user's call), `fsync` before
  `os.replace` (power-loss, not crash, protection).

## [1.4.2] — 2026-09-14

- **Docs synced to the shipped state.** CHANGELOG was missing the 1.4.1
  entry (the CI fix shipped in a separate commit but was never logged);
  README dev section now lists `scripts/smoke.py`, the CI workflow, and
  `data/.gitkeep` with a pointer to the 1.4.1 post-mortem, plus a note that
  a running container must be removed to pick up a rebuilt image; MCP
  `SERVER_INFO` bumped 1.4.0 → 1.4.1.

## [1.4.1] — 2026-09-14

- **Fixed (fresh-clone / CI): first-start crash on root-owned `data/`.**
  In a fresh checkout the `./data` directory doesn't exist, so Docker created
  the bind-mount as **root**; the container runs as `1000:1000` (the
  `VISOR_USER` default) and `server.py` crash-looped with
  `PermissionError: [Errno 13]` — the port accepted connections but reset
  them (curl exit 56). Caught because v1.4's push was the first real CI run,
  and reproduced locally before fixing. The fix: track `data/.gitkeep` so
  `git checkout` creates the dir owned by the cloner; CI exports
  `VISOR_USER="$(id -u):$(id -g)"`; compose sets `init: true` so crash-loops
  show up in `docker logs`. Verified with a full fresh-clone run
  (clone → build → up → smoke suite) and green GitHub CI on `7f200d2`.

## [1.4.0] — 2026-09-14

- **Connectors between blocks** (v1.4 headliner): board-level `links`
  (`{"from": blockId, "to": blockId, "label"?}`) render as curved, labeled
  indigo arrows on an SVG layer that pan/zooms with the canvas. Two creation
  paths: drag a card's edge dot onto another card in the UI, or push links
  via the API (`POST/PATCH/DELETE /api/links`, board-scoped). Arrows
  **auto-follow** their cards on drag, nudge, align, and remote (SSE) moves —
  the camera never moves. Click a link to select (red + ✕); Del/Backspace or
  the ✕ removes it. Links prune themselves when either endpoint block is
  deleted or the board is cleared. Both exports (HTML `E`, PNG `P`) include
  the connectors. `visor_push` (Hermes plugin + MCP) and the CLI gain link
  support: `links` param / `link`, `unlink`, `links` commands. New SSE op
  `links`. Old stores load unchanged (additive key).
- **Fixed**: in-place SSE position updates (a remote drag of a card) now
  re-route attached links — caught by a real-browser CDP test asserting path
  `d` changes with a byte-identical camera.
- **Smoke test** extended with link CRUD, error codes (409/400/404), label
  rename, and prune-on-block-delete coverage.

## [1.3.0] — 2026-09-14

- **PNG export**: whole-board snapshot as an image (P key or the tab's ▣), up to
  2x scale. Built by cloning live cards into an SVG `foreignObject` over the
  dot grid and rasterizing — no dependencies. (The SVG must be drawn from a
  `data:` URI; Chromium taints the canvas when an SVG with `foreignObject`
  loads from a `blob:` URL.)
- **Fixed horizontal-bar chart clipping**: the row-label gutter was a fixed
  42px and cut long words ("integration" → "gration"); it now sizes to the
  longest label.
- **Port env var renamed to `VISOR_PORT`** (Dockerfile, compose, server, CLI).
  `HOLO_PORT` still works as a legacy alias.
- **README**: generic "agent pushed me an audit" demo replaces TTS-pipeline
  examples; real screenshots added; `VISOR_USER` uid:gid troubleshooting note.
- **CI**: `scripts/smoke.py` + a GitHub Action that builds the container and
  exercises create-board / push / read / patch / page / SSE against it.

## [1.2.0] — 2026-09-13

- Minimap (M, click/drag to recenter, live viewport box).
- Card selection + arrow-key nudge (Shift = grid step, Esc deselects).
- Wide cards (`w: 1–3`) with per-card toggle button.
- Board export: any board → standalone offline HTML snapshot (E key or tab ↧).
- SSE auto-resync: events missed during a server restart are re-fetched.
- Chart hover tooltips; camera no longer reframes on in-place updates.

## [1.1.0] — 2026-09-13

- Multi-board: named tabs, create/rename/delete, per-board camera persistence.
- `audio` block: labeled rack with play-in-sequence (A/B comparisons).
- `video` block first-class.
- Docker deployment: stateless image, state in `./data` volume,
  `restart: unless-stopped`.
- `visor_push` registered as a first-class Hermes plugin tool;
  zero-dependency MCP server for Claude Code / OpenCode / Codex.

## [1.0.0] — 2026-09-13

- Initial release: SSE block store, infinite pan/zoom canvas, block types
  (section, text, todo, table, stat, chart, code, image, html), CLI.

