# v1.4 — Connectors between blocks

Date: 2026-09-14 · Status: approved (user: "both modes — API + interactive")

## Problem

Blocks on the canvas are independent cards. Agents often gather *related*
info (cause → effect, dependency A → B, step 1 → step 2) that reads better
with an explicit connection than with spatial proximity.

## Goals

1. Draw labeled arrow connectors between any two blocks on a board.
2. Links **auto-follow** when cards are dragged/aligned — no rework.
3. Two creation paths: **interactive** (drag from a card edge) and
   **API-declarable** (agent pushes a link by block id).
4. Links persist per-board in `store.json`, sync over SSE, and appear in
   both HTML (E) and PNG (P) exports.

## Non-goals (YAGNI)

- No link types/styles beyond one default (indigo arrow) + optional label.
- No arrows between boards (links are board-scoped).
- No minimap rendering of links (cards only — the minimap stays a card map).
- No link re-routing / orthogonal routing — one quadratic curve is enough.

## Data model

Board-level `links` array (NOT per-block — relations are cross-block, and
board-level storage makes pruning trivial). Store v2 boards gain a key:

```json
{"id": "lk3f9a", "from": "<blockId>", "to": "<blockId>", "label": "optional"}
```

- `label` optional (may be `""`).
- Duplicate `(from, to)` pairs are rejected (409).
- Self-links (`from == to`) rejected (400).
- Unknown board or endpoint blocks → 404.
- On block DELETE: all links touching that id are pruned server-side.
- On board CLEAR: all links are cleared too.
- `/api/state?board=X` response includes `"links": [...]` automatically
  (spread `**b`), so client boot/resync pick them up with no API change.
- Old stores (no `links` key) load fine — `b.get("links", [])`.

## Server (server.py)

New endpoints, all board-scoped via `?board=` like the rest:

- `POST /api/links` — body `{from, to, label?}` → create. 409 on duplicate.
- `DELETE /api/links/<id>?board=X` — delete by link id. 404 if missing.
- `PATCH /api/links/<id>?board=X` — body `{label?}` — rename label only.

Each mutation: `save(st)` + `broadcast({"op": "links", "board": bid})`
(full-links change event; the client re-reads state for that board, same
pattern as `meta`).

`do_DELETE` for a block and `do_POST /api/clear` prune the board's links
before broadcasting (clients refetch state, so no per-link `del` event is
needed).

## Client (index.html)

### Link layer

A single `<svg id="links">` element inside `#world` (absolute, inset 0,
`overflow: visible`, `pointer-events: none`) — it inherits the world
transform, so links pan/zoom with the canvas for free. A `<g>` per link,
recomputed from `layout` + live element sizes:

- Endpoint = nearest edge midpoint of each card (pick the edge by the
  dominant axis of the center-to-center vector).
- Curve: quadratic Bezier, control point = midpoint pushed along the
  dominant axis by ~30% of the distance (gentle S-curve, no straight
  lines through card middles).
- Arrowhead: small SVG marker, indigo `#4f46e5`, 2px stroke.
- Label (if set): `<text>` at curve midpoint, 11px, with a white
  halo (paint-order stroke) so it reads over the grid.
- Hit area: an invisible thick-stroke clone of the path per link
  (`pointer-events: stroke`) for click-select.

### Auto-follow

`drawLinks()` recomputes all link paths from current `layout` entries and
`el.offsetWidth/Height`. Called from: `placeAll()` (after transforms
settle), the drag `pointermove` (per frame — cheap, few links), `nudge`,
`alignAll`, and on `links` SSE events. This satisfies "lines follow when
cards move" without camera movement (the camera is never touched).

### Interactive creation

- Hovering a card shows **4 edge anchors** (tiny 12px dots, top/mid/right/
  bottom — midpoints of each edge, matching where curves attach).
- `pointerdown` on an anchor → link-drag state: a temporary path follows the
  cursor (world coords via `(clientX - view.x)/view.s`).
- `pointerup` over another card → `POST /api/links` with the from/to ids
  (direction = anchor's card → target card). Over empty space or the same
  card → abort.
- The SSE `links` event rebuilds the layer, so the created link appears
  even though the drag itself drew nothing permanent.

### Selection + delete

- Click a link path (its hit clone) → select: stroke → `#dc2626` (red),
  label red, plus a small `✕` circle at the curve midpoint.
- `Delete`/`Backspace` with a link selected (and no block selected) →
  `DELETE /api/links/<id>`.
- Clicking the `✕` → same delete.
- Block selection and link selection are mutually exclusive (selecting one
  clears the other); Esc clears both.

### Exports

- **PNG (P):** the export clones live cards into a `foreignObject` div —
  add a `<svg>` of the computed link paths (same coords, offset by the
  export origin) after the cards. Same data-URI rasterization path, so no
  taint issues.
- **HTML (E):** the standalone snapshot already contains a static
  `#snap` div — append the same link `<svg>` (absolute, inset 0 of `#snap`).
- **Minimap:** unchanged (cards only).

## Agent surfaces

- **CLI (visor.py):**
  - `visor.py link <fromId> <toId> ["label"]`
  - `visor.py unlink <linkId>`
  - `visor.py links` — list with resolved block titles.
- **Hermes plugin (hermes/plugin/__init__.py):** `visor_push` gains
  `links: [{from, to, label?}]` (optional) — applied after blocks push,
  using the returned block ids when `from`/`to` are `"<last>"`/by-index is
  NOT supported; ids come from prior pushes (the tool's `blocks` response
  carries ids, so a second `visor_push` call with `links` is the normal
  pattern — same as how focus works today).
- **MCP (mcp/server.py):** `visor_push` gains the same `links` param;
  bump `SERVER_INFO` to 1.4.0.
- **docs/agent-setup.md:** document `links` in the push contract.

## Testing / verification

1. `node --check` the extracted `<script>` before rebuild (per skill).
2. Rebuild container; run `python3 scripts/smoke.py` — extended with:
   - create board + 2 blocks → `POST /api/links` → state shows link
   - duplicate → 409; self-link → 400; unknown block → 404
   - `PATCH` label → state shows new label
   - delete block → link pruned; `DELETE /api/links/<id>` → gone
   - clear board → links gone
3. Real-browser CDP verification on :9223 (per skill):
   - API-created link renders (vision: curve + arrow between the two cards)
   - drag a card → `drawLinks` updates (assert path `d` changed, camera
     byte-identical before/after)
   - edge-anchor drag-create → new link appears
   - click link → selected style + ✕; Delete key → removed
   - P → download PNG → vision: arrows present in the raster
4. Push a demo scenario to a board and verify visually end-to-end.

## Backwards compatibility

- Store v2 unchanged in version number (additive key); old stores load.
- No block schema changes. `w`, `x`, `y`, `data` untouched.
- All existing SSE ops unchanged; `links` is a new op.

## Files touched

`server.py`, `index.html`, `visor.py`, `hermes/plugin/__init__.py`,
`mcp/server.py`, `scripts/smoke.py`, `README.md`, `docs/agent-setup.md`,
`CHANGELOG.md`, `~/.hermes/skills/visor/SKILL.md`, `~/.hermes/plugins/visor/`
(plugin copy sync).
