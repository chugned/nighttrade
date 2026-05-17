# Deploying nighttrade — track it live from anywhere

This runs the observer + dashboard as always-on background services on your
Mac and publishes the dashboard over **Tailscale**, so you can open it from
your phone or laptop anywhere.

> Observation / paper simulation only. Nothing here can place a real trade.

---

## 0. Location matters

macOS privacy protection (TCC) **blocks background services from reading
`~/Desktop`, `~/Documents` and `~/Downloads`.** The live deployment must live
somewhere else — `~/nighttrade` is used here. Edit wherever you like, then push
changes to the live copy with `deploy/sync.sh`.

## 1. Run nighttrade as a background service

```bash
cd ~/nighttrade                               # NOT a path under ~/Desktop
python3 -m pip install -e ".[dev,online]"     # if not already installed
deploy/install.sh                             # installs the launchd services
```

This creates two macOS LaunchAgents that start on login, restart on crash,
and need no open terminal:

| Service | What it does |
|---|---|
| `com.nighttrade.observer`  | the 24/7 live S&P 500 observer (`observe --live`) |
| `com.nighttrade.dashboard` | the dashboard on `127.0.0.1:8001` |

The observer runs under `caffeinate -s`, so the Mac will not system-sleep
while the bot is alive.

```bash
launchctl list | grep nighttrade        # check they are running
tail -f logs/com.nighttrade.observer.err.log   # watch the observer
deploy/uninstall.sh                      # stop + remove the services
```

To use a different dashboard port: `deploy/install.sh 9000`.

---

## 2. Install Tailscale

Tailscale is a private mesh network — your devices reach each other directly,
no port-forwarding, no public exposure.

1. Install the Tailscale app (App Store, or `brew install --cask tailscale`).
2. Open it and **sign in** — this puts your Mac on your *tailnet*.
3. Confirm the CLI works: `tailscale status`.

---

## 3. Publish the dashboard

### Private — your devices only (recommended)

```bash
tailscale serve --bg 8001
```

The dashboard is now at **`https://<your-mac>.<tailnet>.ts.net`** — a real
HTTPS URL, reachable from any device signed into *your* tailnet (your phone,
your laptop), and **nobody else**. `tailscale serve status` shows the URL.

### Public — a link anyone can open

```bash
tailscale funnel --bg 8001
```

Same URL, but reachable from the public internet. Use this only if you want a
shareable link. Funnel must be enabled for your tailnet in the Tailscale admin
console (Settings → Funnel). Consider putting a password in front first
(`tailscale serve` + Tailscale's access controls, or a reverse proxy).

To stop publishing: `tailscale serve --bg 8001 off` (or `funnel ... off`).

---

## The one real caveat

Your Mac is the host, so the bot is live only while the Mac is **powered on
and awake**. `caffeinate -s` stops *system* sleep, but a laptop with the lid
closed on battery still sleeps. For genuine 24/7:

- keep the Mac plugged in, or
- System Settings → Lock Screen / Battery → never sleep, or
- run it on an always-on box instead (see the Fly.io / VPS options — the
  `observe --live` + `dashboard` commands are identical anywhere).

---

## Quick reference

```bash
deploy/install.sh            # install + start the services
deploy/sync.sh               # push code changes to ~/nighttrade + restart
deploy/uninstall.sh          # stop + remove them
launchctl list | grep night  # service status
python3 -m nighttrade status # observatory status (cycles, safety score)
tailscale serve status       # the public URL
```
