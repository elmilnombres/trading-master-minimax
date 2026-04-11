# Architecture — Trading Portfolio System

## Frozen Implementation Constants

These constants are locked and cannot be changed without explicit user approval.

| Decision | Value | Source |
|---|---|---|
| H4 candle close policy | Wait full H4 candle close + 60s grace before evaluation | Q1 |
| ATR period | 14 (M1 default), configurable per bot | Q2 |
| Killzones (UTC internal) | London: 07:00–09:00 UTC / NY: 13:30–16:00 UTC | Q3 |
| Supervisor polling interval | Every 5 seconds | Q4 |
| Supervisor max latency | 15 seconds | Q4 |
| State journal snapshot | Event-driven on state change + 60s periodic backup | Q5 |

## Architecture Overview

```
Bybit (Main Account)
├── Subaccount Alpha  ← alpha-bot service
├── Subaccount Beta   ← beta-bot service
└── Subaccount Gamma  ← gamma-bot service

Portfolio Supervisor (read-only on all subaccounts)
```

## Services

| Service | Entrypoint | Subaccount | Permissions |
|---|---|---|---|
| alpha-bot | `python -m apps.alpha_bot.main` | BYBIT_ALPHA | READ + TRADE |
| beta-bot | `python -m apps.beta_bot.main` | BYBIT_BETA | READ + TRADE |
| gamma-bot | `python -m apps.gamma_bot.main` | BYBIT_GAMMA | READ + TRADE |
| portfolio-supervisor | `python -m apps.portfolio_supervisor.main` | all | READ-ONLY |

## Directory Structure

```
trading_portfolio_system/
├── apps/           — one entrypoint per service
│   ├── alpha_bot/
│   ├── beta_bot/
│   ├── gamma_bot/
│   └── portfolio_supervisor/
├── core/           — shared logic (market_data, structure, poi, bias, confirmation, risk, execution, portfolio, state, telemetry)
├── exchange/       — Bybit-specific only
│   └── bybit/
├── configs/        — YAML per bot and environment
│   ├── symbols/
│   ├── risk/
│   ├── strategies/
│   ├── sessions/
│   └── environments/
├── schemas/        — Pydantic data classes
└── tests/
```

## Assumptions Encoded

### Exchange
- All tradeable symbols use Bybit `linear` category (USDT perpetual futures)
- One-way position mode per subaccount
- No inverse contracts in initial phase
- Leverage fixed at 1x (no margin except for position sizing under one-way mode)

### Market Data
- Candle timestamps are UTC
- H4 candle close is confirmed only after `timestamp + 14400s + 60s` elapsed
- ATR calculated as simple average of True Range on M1 candles (Wilder smoothing deferred to Phase B)
- Killzone logic internally uses UTC; `APP_TIMEZONE` is for display only

### State
- Bot state survives container restarts via snapshot files in `/app/state`
- Journal format: `.jsonl` — one JSON object per line (full snapshot per entry)
- Snapshot frequency: event-driven on state change + 60s periodic backup thread

### Risk
- Position sizing formula: `(capital * risk_pct) / SL_distance`
- SL is structural; size adapts to SL, not the reverse
- Exchange filter validation is mandatory before every order

### Bots
- Bots do not implement their own exchange client or sizing logic
- All shared logic lives in `core/`
- Supervisor can block any bot even if the bot's subaccount has free capital

## Secrets Policy

There is exactly one secrets template in the repo: `.env.example` at the project root.

`configs/environments/.env.example` was removed — do not recreate it.

Real credentials live in `.env` (gitignored) on the VPS. For local development, copy `.env.example` → `.env` and fill in values. The input package's `07_SECRETS_LOCAL_ONLY.env` is the source of truth for real credentials — never copied into the repo.

## Module Responsibility Map

This clarifies where each responsibility lives and prevents accumulation of unrelated logic in one module.

| Module | Responsibility | Does NOT own |
|---|---|---|
| `core/market_data/provider.py` | H4 close confirmation, ATR14, killzone UTC state, spread. Frozen Phase 1 scope. | Bias, POI, structure, confirmation, sizing |
| `core/structure/` | Swing detection, BOS/CHoCH detection | POI logic, bias building |
| `core/poi/` | Order blocks, FVG/iFVG, mitigation, session levels | Structure, bias |
| `core/bias/` | Macro bias builder from 5 blocks | Execution, POI |
| `core/confirmation/` | Sweep, reclaim, inducement, sequence per bot | Bias building, sizing |
| `core/risk/` | Sizing, SL/TP, drawdown, cluster tracking | Confirmation, bias |
| `core/execution/` | Order submission, filter validation, slippage | Risk logic, confirmation |
| `core/portfolio/` | Aggregate exposure, arbitrator | Order execution, strategy |

`provider.py` EXPLICIT NO-GO RULES (permanent — never bypass):
1. Never score or rate a setup (quality, confidence, etc.)
2. Never rank or select POIs
3. Never infer or compute a bias state (bullish/bearish/neutral)
4. Never decide trade validity or signal activation

provider.py measures primitives only. All decisions belong to downstream strategy modules.

## Capital Source of Truth

**Per-bot risk configs** (`configs/risk/<bot>.yaml`) are the sole source of truth for capital allocation.

`configs/environments/prod_minimal_live.yaml` does NOT set capital — that field would be ignored. Each bot reads its capital from its own risk config:

```
configs/risk/alpha.yaml  → capital_usdt: 50.0
configs/risk/beta.yaml   → capital_usdt: 50.0
configs/risk/gamma.yaml  → capital_usdt: 50.0
```

## Deployment

- Single Docker image, 4 services via `docker-compose.yml`
- Each service runs the same image with a different entrypoint
- **Five explicit persistent paths** (do not change container paths):

  | Host path | Container path | Description |
  |---|---|---|
  | `./logs` | `/app/logs` | Structured bot logs |
  | `./state` | `/app/state` | Bot state snapshots |
  | `./data/journals` | `/app/data/journals` | Append-only trade journal (.jsonl) |
  | `./data/snapshots` | `/app/data/snapshots` | Periodic JSON snapshots |
  | `./data/cache` | `/app/data/cache` | Instrument filter cache |

## Phase Order

- Phase A: foundation (complete)
- Phase B: core analysis engines (structure, poi, bias, confirmation)
- Phase C: execution and risk (execution, risk, portfolio, telemetry)
- Phase D: bot apps (alpha, beta, gamma, supervisor)
- Phase E: deployment files, scripts, tests, docs