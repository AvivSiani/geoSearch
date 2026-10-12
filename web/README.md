# GeoSearch web UI

The Hebrew, right-to-left React client for the GeoSearch agent: draw an area on
a map, ask about it, and get answers with results as numbered map markers.
React 18 + TypeScript + Vite, Leaflet + Geoman for the map. The visual rules
come from the design handoff (`DESIGN_SYSTEM.md`): every color, size and
spacing value is a token in `src/styles/tokens.css`.

## Run

```bash
cd web
npm install
npm run dev        # http://localhost:5173, proxies /v1 to the API on :8000
npm run dev:mock   # no backend needed: an in-browser fake agent answers
```

Start the API first for `npm run dev` (`uv run uvicorn geosearch.api.main:app`
in the repo root). The API sends no CORS headers, so the UI always calls it on
its own origin: Vite's proxy in development, a reverse proxy (`/v1` → the API)
in production.

In mock mode, a prompt containing `#error` or `#busy` triggers the matching
error state.

## Check

```bash
npm test           # unit + UI tests (vitest, jsdom)
npm run typecheck
npm run build      # typecheck, then a production build in dist/
```

## Configure

Copy `.env.example` to `.env.local`. Everything is read once in `src/config.ts`:
the API target and timeout, the client-side limits that mirror the backend's
(`VITE_MAX_AREA_KM2`, `VITE_MAX_PROMPT_CHARS`), the basemaps (`VITE_BASEMAPS`,
a JSON list of tile layers), the geocoder and the initial view.

## Theme

A light and a dark theme, switched from the top bar. Until the user picks
one it follows the OS setting; the choice is saved in this browser
(`geosearch.theme`). The design system defines only the light palette; the
dark one in `src/styles/tokens.css` keeps the same token roles. On the map,
street tiles are inverted in dark mode unless a basemap sets `darkUrl`.

## How it fits the backend

| UI | API |
| --- | --- |
| First question on an area | `POST /v1/requests` with `wkt` + `prompt` |
| Follow-up | `POST /v1/requests` with `conversation_id` + `prompt` (no `wkt`) |
| Results, citations | `items[]`; answers cite them as `[i3]`, shown as numbered badges |

- **One conversation, one area.** Drawing, editing or clearing the area after a
  question starts a new chat; the old one stays in history.
- **Circles, rectangles and freehand shapes are sent as polygons.** The backend's
  own Point buffer uses a fixed configured radius, so a drawn circle becomes a
  64-sided geodesic polygon in the browser.
- **History lives in this browser** (`localStorage`, key `geosearch.history`).
  The API keeps only text turns (no items, no geometry) and has no route that
  lists conversations. When a stored conversation has expired on the server
  (`CONVERSATION_NOT_FOUND`), the next question starts a new one on the same area.
- **The tools panel lists the real tools** (`src/data/agentTools.ts`). There is
  no API for the catalog, so update that file when `registry/seeds/` changes.
- **Tool-step chips show what is known:** which tools produced the answer's
  items. The API doesn't report every tool call.

## Layout of `src/`

```
api/         wire types, HTTP client, error → Hebrew message, mock API
chat/        answer parsing (lists, bold, [iN] citations), GeoJSON export
components/  TopBar, map/, chat/, history/, tools/
config.ts    every deployment setting
data/        the agent tools catalogue
geo/         WKT, area, circles, coordinates, thumbnails (pure functions)
i18n/he.ts   every user-facing string
map/         GeoMap (all Leaflet code) and the geocoder
state/       reducer, provider (sending, retries), localStorage history
styles/      tokens and base styles
```

To replace the map library, rewrite `map/GeoMap.ts`; components only use its
methods and callbacks. To change the place search, implement `Geocoder` in
`map/geocoder.ts`.
