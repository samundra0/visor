# Visor v1.5+ — Plugin Architecture & Optional Modules

Date: 2026-09-19
Status: approved for implementation (user sign-off on design, face = **orb**)

## Context

Visor v1.4.6 is a monolith: one `server.py` (616 lines) + one `index.html`
(1320 lines), zero dependencies, one Docker container. This works and must
keep working.

New goals (in priority order):

1. **Optional modules** that attach on demand and can be broken/disabled
   without affecting core (canvas, SSE, exports, push tool, boards, links).
2. **Live data** — auto-refreshing blocks from URLs/endpoints.
3. **Chat bridge** — a card on the canvas that talks to the connected agent.
4. **Voice/face** — mic input (STT) + agent replies as audio (TTS), with an
   orb visualization that visibly talks.

Constraints:

- One stack: `git clone && docker compose up`. No pip installs, no build
  step, no separate services by default.
- Modules ship inside the same image; enabled by env var, off = invisible.
- Core stays a monolith. We are adding a *loading convention*, not a
  framework.

## Prior discovery (already merged into core, pre-v1.5)

- **`html` block scripts never executed**: `makeEl` injects card bodies via
  `el.innerHTML`, which parses `<script>` but does not run it. Interactive
  mocks pushed by agents rendered as dead boxes. Fix (in this change set):
  `makeEl` re-runs html-block scripts post-insertion (replaces the parsed
  node with a fresh `document.createElement('script')`). Verified live via
  CDP: SVG node count 0 → 166, visual confirmed.
- The design-prototypes skill note ("inline `<script>` works in html blocks")
  is now true; `onclick` attributes always worked.

## 1. Plugin convention (v1.5.0, core)

### Layout

```
plugins/
  <name>/
    __init__.py     # server side; REQUIRED
    client.js       # client side; REQUIRED for modules with card types
    README.md       # one-paragraph what/why + config knobs
```

All modules live in-repo (baked into the image). No external discovery,
no `pip install -e`, no network fetching of code.

### Server contract (`plugins/<name>/__init__.py`)

```python
NAME = "chat"                      # must match directory name

def register(app, ctx) -> dict:
    """Called once at startup, per enabled module.
    app: the core HTTP server instance (exposes helper methods)
    ctx: {store, save, emit(board, op, data), config}
    Returns metadata: {"card_types": {"chat": "<desc>"}, "description": "..."}
    """
```

Core API surface given to modules (small, documented):

- `ctx.save(path, data)` / `ctx.load(path, default)` — JSON persistence
  under `data/plugins/<name>/`.
- `ctx.emit(board, op, data)` — push an SSE event through the core bus
  (ops: `upsert`, `links`, `meta`, or a module-namespaced
  `module:<name>` op the core forwards verbatim).
- `app.handle(method, path, handler)` — register an endpoint under
  `/api/plugins/<name>/<path>` (auth-gated like the rest of `/api/*`).
- `ctx.on_upsert(fn)` — optional server-side transform: `fn(block) -> block`
  runs before a block of a module-declared type is stored. This is how the
  chat module appends replies instead of replacing card state.

### Enablement

- `VISOR_MODULES=live,chat,voice` (comma list; compose passthrough; default
  empty = core only).
- Invalid/missing module name → startup log line, skipped, core still boots.
- A module whose `register()` throws → logged with traceback, module
  disabled, core still boots. (Expect-it-to-break, contained.)
