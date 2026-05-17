# nighttrade — orchestration widget brief

A build brief for a compact status tile that summarizes the nighttrade
dashboard inside an external project-orchestration tool.

## What nighttrade is (one paragraph)

nighttrade is an educational stock-market research & paper-trading platform.
It continuously observes the S&P 500 on live market data, runs a quant
analysis pipeline (technical indicators, tape microstructure, macro context,
an ML model, cross-sectional ranking) and **paper-trades only** — it never
places a real order. It condenses market conditions into a single **Market
Safety Score (0-100)**. That score, over time, is the widget's line graph.

## The widget

A small rectangular status tile (~240x120 px works well):

```
┌──────────────────────────────────────┐
│ ● nighttrade              stocks · €  │
│                                       │
│   56/100    WAIT · CHOPPY             │
│   ╭─────────────────────────────╮     │
│   │        ╶╴╶─╮     ╭─╴╶        │     │
│   │  ╶─╮╭╴      ╰─╴╴╯            │     │
│   ╰─────────────────────────────╯     │
│  502 symbols · paper €1,000 · +0.0%   │
└──────────────────────────────────────┘
```

- a status dot + project name
- the headline number: Market Safety Score `NN/100` + `status · condition`
- a **sparkline** = the Market Safety Score over the last ~60 observation cycles
- a footer line: symbols observed · paper equity · gain %

## Data source

A read-only HTTP/JSON API — no auth, no keys. It is **tailnet-only**, so the
orchestrator must run on a device joined to the same Tailscale network.

Base URL: `http://nedims-macbook-pro.tailf42a3b.ts.net:8001`

### `GET /api/overview` — the headline numbers

```json
{
  "safety_score": 56.4,
  "status": "WAIT",
  "condition": "CHOPPY",
  "bot_running": true,
  "cycles": 1,
  "last_heartbeat": "2026-05-17T21:17:53.448688+00:00",
  "equity": 1000.0,
  "starting_cash": 1000.0,
  "drawdown_pct": 0.0,
  "symbols_observed": 502,
  "prediction_accuracy": 0.0,
  "updated": "2026-05-17T21:17:53Z"
}
```

Widget uses: `safety_score`, `status`, `condition`, `bot_running`,
`equity`, `starting_cash`, `symbols_observed`, `last_heartbeat`.
Gain % = `(equity - starting_cash) / starting_cash * 100`.
All money figures are **euros**.

### `GET /api/safety-history` — the sparkline data

A list, oldest → newest, one point per observation cycle:

```json
[
  {"ts": "2026-05-17T21:17:41Z", "score": 56.4, "status": "WAIT", "condition": "CHOPPY"},
  ...
]
```

Plot `score` on a fixed `y: 0-100` axis. Use the last ~60 points.

### `GET /api/health` — liveness ping (optional)

```json
{"ok": true, "real_trading": false, "paper_only": true}
```

## Status → colour

| status             | colour |
|--------------------|--------|
| `SAFE_TO_OBSERVE`  | green  |
| `WAIT`             | amber  |
| `HIGH_RISK`        | orange |
| `UNSAFE`           | red    |
| bot offline        | grey   |

Colour the tile border / score number by `status`.

## Behaviour

- Poll `/api/overview` + `/api/safety-history` every **30-60 s** (the bot
  itself only advances every 5 minutes — faster polling gains nothing).
- `bot_running == false`, OR `last_heartbeat` older than ~20 minutes → render
  the tile greyed-out as **OFFLINE**.
- Empty `safety-history` → "warming up", flat line.
- Request fails / times out → "unreachable".
- Optional: a WebSocket at `/ws` pushes `{overview, status, progress}` every
  ~4 s if you want live updates instead of polling.

## If the line should be P&L instead

Use `GET /api/equity` → `{starting_cash, current_equity, peak_equity,
total_gain, total_gain_pct, points}` and plot `points` (equity over time).
The Safety Score is the better "is it healthy" signal; equity is the better
"is the paper strategy winning" signal.

## CORS note

If the widget is a **browser frontend** fetching this API from a different
origin, the nighttrade dashboard must send CORS headers (it does not yet).
If the orchestrator's **backend** polls the API and serves its own UI, there
is no CORS issue. Ask the nighttrade maintainer to enable CORS if needed.
