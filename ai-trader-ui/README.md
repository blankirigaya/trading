# AI Trader UI

Electron desktop monitoring and control interface for the AI trader backend
(`D:\trading\ai-trader`). **Monitoring only.** This app does not trade, does not size
positions, and contains no trading intelligence. Every decision it displays was made by the
Python backend's AI.

## Safety

- A permanent **PAPER MODE / LIVE SHADOW — NO REAL ORDERS** banner is rendered in the shell on
  every page and cannot be dismissed.
- `LIVE ORDER EXECUTION: FALSE` is always shown in the header.
- The renderer has no buy, sell, or order control. Its only write operations are the safe
  controls on the System page: request an AI review, start/stop the review loop, toggle the
  kill switch, and reset the paper account.
- Kill switch and paper reset both require an explicit confirmation dialog.
- No credentials of any kind live in this repository. FYERS, remote AI, and Jev secrets remain
  in the backend environment.

## Architecture

```text
Electron renderer (sandboxed, context-isolated, no Node)
        │  REST + WebSocket only
        ▼
FastAPI backend  (source of truth)
        │
   FYERS │ Remote AI │ Jev
        └──────┬─────┘
               ▼
          PaperBroker → DecisionJournal
```

The UI never contacts FYERS, Jev, the remote model, the database, or PaperBroker internals
directly. `src/test/sourceSafety.test.ts` asserts this at the source level.

## Electron security

`contextIsolation: true`, `nodeIntegration: false`, `sandbox: true`, `webSecurity: true`. The
preload bridge exposes only `isPackaged` and `platform`. No filesystem, shell, child-process, or
environment access is exposed to the renderer. External links open in the system browser via
`setWindowOpenHandler` deny-by-default. The renderer HTML carries a restrictive CSP.

## Configuration

Copy `.env.example` to `.env`. The UI holds no secrets; these are addresses only.

```env
VITE_API_BASE_URL=http://127.0.0.1:8000
VITE_WS_BASE_URL=ws://127.0.0.1:8000
```

If the backend is unreachable the app still launches and shows `BACKEND OFFLINE`, retrying with
bounded exponential backoff.

## Commands

```bash
npm install
npm run electron:dev     # run against a running backend
npm run dev              # renderer only, in a browser
npm run build            # typecheck + production renderer build
npm run electron:build   # main + preload + renderer
npm run typecheck
npm run lint
npm run test
node scripts/smoke.mjs   # launch the built app, assert it renders, screenshot
```

## Pages

| Page | Contents |
| --- | --- |
| Dashboard | Provider status, portfolio summary, open positions, market strip, latest AI decision (three stages), AI analysis, live timeline |
| Market | Watched symbols with LTP/change/OHLC/volume, 1-minute candlestick chart with AI and Jev decision markers |
| Timeline | Live event stream with category filters and text search |
| Positions | Open paper positions valued at real FYERS prices, linked decision IDs |
| Portfolio | Portfolio value, cash, invested, realized/unrealized P&L, allocation bars |
| Trades | Simulated trades, every row labelled PAPER, with detail drawer |
| Decisions | Filterable journal; detail shows initial AI, Jev, reconsideration, final AI, safety, paper execution, portfolio before/after |
| Performance | P&L counters, AI decision analysis, outcome measurements |
| System | Runtime and provider diagnostics, live order execution DISABLED, confirmed safe controls, last error |

## State

TanStack Query owns all server state. The WebSocket stream only invalidates queries; the UI is
never the authoritative store. On reload, state is reloaded from the backend rather than
reconstructed from UI memory.

## Tests

41 tests across five files: INR/Indian-numbering formatting, WebSocket backoff and reconnect
behaviour, paper-mode and offline rendering, the three-stage decision separation, control
confirmation, and source-level safety guarantees.
