# ☕ BrewLog — Home Assistant Add-on

> **Written entirely by [Claude](https://claude.ai) (Anthropic's AI assistant), based on a conversation with the user. Not a single line of code was written by hand.**

BrewLog is a coffee brewing tracker that runs as a native add-on inside [Home Assistant](https://www.home-assistant.io/). It lives in your HA sidebar alongside your dashboards and automations, and stores all data locally on your device — no cloud, no subscriptions, no accounts.

---

## Features

- **Products** — catalogue your coffees with name, roaster, roast date, purchase date, photo and notes. Sorted by most recently brewed.
- **Brew logging** — log every session with method, dose, water weight, grind size, brew time (via a built-in timer), and notes. Live ratio calculation as you type.
- **Brew history** — tap any coffee to see its full history and stats.
- **Timer** — count-up timer with start, stop, reset and manual time entry.
- **Settings** — default method, default dose, ratio display toggle, numerical vs descriptive grind scale, vibration on timer stop.
- **Consistent design** — warm earthy colour palette designed to feel at home on both mobile and desktop HA frontends.

---

## Installation

### Prerequisites
- Home Assistant (any recent version)
- Samba share add-on **or** SSH access to your HA instance
- A GitHub account (to host the add-on repository)

### Steps

1. Fork or clone this repository to your own GitHub account.

2. In Home Assistant, go to:
   **Settings → Add-ons → Add-on Store → ⋮ menu → Repositories**

3. Add your repository URL (e.g. `https://github.com/your-username/brewlog`)

4. BrewLog will appear under **Community Add-ons**. Click **Install**.

5. Wait for the image to build (2–5 minutes on HA Green / Raspberry Pi).

6. Click **Start**, then enable **Show in sidebar** for a permanent ☕ link.

---

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                 Home Assistant                       │
│                                                      │
│  Sidebar panel ──► HA Ingress proxy                 │
│                         │                            │
│                         ▼                            │
│              Docker container (brewlog)              │
│              ┌──────────────────────┐               │
│              │  Python / Flask      │               │
│              │  serving port 8099   │               │
│              │         │            │               │
│              │  SQLite database     │               │
│              │  /data/brewlog.db    │               │
│              └──────────────────────┘               │
│                         │                            │
│              Persistent data volume                  │
│              (survives restarts & updates)           │
└─────────────────────────────────────────────────────┘
```

### Tech stack

| Layer | Technology |
|-------|-----------|
| Runtime | Python 3 on Alpine Linux |
| Web framework | [Flask](https://flask.palletsprojects.com/) |
| Database | SQLite (via Python's built-in `sqlite3`) |
| Frontend | Vanilla HTML/CSS/JavaScript — no frameworks, no build step |
| Container base | `ghcr.io/home-assistant/aarch64-base:latest` |
| HA integration | Ingress panel (port 8099), persistent `/data` volume |

### Why no nginx / gunicorn?

Early versions of this add-on used nginx as a reverse proxy in front of gunicorn. This caused persistent S6-overlay init issues on HA Green. The final architecture has Flask serve directly on port 8099 — perfectly adequate for a single-user local app, and dramatically simpler to maintain.

### File structure

```
brewlog-addon/
├── config.yaml                  ← HA add-on metadata, ingress config
├── Dockerfile                   ← Alpine + Python + Flask
├── build.yaml                   ← Base images per architecture
├── repository.yaml              ← Required at repo root for HA to accept it
├── README.md                    ← This file
├── DOCS.md                      ← In-app documentation tab
└── rootfs/
    └── app/
        ├── app.py               ← Flask app, SQLite schema, all API routes
        └── templates/
            └── index.html       ← Entire frontend: HTML + CSS + JS (~1100 lines)
```

### API routes

All routes are served by Flask and prefixed automatically with the HA ingress path.

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Serve the single-page app |
| GET | `/api/products` | List all products |
| POST | `/api/products` | Create product |
| GET | `/api/products/<id>` | Get product + brew history |
| PUT | `/api/products/<id>` | Update product |
| DELETE | `/api/products/<id>` | Delete product (cascades to brews) |
| GET | `/api/brews` | List recent brews |
| POST | `/api/brews` | Log a brew |
| DELETE | `/api/brews/<id>` | Delete a brew |
| GET/POST | `/api/settings` | Read/write settings |
| POST | `/api/clear` | Delete all data |

### Database schema

**products**
```sql
id, name, brand, photo_data (base64), roast_date, purchase_date,
notes, created_at, last_brewed_at, brew_count
```

**brews**
```sql
id, product_id (FK→products), product_name, brew_method,
coffee_weight_g, water_weight_g, grind_size, brew_time_secs,
notes, brewed_at
```

**settings**
```sql
key, value  (simple key-value store)
```

---

## Security

BrewLog relies on **Home Assistant's ingress system** for access control:

- Port 8099 is bound inside Docker's internal network only — it is not directly reachable from outside the container
- All traffic must pass through HA's ingress proxy, which requires an active HA session
- If you expose HA externally (e.g. via Cloudflare Tunnel), HA's own authentication protects the add-on

**Recommended:** If using Cloudflare Tunnel, add a [Cloudflare Access](https://developers.cloudflare.com/cloudflare-one/policies/access/) policy requiring your personal email before reaching the HA login page. This gives you two independent auth layers.

---

## Development

### Making changes

1. Edit files locally
2. Push to your GitHub repo
3. In HA: Settings → Add-ons → BrewLog → ⋮ → **Rebuild**

The rebuild pulls fresh source from GitHub and rebuilds the Docker image. Your data in `/data/brewlog.db` is preserved.

### Bumping the version

Update the `version` field in `config.yaml`. HA uses this to detect updates.

---

## Data & Backups

All brew data lives in `/data/brewlog.db` inside the add-on's persistent volume. This is:

- **Preserved** across restarts, rebuilds and add-on updates
- **Included** in Home Assistant's built-in backup system (Settings → System → Backups)
- **A standard SQLite file** — you can download it via Samba and open it with any SQLite browser

---

## About

BrewLog was conceived, designed and built entirely through a conversation with **Claude** (claude.ai), Anthropic's AI assistant. The user described what they wanted — an Android-style coffee tracking app that could run as a Home Assistant add-on on their HA Green — and Claude wrote every line of code, debugged every build error from supervisor logs, and iterated on the architecture until it worked.

The project went through several architectural evolutions:

1. **Android app** (Kotlin/XML) — the original request
2. **HA add-on with nginx + gunicorn + S6** — overcomplicated, persistent init failures
3. **HA add-on with Flask direct** — current architecture, simple and stable

Total human code written: **0 lines.**

---

## Hardware

Tested on **Home Assistant Green** (aarch64). Also supports amd64 and armv7 via `build.yaml`.
