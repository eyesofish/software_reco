# Project type

This is a **Create React App (CRA)** project, customized with **CRACO**.

Why:
- `react-scripts` exists in dependencies (`ollama_springboot/ollama-gui-reactjs/package.json`).
- Scripts use `craco start/build/test/eject`.
- No Vite config and no Next.js app/pages structure.

# Entry point

**Entry file:** `ollama_springboot/ollama-gui-reactjs/src/index.tsx`

Top-level render flow:
- Imports global CSS: `~/index.css`
- Imports router: `~/router`
- Creates root with `ReactDOM.createRoot(...)`
- Renders `<RouterProvider router={router} />` inside `React.StrictMode`

# Root component

There is no `App.tsx`.

Practical app root:
- `index.tsx` -> `RouterProvider`
- Route elements are defined in `src/router.tsx`

# Routes table (path -> component)

| Path | Rendered element |
|---|---|
| `/` | `<SplashScreen><NotFound /></SplashScreen>` |
| `/ollama-gui-reactjs` | `<SplashScreen><Chat /></SplashScreen>` |
| `/ollama-gui-reactjs/chat` | `<SplashScreen><Chat /></SplashScreen>` |
| `/ollama-gui-reactjs/chat/:chat` | `<SplashScreen><Chat /></SplashScreen>` |
| `/ollama-gui-reactjs/config` | `<SplashScreen><Config /></SplashScreen>` |

Notes:
- Router file: `ollama_springboot/ollama-gui-reactjs/src/router.tsx`
- Route constants: `ollama_springboot/ollama-gui-reactjs/src/constants/routes.ts`
- `/` immediately redirects to `ROUTES.ROOT` via `NotFound`.

# Config route analysis (`/ollama-gui-reactjs/config`)

## 1) Rendered component
- `Config`
- File: `ollama_springboot/ollama-gui-reactjs/src/pages/config/index.tsx`

## 2) Route definition
- React Router `createBrowserRouter(...)`
- Route:

```tsx
{
  path: ROUTES.CONFIG,
  element: <SplashScreen><Config /></SplashScreen>
}
```

## 3) Navigation into Config
- Trigger component: `Menu`
- File: `ollama_springboot/ollama-gui-reactjs/src/components/menu/index.tsx`
- Gear button handler:

```tsx
onClick={() => !loading && navigate(ROUTES.CONFIG)}
```

## 4) Current Config component hierarchy

- `RowContainer`
- `Menu`
- `ColumnContainer`
  - `Filler`
  - `Filler (width='88%')`
    - Language selector (`ThemeContainer + ThemeSelect`)
    - Theme selector (`Dark/Light`)
    - Toggle: `Enable splash animation`
    - Slider: `Animation speed` (`0.1` to `2.0`)
    - Slider: `Font size` (`14` to `20`)
    - Toggle: `Save all chats`
    - `TextInput` (`Model URL`)
    - `TextInput` (`Model Name`)
    - `ButtonsContainer`
      - `Button` (`Clear all`)
      - `Button` (`Chat`)
      - `Button` (`Save`)
  - `Filler`
  - `Footer`

Simplified JSX:

```tsx
<RowContainer>
  <Menu />
  <ColumnContainer>
    <Filler />
    <Filler width='88%'>
      <LanguageSelect />
      <ThemeSelect />
      <Toggle id='enableSplash' />
      <Range id='starSpeed' />
      <Range id='fontSize' />
      <Toggle id='autoSaveChats' />
      <TextInput placeholder='Model URL' />
      <TextInput placeholder='Model Name' />
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

## 5) Config state and persistence

Config page currently mixes:
- Global config store:
  - `modelUrl`
  - `modelName`
  - `autoSaveChats`
- Local UI state:
  - `theme`
  - `language` (via `useAppLanguage`)
  - `enableSplash`
  - `starSpeed`
  - `fontSize`

LocalStorage keys used by Config/Splash:
- `config` (via `Store.set('config', config)`)
- `theme`
- `language`
- `enableSplash`
- `starSpeed`
- `fontSize`

Save button behavior in Config:
- Dispatches `updateConfig({...config, modelName, modelUrl})`
- Saves theme and applies `data-theme`
- Navigates to `'/'` (then app redirects to `ROUTES.ROOT`)

# Sidebar menu and chat list behavior

File:
- `ollama_springboot/ollama-gui-reactjs/src/components/menu/index.tsx`

Current chat rendering:
- Uses `chats.map(...)`
- Each row has:
  - chat link (`Chat`)
  - per-row delete button (`DeleteButton`) with `×`
- Delete button is no longer global.

Delete flow:
1. Click row-level `×`
2. Confirm dialog
3. `deleteSingleChat(chatIndex)` computes `updatedChats = chats.filter(...)`
4. Dispatches `disChats(deleteChat(chatIndex))`
5. Persists `Store.set('chats', updatedChats)`
6. Navigates to `ROUTES.ROOT`

Reducer behavior (`src/stores/chats/reducer.ts`):
- `DELETE_CHAT` creates `nextChats` by filtering out only target index.

# Splash lifecycle behavior

Files:
- `ollama_springboot/ollama-gui-reactjs/src/pages/splashScreen/index.tsx`
- `ollama_springboot/ollama-gui-reactjs/src/components/SplashScreen.tsx`

Current behavior:
- `enableSplash` controls whether splash animation is shown.
- App boot (`AppBoot.run()`) always runs.
- If splash is enabled, splash remains for at least 3 seconds.
- Star animation speed reads `starSpeed` from localStorage.
- Splash title is localized via `language` and `I18N`.

# i18n overview

File:
- `ollama_springboot/ollama-gui-reactjs/src/services/language.ts`

Current status:
- Supported languages: `en`, `zh`
- LocalStorage key: `language`
- `useAppLanguage()` listens to:
  - custom event `app-language-change`
  - browser `storage` event
- Text consumers include:
  - `Config`
  - `SplashScreen`
  - `About`

# Data flow summary (chat request path)

1. User types in `TextArea` on `Chat` page.
2. `requestHandler()` creates a user message and appends it to:
   - local `talkMessages`
   - global chats store (`ADD_MESSAGE`)
3. Calls `requester(modelUrl, modelName, ...)`
4. Streams assistant chunks into `streamingAssistantContent`
5. On completion, appends final assistant message to store and UI
6. If `autoSaveChats` is enabled, chats persist to localStorage

# Current About text location

Displayed empty-state text is now localized and read from:
- `I18N[language].aboutDescription`
- Consumer file: `ollama_springboot/ollama-gui-reactjs/src/components/about/index.tsx`
