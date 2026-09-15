# Changelog

All notable changes to Visor. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/).

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

