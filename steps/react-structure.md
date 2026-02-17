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

# Config route analysis (`/ollama-gui-reactjs/config`)

## 1) Which React component renders this page
- Component name: `Config`
- Defined as: `export default function Config ()`

## 2) Exact file path
- `ollama_springboot/ollama-gui-reactjs/src/pages/config/index.tsx`

## 3) Which router defines this route
- Router type: **React Router** (`createBrowserRouter`)
- Router file: `ollama_springboot/ollama-gui-reactjs/src/router.tsx`
- Route constant: `ROUTES.CONFIG` from `ollama_springboot/ollama-gui-reactjs/src/constants/routes.ts`
- Effective path value: `/ollama-gui-reactjs/config`

Routing definition:
```tsx
{
  path: ROUTES.CONFIG,
  element: <SplashScreen><Config /></SplashScreen>
}
```

## 4) How navigation to this page works
- Trigger component: `Menu` (`ollama_springboot/ollama-gui-reactjs/src/components/menu/index.tsx`)
- Trigger action: settings button calls `navigate(ROUTES.CONFIG)` on click.
- Main user flow: `Chat` renders `Menu`, and clicking the settings icon navigates to `/ollama-gui-reactjs/config`.

Navigation trigger line:
```tsx
onClick={() => !loading && navigate(ROUTES.CONFIG)}
```

## 5) Component hierarchy

There is no `App.tsx` in this project, so the practical hierarchy is:

- `index.tsx` (`RouterProvider`) -> route element from `router.tsx`
- Route element: `LoadingWrapper` (exported from `pages/splashScreen`)
- `LoadingWrapper` -> `SplashLifecycle` -> `Config`
- `Config` page layout:
  - `RowContainer`
  - `Menu`
  - `ColumnContainer`
    - `Filler`
    - `Filler (width='88%')`
      - `Toggle`
      - `TextInput` (`Model URL`)
      - `TextInput` (`Model Name`)
      - `ButtonsContainer`
        - `Button` (`Clear all`)
        - `Button` (`Chat`)
        - `Button` (`Save`)
    - `Filler`
    - `Footer`

JSX structure (simplified):
```tsx
<RowContainer>
  <Menu />
  <ColumnContainer>
    <Filler />
    <Filler width='88%'>
      <Toggle />
      <TextInput />
      <TextInput />
      <ButtonsContainer>
        <Button>Clear all</Button>
        <Button>Chat</Button>
        <Button>Save</Button>
      </ButtonsContainer>
    </Filler>
    <Filler />
    <Footer />
  </ColumnContainer>
</RowContainer>
```

# Gear icon -> `/config`

## File path
- Gear/settings icon component: `ollama_springboot/ollama-gui-reactjs/src/components/menu/index.tsx`

## JSX code

The gear icon is rendered inside the first `Button` in `Menu`:

```tsx
<Button
  style={{ padding: '6px', paddingBottom: '3px' }}
  onClick={() => !loading && navigate(ROUTES.CONFIG)}
>
  <svg width="16px" height="16px" viewBox="0 0 32 32" fill="#fff" stroke="#fff">
    <g id="SVGRepo_iconCarrier">
      <title>Configurations</title>
      <path d="M23.265,24.381l.9-.894 ..."></path>
    </g>
  </svg>
</Button>
```

Source reference:
- `ollama_springboot/ollama-gui-reactjs/src/components/menu/index.tsx:76-81`

## Navigation logic

- `Menu` uses React Router's `useNavigate()`:
  - `const navigate = useNavigate()`
  - Source: `ollama_springboot/ollama-gui-reactjs/src/components/menu/index.tsx:22`
- On gear click:
  - `onClick={() => !loading && navigate(ROUTES.CONFIG)}`
  - Source: `ollama_springboot/ollama-gui-reactjs/src/components/menu/index.tsx:78`
- Route constant:
  - `CONFIG: \`${ROOT}/config\`` where `ROOT = '/ollama-gui-reactjs'`
  - Source: `ollama_springboot/ollama-gui-reactjs/src/constants/routes.ts:1-8`

So the click navigates to: `/ollama-gui-reactjs/config`.

## Router usage

- Router library: **React Router**
- Route is defined in `createBrowserRouter(...)`:

```tsx
{
  path: ROUTES.CONFIG,
  element: <SplashScreen><Config /></SplashScreen>
}
```

Source reference:
- `ollama_springboot/ollama-gui-reactjs/src/router.tsx:30-31`

# Config functionality and data flow

## 1) Where API URL state is stored

Two layers store API URL:

- Local component state (editable input value before save):
  - `const [modelUrl, setModelUrl] = useState(config.modelUrl)`
  - Source: `ollama_springboot/ollama-gui-reactjs/src/pages/config/index.tsx:25`
- Global config store (persisted app config):
  - `STATE.config.modelUrl` default value
  - Source: `ollama_springboot/ollama-gui-reactjs/src/stores/config/index.ts:9`

Input binding:
- `<TextInput value={modelUrl} onChange={e => setModelUrl(e.target.value)} />`
- Source: `ollama_springboot/ollama-gui-reactjs/src/pages/config/index.tsx:63-66`

## 2) Where model name state is stored

Two layers store model name:

