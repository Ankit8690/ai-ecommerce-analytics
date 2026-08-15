# Deployment Guide — Original Phase 13

This walkthrough deploys the working local Docker stack to **Render.com**
using the committed [`render.yaml`](../render.yaml) blueprint. Render is
chosen because it (a) supports persistent managed PostgreSQL on a real
free tier, (b) accepts our existing `Dockerfile` without modification, and
(c) publishes both web services on HTTPS URLs suitable for interview demos.

Total hands-on time after the prerequisites: **~15 minutes**.

---

## Prerequisites (once)

1. **Push this repo to GitHub** (public or private). The blueprint reads
   from the linked repo.
   ```bash
   gh repo create ai-ecommerce-analytics --public --source=. --push
   ```
   If you don't have the GitHub CLI, create the repo in the browser and:
   ```bash
   git remote add origin https://github.com/<you>/ai-ecommerce-analytics.git
   git push -u origin main
   ```
2. **Create a Render account** at https://render.com — sign in with the
   same GitHub account so Render can see your repo.
3. **Have your Gemini API key ready** (from https://aistudio.google.com/).
   You will paste it into the Render dashboard, not into Git.

---

## Step 1 — Create the Blueprint

1. In Render → **New +** → **Blueprint**.
2. Select the repo you pushed above.
3. Render reads `render.yaml` and lists three resources:
   - `ecommerce-postgres` (managed PostgreSQL 16, free 90-day plan)
   - `ecommerce-api` (Docker web service, free)
   - `ecommerce-dashboard` (Docker web service, free)
4. Click **Apply**. Render provisions the database (~2 min) and starts
   building the two Docker images (~4-6 min the first time — the RAG
   index is baked at build time so no external dependencies are needed).

## Step 2 — Add secrets

Both web services have two variables marked `sync: false` in the
blueprint — they must be set manually so they never enter Git:

For **`ecommerce-api`** → *Environment*:
| Key | Value |
|---|---|
| `LLM_API_KEY` | your Gemini key (`AIza…`) |
| `CORS_ALLOW_ORIGINS` | *(fill in during Step 4)* |

For **`ecommerce-dashboard`** → *Environment*:
| Key | Value |
|---|---|
| `LLM_API_KEY` | same Gemini key (dashboard uses it for direct RAG / rec calls) |

Save. Render redeploys automatically.

## Step 3 — Seed the database (one-shot, from your laptop)

The blueprint provisions an empty database. Load schema + 8 CSVs + views
by pointing your local `seed.py` at Render's **External Database URL**.
This avoids the Starter-tier-only Render Shell and works on the free plan.

1. In Render → **`ecommerce-postgres`** → **Info** → copy the **External
   Database URL**. It looks like
   `postgresql://ecommerce_app:xxxx@dpg-abc-a.oregon-postgres.render.com/ecommerce_ai`.
2. In your local Git Bash (from the repo root):
   ```bash
   # Rewrite the URL with the +psycopg driver hint SQLAlchemy expects,
   # and set it for this shell only (do NOT put it in .env):
   export DATABASE_URL="postgresql+psycopg://ecommerce_app:xxxx@dpg-abc-a.oregon-postgres.render.com/ecommerce_ai"
   export DATABASE_URL_READONLY="$DATABASE_URL"   # Render exposes one role
   export DATASET_RAW_DIR="Dataset/raw"

   # 3a. Load schema + all 8 CSVs (~2-3 min over your uplink)
   .venv/Scripts/python.exe database/seed.py

   # 3b. Create the 9 analytics views (~2 s)
   .venv/Scripts/python.exe scripts/apply_analytics_schema.py
   ```
   Expected: `[verify] ALL CHECKS PASSED ✓` then
   `Applied analytics_schema.sql to dpg-abc-a.oregon-postgres.render.com/ecommerce_ai`.

3. Verify the numbers match the known truth values:
   ```bash
   .venv/Scripts/python.exe -c "
   import os
   from sqlalchemy import create_engine, text
   e = create_engine(os.environ['DATABASE_URL'])
   with e.connect() as c:
       print('orders  :', c.execute(text('SELECT COUNT(*) FROM public.orders')).scalar())
       print('views   :', c.execute(text(\"SELECT COUNT(*) FROM information_schema.views WHERE table_schema='analytics'\")).scalar())
       print('gmv     :', c.execute(text('SELECT product_gmv FROM analytics.v_executive_kpis')).scalar())
   "
   ```
   Expected: `orders 99441`, `views 9`, `gmv 15843553.24`.

4. Close the terminal (or `unset DATABASE_URL`) so your local project keeps
   using your local `.env` — you don't want to accidentally point your dev
   work at the cloud DB.

> **Why not automatic on first container start?** Rerunning the seed against
> a live DB would error on the existing tables. Making seeding an explicit
> one-shot from your laptop is safer than a fragile idempotency dance.

## Step 4 — Wire CORS

After the two web services are live, Render gives you the URLs
(something like `https://ecommerce-dashboard.onrender.com` and
`https://ecommerce-api.onrender.com`).

1. Copy the dashboard URL.
2. Set `CORS_ALLOW_ORIGINS` on `ecommerce-api` to that URL.
3. Save → Render redeploys the api in ~30 s.

## Step 5 — Smoke test

Replace `<api-url>` / `<dashboard-url>` with your actual Render URLs.

```bash
# 1. API health
curl -fsS https://<api-url>/health
# → {"status":"ok","database_connected":true,"version":"1.0.0"}

# 2. Executive KPIs — should match the known truth values
curl -fsS https://<api-url>/api/kpis
# → total_orders 99,441 · product_gmv 15,843,553.24 · cash_collected 16,008,872.12

# 3. Analyst
curl -fsS -X POST https://<api-url>/api/analyst \
  -H "Content-Type: application/json" \
  -d '{"question":"top 3 least reviewed products"}'
```

Then open `https://<dashboard-url>/` and click through every sidebar item:

- Executive Overview, Sales & Revenue, Products & Categories, Customers &
  Segments, Customer Experience, Delivery & Operations, Sales Forecasting
- AI Business Analyst, Question Library, SQL Query, Knowledge Base,
  Recommendations

## Step 6 — (Optional) Add a dedicated read-only role

Render's managed Postgres exposes one connection string. For defence-in-depth,
create a dedicated read-only role and update `DATABASE_URL_READONLY`:

In Render → `ecommerce-postgres` → **Connect** → **PSQL Command**:

```sql
CREATE ROLE ecommerce_readonly WITH LOGIN PASSWORD '<generate a strong password>';
GRANT CONNECT ON DATABASE ecommerce_ai TO ecommerce_readonly;
GRANT USAGE ON SCHEMA public, analytics TO ecommerce_readonly;
GRANT SELECT ON ALL TABLES IN SCHEMA public, analytics TO ecommerce_readonly;
ALTER DEFAULT PRIVILEGES IN SCHEMA public, analytics GRANT SELECT ON TABLES TO ecommerce_readonly;
```

Then rewrite `DATABASE_URL_READONLY` on both web services to point at the
`ecommerce_readonly` user + password. The SQL validator already blocks
DML/DDL, but the DB layer is the ultimate guarantee.

---

## Free-tier caveats (be aware for the interview demo)

- **Cold starts.** Free-tier web services sleep after 15 min idle. The
  first request after sleep takes ~30-45 s to wake. For a live demo, hit
  `/health` a minute before you present.
- **90-day database.** Render's free Postgres expires after 90 days.
  Upgrade to Starter ($7/mo) before then or export + reimport.
- **Bandwidth / hours.** Free-tier limits are generous for a portfolio
  demo but not for real traffic.

## Alternative deploy targets

Anything that runs `docker compose up` will work: DigitalOcean droplet
(~$4-6/mo, always-on), a Hetzner CX11, or your own VPS. The blueprint
above is Render-specific but the underlying Dockerfile / compose is
platform-agnostic.

---

## Rollback

```bash
# Local — revert code
git revert <commit>
git push

# Render redeploys automatically from the new HEAD.
```

## Wiping and re-seeding the deployed DB

1. Render → `ecommerce-postgres` → **Recreate**. Get a new connection string.
2. Rerun **Step 3** in the api shell.

## Cost summary

| Resource | Free tier | Paid |
|---|---|---|
| PostgreSQL | 90 days, then removed | Starter $7/mo |
| API web service | 750 h/mo, sleeps idle | Starter $7/mo, always-on |
| Dashboard web service | 750 h/mo, sleeps idle | Starter $7/mo, always-on |

For interview-ready always-on hosting, budget **~$21/mo** total.
