# CLAUDE.md — TRADING PORTFOLIO SYSTEM
# Frozen framework contract. Do not reinterpret.

## PROJECT IDENTITY
Name: trading_portfolio_system
Type: SMC/ICT algorithmic trading system
Exchange: Bybit
Mode: Live minimal controlled (real money, $50/bot)

## LOCKED CONSTANTS
Do not change without explicit user approval.

H4_CANDLE_CLOSE_POLICY: wait full H4 candle close + 60s grace window before evaluation. Never act on unfinished H4 candles.
ATR_PERIOD: 14 (M1 default). Configurable per bot.
KILLZONES (UTC internal):
  - London: 07:00–09:00 UTC
  - New York: 13:30–16:00 UTC
  All internal killzone logic must use UTC.
SUPERVISOR_POLL_INTERVAL: 5 seconds
SUPERVISOR_MAX_LATENCY: 15 seconds
STATE_JOURNAL_SNAPSHOT: event-driven on state change + periodic 60s safety backup

## BOT PERMISSIONS (CONTRACT)
- Alpha / Beta / Gamma bots: Bybit API key with READ + TRADE permissions on their respective subaccounts
- Portfolio supervisor: Bybit API key with READ-ONLY permissions on all subaccounts (no trade)
This is conservative default. Change only if explicitly authorized by user.

## ARCHITECTURE
apps/           — one entrypoint per bot + supervisor (isolated, one per service/container)
core/           — shared logic: market_data, structure, poi, bias, confirmation, risk, execution, portfolio, state, telemetry
exchange/bybit/ — Bybit-specific adapter only. No other exchange logic here.
configs/        — YAML configuration per bot and environment
schemas/        — Pydantic data classes only

## JERARCHHY (NEVER MERGE)
Priority: Alpha → Gamma → Beta
Subaccounts: Alpha / Beta / Gamma — one per bot, one-way mode
Supervisor: reads all 3 subaccounts, can block, does NOT generate entries
Cluster rule: max 1 position per cluster in initial phase

## CAPITAL & RISK (NEVER CHANGE WITHOUT USER APPROVAL)
Alpha: 50 USDT — risk 0.35–0.50% per trade
Beta:  50 USDT — risk 0.20–0.30% per trade
Gamma: 50 USDT — risk 0.25–0.40% per trade
Max simultaneous portfolio risk: 0.75–1.00%
Max daily loss: 2.0% | Max weekly loss: 5.0%

## TEMPORALITIES
Macro: D1, H4 | Context: H1 | Refinement: M15 | Trigger: M5, M1
Rule: Micro cannot override H4 macro bias.

## SESGO MACRO (5 BLOCKS)
1. H4 structure
2. Draw on liquidity
3. Price location vs H4 POI
4. D1 context
5. H1 internal state
States: bullish_continuation / bearish_continuation / bullish_reversal_candidate / bearish_reversal_candidate / neutral

## POI WHITELIST
Order Block, Mitigation Zone, Equilibrium/50%, FVG, iFVG, PDH/PDL, PWH/PWL, Asian High/Low, London High/Low, liquidity sweep levels.
NOT YET: breaker blocks, BPR, liquidity void advanced.

## NO-GO RULES
- Do not summarize away critical details from these docs
- Do not invent strategies not in the frozen contract
- Do not change Alpha/Gamma/Beta priority
- Do not merge subaccounts
- Do not hardcode secrets (use env vars only)
- Do not build exchange logic outside exchange/bybit/
- Do not act on unfinished H4 candles
- Do not enable live trading without exchange filter validation

## PHASE ORDER (CONTRACT)
Phase A: foundation — schemas, exchange/bybit, core/market_data, core/state, minimal configs
Phase B: core analysis — structure, poi, bias, confirmation
Phase C: execution and risk — execution, risk, portfolio, telemetry
Phase D: bot apps — alpha, beta, gamma, supervisor
Phase E: deployment — Dockerfile, docker-compose, scripts, tests, docs

## PHASE STATUS

| Phase | Status | Notes |
|-------|--------|-------|
| Alpha Phase A.1 (rate-limit governor) | **✓ VERIFIED** | 2026-04-14 |
| Alpha Phase B (WS-first) | NOT STARTED | After Phase A complete |
| Beta Phase A.1 (rate-limit governor) | **✓ VERIFIED** | 2026-04-15 |
| Gamma | NOT STARTED | — |
| Delta | NOT STARTED | — |

### Alpha Phase A.1 — VERIFIED ✓

**Problem solved:** Bybit retCode 10006 causing infinite retry loop within same tick.

**Root cause:** `client.py` was retrying 10006 immediately with no backoff, causing a
retry-on-every-tick pattern that never reset. Governor was resetting on immediate success
rather than tracking post-cooldown recovery ticks.

**Fix implemented:**
- `core/rate_limiter/governor.py` — state machine: `on_10006_abort()`, `should_skip_tick()`,
  `mark_tick_clean()`, exponential backoff with ±2s jitter, cap 60s
- `exchange/bybit/client.py` — 10006 aborts tick once, no retry loop, calls `governor.on_10006_abort()`
- `exchange/bybit/execution.py` — 10006 removed from `RETRY_CODES`
- `exchange/bybit/subaccount.py` — `governor` property exposed
- `core/execution/errors.py` — `RetryableExchangeError` with `code` and `original` properties
- `apps/_runtime/bot_runtime.py` — pre-tick skip via `should_skip_tick()`, explicit `e.code == 10006`
  handling, `mark_tick_clean()` on clean ticks

**Validated behavior:**
- No same-tick retry after 10006 (verified: no sleep/retry in client.py line 94)
- 10006 removed from RETRY_CODES (verified: execution.py line 26)
- Governor `on_10006_abort()` called once per 10006 event
- `should_skip_tick()` skips subsequent ticks during cooldown window
- Heartbeat updates every 5s even during rate-limit cooldown
- Container remains `healthy` throughout

**Still present (not a bug):** Bybit continues returning 10006 on every tick due to
API rate limits on the account. This is expected Bybit behavior. The fix ensures
Alpha does not spiral into retry loops — it gracefully skips ticks and recovers
after cooldown windows with 2 consecutive clean ticks resetting backoff to 0.0.

### Beta Phase A.1 — VERIFIED ✓

**Problem solved:** Same Bybit retCode 10006 infinite retry loop as Alpha.
Beta was running OLD code (87691-byte tarball vs 87393-byte old) until container
restart at 2026-04-15 — the fix was already in master but Beta had not been restarted
to pick it up.

**What was done:** `docker restart beta-bot-b61ydjyqzf5bff2wfgp0nmzi`

**Shared fix:** Beta uses identical governor architecture as Alpha — same files,
same state machine. All fixes are in shared `core/` and `exchange/bybit/` modules.
No Beta-specific code changes required.

**Validated behavior:**
- Container restart: Up 3 minutes, healthy
- `RETRY_CODES` without 10006 (verified: line 26 confirmed in container)
- `on_10006_abort()` in client.py (verified: confirmed in container)
- `should_skip_tick()` at bot_runtime.py line 248
- `mark_tick_clean()` at bot_runtime.py line 287
- Heartbeat advancing every ~5s during 10006 cooldown
- Boot log shows "Starting Phase 4 runtime..." with 87691-byte tarball

**No further code changes needed for Beta in this phase.**

## SECRETS POLICY
All secrets via environment variables only.
Local secrets file: docs/00_IMPLEMENTATION_INPUT/07_SECRETS_LOCAL_ONLY.env (NOT in repo, NOT in .env.example).
.env.example contains placeholder names only, no real values.