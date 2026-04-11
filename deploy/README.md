# Coolify Deployment Guide — Trading Portfolio System

## Prerequisites

1. **Coolify instance** with Docker build capability
2. **Git repository** with this project pushed (Coolify builds from it directly)
3. **Six Bybit API keys** with correct permissions (see below)

## Bybit API Key Requirements

| Bot | Key Name | Subaccount | Permissions |
|-----|----------|------------|-------------|
| alpha-bot | `BYBIT_API_KEY_ALPHA_BOT` | BYBIT_ALPHA | READ + TRADE |
| beta-bot  | `BYBIT_API_KEY_BETA_BOT`  | BYBIT_BETA  | READ + TRADE |
| gamma-bot | `BYBIT_API_KEY_GAMMA_BOT` | BYBIT_GAMMA | READ + TRADE |

The portfolio-supervisor needs **no API credentials** — it is read-only.

## How It Works

Coolify detects the `Dockerfile` and `docker-compose.yml` in the repository root and builds the image on the runner. No registry, no manual push — just point Coolify at the Git repo.

## Coolify Resource Structure

Deploy as **one docker-compose application** with all 4 services running together.

## Environment Variables to Set in Coolify

Since all non-secret variables are baked into `docker-compose.yml`, you only need to set the **6 real secrets** in Coolify's application-level environment variables.

| Variable | Value |
|----------|-------|
| `BYBIT_API_KEY_ALPHA_BOT` | `<your real alpha API key>` |
| `BYBIT_API_SECRET_ALPHA_BOT` | `<your real alpha API secret>` |
| `BYBIT_API_KEY_BETA_BOT` | `<your real beta API key>` |
| `BYBIT_API_SECRET_BETA_BOT` | `<your real beta API secret>` |
| `BYBIT_API_KEY_GAMMA_BOT` | `<your real gamma API key>` |
| `BYBIT_API_SECRET_GAMMA_BOT` | `<your real gamma API secret>` |

All other env vars (`BOT_NAME`, `SUBACCOUNT_NAME`, `PYTHONPATH`, `STATE_DIR`, etc.) are already defined per-service in `docker-compose.yml` — Coolify passes them automatically to each container.

## Persistent Volumes

In the Coolify application settings, configure these as **named persistent volumes**:

| Volume Name | Container Path | Purpose |
|-------------|---------------|---------|
| `trading-state`     | `/app/state`            | Bot state snapshots |
| `trading-heartbeat` | `/app/heartbeat`        | Heartbeat files (5s interval) |
| `trading-logs`      | `/app/logs`             | Structured log output |
| `trading-journals`  | `/app/data/journals`     | Trade journals |
| `trading-snapshots` | `/app/data/snapshots`    | Periodic risk snapshots |
| `trading-cache`     | `/app/data/cache`        | Instrument filter cache |

Coolify creates these automatically from the `volumes:` section in `docker-compose.yml` the first time the application is deployed.

> In Coolify, set each volume as a **named persistent volume** (Coolify's volume manager). These survive deployments and server restarts.

## Health Checks

Each container uses a **file-based heartbeat health check**:

```
test: find /app/heartbeat/{bot_id}_heartbeat.json -mmin -2 -print
interval: 30s | timeout: 10s | retries: 3 | start_period: 60s
```

- Bot writes heartbeat every **5 seconds** to `/app/heartbeat/{bot_id}_heartbeat.json`
- If the file is missing or older than **2 minutes**, Docker marks container unhealthy → restart
- `start_period: 60s` gives the bot time to fetch instrument info and write its first heartbeat

The supervisor's healthcheck monitors `alpha_bot`'s heartbeat as a proxy for overall system aliveness.

## Verifying 24/7 Operation in Coolify

### 1. Container Status
In Coolify dashboard → each resource:
- **Status column**: Shows `Running` (green)
- **Uptime**: Shows time since last deployment
- **Health**: Shows `Healthy` once first heartbeat is written

### 2. Container Logs
Click **Logs** on any bot resource:
- You should see the startup sequence:
  ```
  [alpha_bot] Starting Phase 4 runtime...
  [alpha_bot] Subaccount: BYBIT_ALPHA
  [alpha_bot] Symbol: BTCUSDT
  [alpha_bot] Capital: 50 USDT
  [alpha_bot] Tick loop: 5s interval
  [alpha_bot] Entering tick loop — Ctrl-C to stop
  ```
- Then every 5s:
  ```
  [alpha_bot] tick | signals=0 | frozen=False
  ```

### 3. Heartbeat Files (via Coolify terminal)
```bash
# Check inside any container
docker exec alpha-bot cat /app/heartbeat/alpha_bot_heartbeat.json

# Expected output:
{
  "bot_id": "alpha_bot",
  "timestamp": "2026-04-11T20:00:00+00:00",
  "status": "running",
  "is_frozen": false,
  "last_error": null
}
```

### 4. Supervisor Logs
Check supervisor logs for the poll output:
```
[portfolio_supervisor] alpha_bot=ok | beta_bot=ok | gamma_bot=ok
```
Every 5 seconds, no alerts = all bots healthy.

### 5. Coolify Deployment Checklist

```
[ ] All 4 resources show "Running" status
[ ] All 4 resources show "Healthy" health
[ ] Logs show tick loop running (not crashing)
[ ] Supervisor logs show "ok" for all 3 bots
[ ] No repeated restarts (restart count = 0 in Docker stats)
[ ] Heartbeat files exist in mounted volume
```

## Restart Behavior

| Event | Behavior |
|-------|----------|
| Bot process crashes | Docker restarts container (restart policy: unless-stopped) |
| Bot hangs/frozen | Supervisor detects stale heartbeat → logs FROZEN/STALE alert |
| Server restarts | Coolify restarts all containers automatically |
| Coolify redeploys | Containers restart with same persistent volumes |
| Manual stop | Container stays stopped (unless-stopped) |

## Alerting (Phase 4)

The supervisor logs these to stdout (visible in Coolify logs):

| Alert | Meaning | Action |
|-------|---------|--------|
| `STALE` | Bot heartbeat older than 15s | Check if bot container crashed |
| `FROZEN` | Bot `is_frozen=True` | Normal if daily loss cap hit |
| `DRAWDAY` | Daily loss ≥ 80% of 2.0% cap | Review positions |
| `DRAWWEK` | Weekly loss ≥ 80% of 5.0% cap | Review strategy |
| `ANOMALY` | ≥3 consecutive losses | Review market conditions |

Phase 5 (not yet implemented): Slack/PagerDuty webhook alerting.

## Disk Space

- **Heartbeat files**: < 1 KB/day per bot
- **Logs**: ~5–50 MB/day depending on market activity
- **Snapshots**: ~1–5 KB each, written on state change + every 60s
- **Journals**: ~100–500 bytes per trade

Total estimated disk usage: **~50–200 MB per month** with active trading.
