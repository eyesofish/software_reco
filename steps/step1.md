# Runtime rendering path for landing text (first load)

## 1) URL path that triggers it

First browser load usually starts at:
- `/`

At runtime, `/` does **not** render Chat directly. It renders `NotFound`, which immediately navigates to:
- `/ollama-gui-reactjs`

So the landing text is effectively shown under:
- `/ollama-gui-reactjs` (and also `/ollama-gui-reactjs/chat` / `/ollama-gui-reactjs/chat/:chat` when chat is empty)

## 2) Route config selection sequence

Route config file:
- `ollama_springboot/ollama-gui-reactjs/src/router.tsx`

Sequence on first load:
1. Path `/` matches:
   - `element: <SplashScreen><NotFound /></SplashScreen>` (`router.tsx:13-16`)
2. `NotFound` runs `navigate(ROUTES.ROOT)` (`pages/notFound/index.ts:10-12`)
3. `ROUTES.ROOT` is `/ollama-gui-reactjs` (`constants/routes.ts:1-5`)
4. Path `/ollama-gui-reactjs` matches:
   - `element: <SplashScreen><Chat /></SplashScreen>` (`router.tsx:18-20`)

## 3) Page component that imports About

`Chat` imports `About` here:
- `ollama_springboot/ollama-gui-reactjs/src/pages/chat/index.tsx:16`

`Chat` renders `About` in empty-state branch:
- `ollama_springboot/ollama-gui-reactjs/src/pages/chat/index.tsx:199`
- JSX: `: <About />`

## 4) Exact runtime component sequence (first-load path)

1. `src/index.tsx` -> `<RouterProvider router={router} />`
2. Router match `/` -> `<SplashScreen><NotFound /></SplashScreen>`
3. `NotFound` redirects to `/ollama-gui-reactjs`
4. Router match `/ollama-gui-reactjs` -> `<SplashScreen><Chat /></SplashScreen>`
5. `Chat` renders `<About />` when `hasTalk === false`
6. `About` outputs the landing informational text block

## 5) Exact JSX rendering the requested strings

File:
- `ollama_springboot/ollama-gui-reactjs/src/components/about/index.tsx`

Rendered JSX lines:
- `index.tsx:11`
```tsx
<p><a href='https://ollama.com' target='_blank'>Ollama</a> is an interface created by Meta that facilitates the use of artificial intelligence.</p>
```

- `index.tsx:12`
```tsx
<p>The initial setup uses the <a href='https://ollama.com/library/llama3' target='_blank'>Llama 3</a> model.</p>
```

- `index.tsx:15`
```tsx
<a href='https://github.com/kastorcode' target='_blank' title='Powered by KastorCode'>&lt;kastor.code/&gt;</a>
```

## 6) Notes

- The exact old sentence `"This app is a front end for the LLM"` is no longer in current code (already replaced).
- The current landing title is rendered at `about/index.tsx:10`.