- Disabled module: `/api/plugins/<name>/*` → 404; its block types → 400 on
  push (same as today's unknown types); client never loads its JS.

### Core client changes (index.html)

- New core endpoint `GET /api/modules` → `[{name, src: "/plugins/<name>/client.js"}]` (open route, no auth — it's just a manifest).
- `GET /plugins/<name>/client.js` served by core from the repo dir.
- Boot sequence: fetch `/api/modules` → inject `<script src>` for each →
  `boot()`. Module scripts are small and same-origin; failure to load one
  (404 on disk mismatch) logs and continues.
- Client contract for `client.js`:

  ```js
  window.VisorModules = window.VisorModules || {};
  window.VisorModules.chat = {
    cardTypes: ["chat"],                    // block types this module renders
    renderCard: (b) => `<div>...</div>`,    // returns HTML string for bodyHTML()
    onSSE?: (op, msg) => {},                // core forwards every SSE op
    onBoard?: (boardId) => {},              // on switch
  };
  ```

- `bodyHTML(b)`: new `default:` branch — if `window.VisorModules` has a
  module claiming `b.type`, use its `renderCard(b)`. Otherwise the existing
  JSON-dump fallback. Module renderers may include `<script>` — the
  html-script fix (above) generalizes to it: `makeEl` re-runs scripts for
  any card whose body came from a module renderer (tracked via a flag on
  the element).
- No module may touch core state (`blocks`, `view`, `world`) directly —
  only the documented hooks. Keep it a fence so modules can break without
  taking the canvas down.

### Versioning

- 1.5.0 = convention + html-script fix + `/api/modules` + CI: smoke test
  gains a "plugin disabled/enabled" case (push a module-declared type with
  the module off → 400; on → 200).

## 2. `live` module (v1.6.0)

Auto-refreshing data blocks.

- Block type `live`:

  ```json
  {"url": "http://localhost:8080/metrics", "interval": 30,
   "extract": "spend.total",            // dot-path into JSON; "auto" = guess
   "as": "stat|text|table|spark"}
  ```

- Server: one daemon scheduler thread (single, shared) polls due blocks
  (min interval 5s, default 30s). Results stored in
  `data/plugins/live.json`: per block id `{value, ts, history:[...]}`
  (history capped at 500 points, used by `spark`).
- Refresh applies the value into the block's stored `data` via the core
  store (so exports/state include latest) + `ctx.emit(board, "upsert", ...)`.
- Client `renderCard`: renders per `as` — `stat` (KPI value + delta +
  sub), `text` (markdown), `table` (first rows of a JSON array), `spark`
  (inline SVG polyline of history). Card footer: "updated 12s ago" + a
  pulsing dot while a fetch is in flight.
- Failure: keep last value, red dot, error string in card footer. One
  consecutive-failure counter; after 10 the card dims and shows the last
  error (no silent retry spam — poll continues).
- Fetch safety: only `http(s)`, 10s timeout, 1MB response cap. Local
  addresses allowed by default (personal canvas); `VISOR_LIVE_BLOCK_LOCAL=1`
  restricts to a configured host allowlist for networked installs.
- Push/CLI/MCP parity: `live` is just a block type — `visor_push`
  (plugin/MCP/CLI) gains no new verb; the Hermes plugin's `_BLOCK_TYPES`
  set must be extended per enabled module (the plugin reads
  `GET /api/modules` once and adds declared types; cached).

## 3. `chat` module (v1.7.0) — agent chat bridge

A card on the canvas that is a chat with the agent.

- Block type `chat`: the card shows a thread (markdown messages, you +
  agent) + an input box. Thread = one per board (card carries no thread id;
  `data/plugins/chat/<board>.json` holds `{messages:[{role,text,ts}]}`,
  capped at 200 messages, trimmed oldest).
- **Outbound (you → agent)**: input box POSTs `/api/plugins/chat/send`.
  Module forwards the message as a POST to the configured agent webhook:
  `CHAT_WEBHOOK_URL` + `CHAT_WEBHOOK_SECRET` (env; written by
  `visor.py chat setup` helper which runs `hermes webhook subscribe
  visor-chat --prompt "..."` and stores the returned URL/secret into
  `data/plugins/chat/config.json`; env wins if set).
  - Webhook prompt template instructs: answer, then push your answer with
    `visor_push` as a `chat` block for board `<board>` so it appears on the
    canvas.
- **Inbound (agent → you)**: the agent's `visor_push` of a `chat`-type
  block is intercepted by the module's `ctx.on_upsert` transform: instead of
  replacing the card, the text in `data.text` is appended to the thread and
  the card's stored data is set to the full thread. `ctx.emit` pushes the
  upsert over SSE; client renders in place.
  - If the agent answers but never pushes: card shows "waiting for the
    agent to reply to the canvas…" with a 120s timeout → honest timeout
    state (message keeps the "sent" mark).
- **Bridge states on the card**: `live` (webhook reachable), `offline`
  (no config / last send failed with connection error) — shown in the card
  footer; input stays enabled (messages queue in-thread as `sent`, one
  retry offered as a button).
- Session model: fresh agent run per message (webhook semantics). Same
  profile/memory/skills → continuity. Documented in README.
- Security: webhook secret from env/config, never rendered client-side;
  `send` endpoint is auth-gated like the rest of `/api/*`.
- Smoke: with a stub webhook (test spins a tiny http server in CI), assert
  send → forward happened, and reply push → thread grew.

## 4. `voice` module (v1.8.0) — orb face, STT + TTS

Declares dependency on `chat` (card shows "needs chat module" if absent).

- Block type `voice`: the **orb** face (design A from the compare mock —
  user picked it). States: `idle` (breathing core, slow ring rotation),
  `listening` (amber radial pulse while mic open), `thinking` (rings
  speed up while waiting on the agent), `speaking` (green waveform ring,
  amplitude-synced via WebAudio `AnalyserNode` on the played TTS clip).
  Implementation carries over from the verified mock (canvas, ~120 lines).
- **STT**: browser `getUserMedia` + `MediaRecorder` (webm/opus) →
  `POST /api/plugins/voice/stt` (bytes + `?format=`). Module transcribes
  and returns `{text}`; transcript is dropped into the chat thread as a
  user message via the normal `send` path (one tap = one turn).
  - Config (`data/plugins/voice/config.json` or env `VOICE_*`):
    `stt: {mode: "url"|"whisper-local", endpoint: "http://whisper:9000/v1/audio/transcriptions", model: "base"}`.
- **TTS**: when a new *agent* message lands on the chat thread (client
  `onSSE` on the chat upsert, only if a voice card is present on the
  board), client POSTs it to `/api/plugins/voice/tts` → server synthesizes →
  returns `{src: "/plugins/voice/audio/<id>.mp3"}` (served, GC'd after 2h).
  Audio element + analyser drive the orb.
  - Config: `tts: {mode: "url", endpoint: "http://kokoro:8889/api/tts", voice: "..."}` —
    defaults target the compose services below; any OpenAI-compatible
    `/v1/audio/speech`-style or raw text→bytes endpoint works (module
    supports `response: "binary"|"json"`).
- **One-stack backends** (compose profiles, `docker compose --profile voice up -d`):
  - `whisper` service: whisper.cpp HTTP server (CPU by default;
    `WHISPER_DEVICE=cuda` env passthrough if the host has a GPU).
  - `kokoro` service: local Kokoro-82M TTS HTTP service (CPU is fine at
    82M; same profile).
  - Both optional: without the profile, `voice` module still boots, face
    renders, and cards degrade to "STT/TTS backend not configured" on use.
  - User's existing H200 services (kokoro-tts :20001 via aigateway, any
    deployed whisper) plug in via the config endpoints — no code change.
- Privacy note for README: mic audio stays in the browser + the configured
  STT backend; nothing is persisted.

## Build order & release map

| Version | Contents |
|---|---|
| 1.5.0 | html-script fix (done, uncommitted), plugin convention, `/api/modules`, `plugins/` skeleton, CI plugin cases |
| 1.6.0 | `live` module |
| 1.7.0 | `chat` module + webhook setup helper |
| 1.8.0 | `voice` module (orb face, STT/TTS, compose voice profile) |

Each release: README update, CHANGELOG entry, smoke-test extension, and a
CDP-verified UI check before commit. Commits only with explicit user
confirmation (repo rule).

## Testing

- `scripts/smoke.py` extensions per release (see above), plus a core
  regression: with `VISOR_MODULES` unset, the container must behave
  byte-identically to v1.4.6 (same endpoint responses; `/api/modules` →
  `[]`).
- Client changes verified via CDP on the running container (DOM asserts +
  screenshots), not just `node --check`.
- Module isolation test: set a module to throw in `register()` (env
  `VISOR_MODULES_BROKEN=1` test hook or a fixture module in CI) → core
  /state, SSE, and exports still work.

## Non-goals (this cycle)

- No log/file tailing in `live` (HTTP polling only; file tail = later).
- No multi-board chat threads (one per board is enough).
- No wake-word, no voice input persistence, no video.
- No per-endpoint plugin auth beyond the existing `VISOR_TOKEN`.
