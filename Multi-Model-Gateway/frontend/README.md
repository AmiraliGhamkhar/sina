# llm-gateway — Frontend

React + Vite + TypeScript frontend for the llm-gateway backend. Dark, editorial UI
(deep-purple canvas, coral/gold accents, Instrument Serif headings) built with
Tailwind CSS v4, Zustand, and react-router.

## Stack

- **React + Vite + TypeScript**
- **Tailwind CSS v4** (`@tailwindcss/vite`, CSS-first theme in `src/index.css`)
- **Zustand** for state, **react-router-dom** for routing
- **react-markdown + remark-gfm + rehype-highlight** for chat message rendering
- **lucide-react** icons

## Setup

```bash
npm install
cp .env.example .env   # then edit if needed
npm run dev            # http://localhost:5173
```

The backend is expected at `http://localhost:2727` (the Docker-mapped port).

## Environment variables

| Var | Dev default | Notes |
|---|---|---|
| `VITE_API_URL` | `http://localhost:2727` | Backend base URL |
| `VITE_API_PREFIX` | `` (empty) | Use `/api` when behind the Caddy reverse proxy |
| `VITE_COMFYUI_HOST` | `http://localhost:8188` | Obsolete/ignored for rendering (images are now served via the authed `/v1/images/file` route; kept only for the settings-page display) |

For production behind Caddy, the built frontend is served on port `6969` and the
API is reached via `/api` — set `VITE_API_PREFIX=/api`.

## Scripts

- `npm run dev` — dev server
- `npm run build` — typecheck (`tsc -b`) + production build to `dist/`
- `npm run preview` — preview the production build

## Project structure

```
src/
  lib/         types, api-client (auth interceptor + SSE), endpoints, utils, config
  stores/      zustand: auth, chat, preset, template, ui (toasts)
  hooks/       use-chat (SSE streaming), use-image (ComfyUI polling)
  components/  ui/ primitives, layout/ shell + sidebar, chat/ message UI
  pages/       login, register, chat, images, presets, templates, settings, not-found
```

## Image generation — aspect ratios

The `aspect_ratio` value is forwarded **verbatim** to ComfyUI's `ResolutionSelector`
node, so the options must match the strings that node accepts. The list is **owned by
the backend** (single source of truth) and fetched by the frontend — it is no longer
hardcoded here.

- Backend source: `ASPECT_RATIOS` / `DEFAULT_ASPECT_RATIO` in `backend/app/services/comfy.py`
  (the image request also validates against it).
- Endpoint: `GET /v1/images/aspect-ratios` → `{ "aspect_ratios": [...], "default": "..." }`.
- Frontend: the image-gen page fetches this on mount (`imageApi.aspectRatios()`), renders
  the button grid from it, and selects the returned default. If the fetch fails it shows a
  retry and falls back to the backend's own default by omitting `aspect_ratio` from the request.

**To add more ratios:** append the exact node string to `ASPECT_RATIOS` in the backend —
nothing to change on the frontend.

## Backend behaviors the UI relies on

- Chat auto-routing only happens when `model: "auto"` is sent; the UI always sends it.
- Chat SSE frames tokens as `data: <content>\n\n`, ends with `data: [DONE]`, and emits
  errors **without** a `data:` prefix — the client parses both.
- `GET /v1/convo` and `GET /v1/convo/{id}` return bare arrays; presets/templates lists
  are wrapped in `{ data: [...] }`. Messages share an `index` per turn and are sorted
  client-side (user before assistant).
