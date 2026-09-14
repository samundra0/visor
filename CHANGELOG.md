# Changelog

All notable changes to Visor. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/).

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
