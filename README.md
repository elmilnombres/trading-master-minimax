# Trading Portfolio System

SMC/ICT algorithmic trading system running on Bybit — live minimal controlled mode.

## Architecture
- 3 bots: Alpha, Beta, Gamma (one subaccount each)
- 1 portfolio supervisor (read-only, all subaccounts)
- Priority: Alpha → Gamma → Beta
- Exchange: Bybit
- Deployment: Docker / Coolify on VPS

## Bots
| Bot | Capital | Risk/trade | Timeframes | Trigger |
|---|---|---|---|---|
| Alpha | 50 USDT | 0.35–0.50% | H4 bias + M15 refine | Limit on approved POI |
| Beta  | 50 USDT | 0.20–0.30% | H4 bias + M1 trigger | Sweep → CHoCH → inducement → FVG |
| Gamma | 50 USDT | 0.25–0.40% | H4 bias + M5 | Killzone sweep → reclaim |

## Locked constants
- H4 close policy: wait full H4 candle + 60s grace
- ATR period: 14 (M1)
- Killzones (UTC): London 07–09, NY 13:30–16
- Supervisor polling: 5s, max latency 15s
- State journal: event-driven + 60s periodic backup

## Setup
```bash
pip install -r requirements.txt
cp configs/environments/.env.example .env
# fill in real credentials in .env before running
```

## Development
```bash
# run a single bot
python -m apps.alpha_bot.main

# run supervisor
python -m apps.portfolio_supervisor.main
```

## Docs
See `docs/` for architecture, deployment and operational rules.
See `CLAUDE.md` for the frozen framework contract.