- Local component state:
  - `const [modelName, setModelName] = useState(config.modelName)`
  - Source: `ollama_springboot/ollama-gui-reactjs/src/pages/config/index.tsx:24`
- Global config store:
  - `STATE.config.modelName` default value
  - Source: `ollama_springboot/ollama-gui-reactjs/src/stores/config/index.ts:8`

Input binding:
- `<TextInput value={modelName} onChange={e => setModelName(e.target.value)} />`
- Source: `ollama_springboot/ollama-gui-reactjs/src/pages/config/index.tsx:68-71`

## 3) What happens when clicking Save button

Click path:

- Save button triggers `handleSave`:
  - `<Button onClick={handleSave}>Save</Button>`
  - Source: `ollama_springboot/ollama-gui-reactjs/src/pages/config/index.tsx:76`
- `handleSave` dispatches global store update:
  - `disConfig(updateConfig({ ...config, modelName, modelUrl }))`
  - Source: `ollama_springboot/ollama-gui-reactjs/src/pages/config/index.tsx:36-40`
- Action creator returns `UPDATE_CONFIG` action:
  - Source: `ollama_springboot/ollama-gui-reactjs/src/stores/config/actions.ts:7-8`
- Reducer replaces `config` state:
  - `case 'UPDATE_CONFIG': return { config: { ...action.payload } }`
  - Source: `ollama_springboot/ollama-gui-reactjs/src/stores/config/reducer.ts:16-20`
- `useEffect` persists updated config to localStorage:
  - `Store.set('config', config)`
  - Source: `ollama_springboot/ollama-gui-reactjs/src/pages/config/index.tsx:46-50`

## 4) What happens when clicking Chat button

- Chat button runs `navigate(ROUTES.GO_BACK)`:
  - `<Button onClick={() => navigate(ROUTES.GO_BACK)}>Chat</Button>`
  - Source: `ollama_springboot/ollama-gui-reactjs/src/pages/config/index.tsx:75`
- `ROUTES.GO_BACK` is `-1`, so React Router navigates one step back in browser history:
  - Source: `ollama_springboot/ollama-gui-reactjs/src/constants/routes.ts:8`

## 5) Which state management/storage patterns are used

- React state: **Yes**
  - `useState` for `modelUrl`, `modelName` in Config
  - Source: `ollama_springboot/ollama-gui-reactjs/src/pages/config/index.tsx:24-25`
- Context: **Not directly in this flow**
  - No `createContext` usage for config state in app code; config uses `react-hooks-global-state` store API.
- Flux store: **Yes (Flux-like global store)**
  - `createStore(reducer, STATE)` + dispatch/actions/reducer pattern
  - Source: `ollama_springboot/ollama-gui-reactjs/src/stores/config/index.ts:1-16`
  - Source: `ollama_springboot/ollama-gui-reactjs/src/stores/config/actions.ts:3-8`
  - Source: `ollama_springboot/ollama-gui-reactjs/src/stores/config/reducer.ts:4-23`
- Redux: **No**
  - No Redux dependencies in `package.json`; uses `react-hooks-global-state`.
  - Source: `ollama_springboot/ollama-gui-reactjs/package.json:34-53`
- localStorage: **Yes**
  - Wrapper service writes/reads `config` key.
  - Source: `ollama_springboot/ollama-gui-reactjs/src/services/store.ts:12-21`
  - Persist in Config page: `ollama_springboot/ollama-gui-reactjs/src/pages/config/index.tsx:49`
  - Load on boot: `ollama_springboot/ollama-gui-reactjs/src/services/appBoot.ts:25-29`

## Data flow trace: UI input -> state -> store -> API usage

1. UI input changes local state in Config:
   - `setModelUrl(...)`, `setModelName(...)`
   - Source: `ollama_springboot/ollama-gui-reactjs/src/pages/config/index.tsx:65`, `ollama_springboot/ollama-gui-reactjs/src/pages/config/index.tsx:70`
2. Save dispatches updated values to global config store:
   - Source: `ollama_springboot/ollama-gui-reactjs/src/pages/config/index.tsx:36-40`
3. Config reducer stores new values in global state:
   - Source: `ollama_springboot/ollama-gui-reactjs/src/stores/config/reducer.ts:16-20`
4. Config page effect persists global config to localStorage:
   - Source: `ollama_springboot/ollama-gui-reactjs/src/pages/config/index.tsx:46-50`
5. On app boot, localStorage config rehydrates global store:
   - `Store.get('config')` -> `disConfig(updateConfig(config))`
   - Source: `ollama_springboot/ollama-gui-reactjs/src/services/appBoot.ts:25-29`
   - Boot invocation path: `ollama_springboot/ollama-gui-reactjs/src/pages/splashScreen/index.tsx:23`
6. Chat page reads `modelUrl` + `modelName` from global config:
   - `const { autoSaveChats, modelName, modelUrl } = useConfig('config')`
   - Source: `ollama_springboot/ollama-gui-reactjs/src/pages/chat/index.tsx:35`
7. Chat request uses those values in API call:
   - `requester(modelUrl, modelName, ...)`
   - Source: `ollama_springboot/ollama-gui-reactjs/src/pages/chat/index.tsx:84-91`
   - `fetch(modelUrl, { ... body: { model: modelName, ... } })`
   - Source: `ollama_springboot/ollama-gui-reactjs/src/services/requester.ts:16-44`

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
