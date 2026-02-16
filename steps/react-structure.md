# Project type

This is a **Create React App (CRA)** project, customized with **CRACO**.

Why:
- `react-scripts` is present in dependencies (`ollama_springboot/ollama-gui-reactjs/package.json`).
- Scripts are `craco start/build/test/eject`, which is the CRA toolchain wrapped by CRACO.
- There is no Vite config (`vite.config.*`) and no Next.js structure (`next`, `pages`/`app` conventions for Next routing).

# Entry point

**Entry file:** `ollama_springboot/ollama-gui-reactjs/src/index.tsx`

What it does:
- Imports global CSS (`src/index.css`).
- Imports `router` from `src/router.tsx`.
- Creates React root with `ReactDOM.createRoot(...)`.
- Renders the app in `React.StrictMode`.
- Mounts routing via `<RouterProvider router={router} />`.

Key lines:
- `index.tsx:8-10` create root
- `index.tsx:12-15` render `RouterProvider`

# Root component

There is **no `App.tsx` root component** in this project.

The practical root is:
- `RouterProvider` in `src/index.tsx`
- Backed by router config in `src/router.tsx`

So the top-level render flow is:
- `index.tsx` -> `RouterProvider` -> route element from `router.tsx`

# Routes table (path -> component)

| Path | Rendered element |
|---|---|
| `/` | `<SplashScreen><NotFound /></SplashScreen>` |
| `/ollama-gui-reactjs` | `<SplashScreen><Chat /></SplashScreen>` |
| `/ollama-gui-reactjs/chat` | `<SplashScreen><Chat /></SplashScreen>` |
| `/ollama-gui-reactjs/chat/:chat` | `<SplashScreen><Chat /></SplashScreen>` |
| `/ollama-gui-reactjs/config` | `<SplashScreen><Config /></SplashScreen>` |

Source:
- Route definitions: `ollama_springboot/ollama-gui-reactjs/src/router.tsx:11-32`
- Path constants: `ollama_springboot/ollama-gui-reactjs/src/constants/routes.ts:1-8`

Note on `/`:
- `NotFound` immediately navigates to `ROUTES.ROOT` (`/ollama-gui-reactjs`) via `useEffect`.
- Source: `ollama_springboot/ollama-gui-reactjs/src/pages/notFound/index.ts:10-12`

# Initial page component tree

On first load at `/` (default browser root), the effective chain is:

1. `src/index.tsx` (entry)
2. `RouterProvider` (root router renderer)
3. Route `/` from `src/router.tsx`
4. `SplashScreen` export (actually `LoadingWrapper` in `src/pages/splashScreen/index.tsx`)
5. Inner `SplashScreen` + `NotFound`
6. `NotFound` redirects to `/ollama-gui-reactjs`
7. Route `/ollama-gui-reactjs` -> `SplashScreen` wrapper + `Chat`
8. `Chat` page renders:
   - Layout: `RowContainer`
   - Left: `Menu`
   - Main: `ColumnContainer`
   - Body: `Talk` (if chat exists) **or** `About` (empty state)
   - Input area: `TextArea` + `Button`

Important lines:
- Chat empty-state render: `ollama_springboot/ollama-gui-reactjs/src/pages/chat/index.tsx:199`
- `About` import in Chat: `ollama_springboot/ollama-gui-reactjs/src/pages/chat/index.tsx:16`

# Exact text location: "This app is a front end for the LLM"

Current working tree status:
- Exact text search result: **no matches**.
- It is not currently present in any file.

Where it was defined and rendered:
- Defined in `ollama_springboot/ollama-gui-reactjs/src/components/about/index.tsx` (historically line 9 in commit `9d93fb7`).
- That component is rendered by `Chat` at `ollama_springboot/ollama-gui-reactjs/src/pages/chat/index.tsx:199` via `<About />`.
- Therefore it appeared under routes rendering `Chat`:
  - `/ollama-gui-reactjs`
  - `/ollama-gui-reactjs/chat`
  - `/ollama-gui-reactjs/chat/:chat`

Current replacement text location:
- `ollama_springboot/ollama-gui-reactjs/src/components/about/index.tsx:10`
