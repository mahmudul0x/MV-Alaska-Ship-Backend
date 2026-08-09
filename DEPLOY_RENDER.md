# Render deploy — MV Alaska backend (temporary demo)

Temporary demo deploy: Render (web service) + Supabase Postgres (Transaction
Pooler, port 6543). Not the final production setup.

## Render service settings

| Setting | Value |
|---|---|
| Root Directory | `backend` (this folder) |
| Runtime | Python 3 |
| Build Command | `pip install -r requirements.txt` |
| Start Command | *(from Procfile)* `python manage.py migrate && python manage.py collectstatic --noinput && gunicorn config.wsgi:application` |
| Instance type | Free |

Or use the committed `render.yaml` via **New → Blueprint**.

## Environment variables (set on Render → Environment)

| Variable | Required | Description |
|---|---|---|
| `DATABASE_URL` | ✅ | Supabase **Transaction Pooler** connection string (host `...pooler.supabase.com`, port **6543**). Include `?sslmode=require`. |
| `SECRET_KEY` | ✅ | Long random string. Render can auto-generate (`generateValue`). |
| `DEBUG` | ✅ | `False`. |
| `ALLOWED_HOSTS` | ✅ | Your Render host, e.g. `mv-alaska-backend.onrender.com` (comma-separated for multiple). |
| `CORS_ALLOWED_ORIGINS` | ✅ | Frontend origin(s), e.g. `https://your-app.vercel.app` (comma-separated, **no trailing slash**). |
| `BACKEND_URL` | ✅ | Full https URL of this service, e.g. `https://mv-alaska-backend.onrender.com`. Used to build SSLCommerz callback URLs. |
| `FRONTEND_URL` | ✅ | Vercel app URL, e.g. `https://your-app.vercel.app`. Used for post-payment redirects. |
| `DRF_NUM_PROXIES` | ✅ | `1` normally (Render sits behind one proxy). **`2` if the frontend proxies `/api` to this service** — see "Getting onrender.com out of customer links" below. Wrong here means every visitor shares one throttle bucket. |
| `PUBLIC_API_BASE_URL` | ⬜ | Origin used to build customer-facing invoice links. Leave blank to build from the request host. See below. |
| `SSLCOMMERZ_STORE_ID` | ✅ | SSLCommerz store id (sandbox for demo). |
| `SSLCOMMERZ_STORE_PASSWORD` | ✅ | SSLCommerz store password. |
| `SSLCOMMERZ_IS_SANDBOX` | ✅ | `True` for the demo. |
| `EMAIL_BACKEND` | ⬜ | Default `...console.EmailBackend` (prints to logs). Set SMTP backend to send real invoice emails. |
| `EMAIL_HOST` / `EMAIL_PORT` / `EMAIL_USE_TLS` | ⬜ | SMTP settings if using real email. |
| `EMAIL_HOST_USER` / `EMAIL_HOST_PASSWORD` | ⬜ | SMTP credentials. |
| `DEFAULT_FROM_EMAIL` | ⬜ | From address on outgoing mail. |
| `BOOKING_HOLD_MINUTES` | ⬜ | Default `30`. |
| `PAYMENT_SESSION_MINUTES` | ⬜ | Default `30`. |
| `BALANCE_DUE_REMINDER_DAYS` | ⬜ | Default `2`. |
| `AUTHORITY_PHONES` | ⬜ | Comma-separated helpline numbers on reports/invoices. |

`PYTHON_VERSION` (e.g. `3.12`) can be set to pin the runtime.

## Notes for the demo

- **Free tier has no cron**, so the payment reconciliation jobs from
  `DEPLOYMENT.md` won't run. Fine for a walkthrough; not for real money.
- Free web services **sleep after inactivity** — the first request after idle
  takes ~30–60s to wake. Warm it up before the client demo.
- Supabase pooler (6543) is already handled in `settings.py`
  (`conn_max_age=0`, `DISABLE_SERVER_SIDE_CURSORS=True`). Do **not** switch to
  the direct connection (5432) for the web service on Render's free tier.

## Frontend (Vercel)

Set this on Vercel → Project → Environment Variables:

```
VITE_API_BASE_URL=https://<your-render-host>.onrender.com/api
```

(The frontend reads `VITE_API_BASE_URL` and expects the `/api` suffix.)

## Getting onrender.com out of customer links

Invoice download links are absolute URLs, and by default they carry whatever
host this service answers on — so a customer's receipt reads
`https://<your-render-host>.onrender.com/api/invoices/…`. That is not a secrecy
problem (the API origin is compiled into the frontend bundle either way), but
it looks unbranded and the link dies if the backend ever moves. Two ways to fix
it; pick one, not both.

### Option A — custom domain on this service (recommended)

1. Render → Settings → **Custom Domains** → add `api.<your-domain>`.
2. At your DNS provider, add the CNAME record Render shows you. Render issues
   the TLS certificate itself, usually within a few minutes.
3. Then set:
   - `ALLOWED_HOSTS` → include `api.<your-domain>`
   - `PUBLIC_API_BASE_URL` → `https://api.<your-domain>`
   - `BACKEND_URL` → `https://api.<your-domain>` (SSLCommerz callbacks now
     carry the branded domain too, so the mid-payment redirect stops flashing
     onrender.com)
   - `CORS_ALLOWED_ORIGINS` → the website origin, e.g.
     `https://www.<your-domain>` (still a cross-origin call — a different
     subdomain is a different origin)
   - `DRF_NUM_PROXIES` → stays **1**
   - Vercel: `VITE_API_BASE_URL` → `https://api.<your-domain>/api`
4. Remove the `/api` rewrite from `alaskaShip-frontend/vercel.json`, or it sits
   there as dead config that will confuse the next person.

No extra network hop, no proxy bandwidth, and the payment redirect is branded
as well. Costs one DNS record.

### Option B — proxy `/api` through the website host

`alaskaShip-frontend/vercel.json` rewrites `/api/*` to this service, so every
call is same-origin and the customer only ever sees the website's domain.

- Vercel: `VITE_API_BASE_URL` → `/api`
- `PUBLIC_API_BASE_URL` → the website origin, e.g. `https://www.<your-domain>`
  (match the canonical host exactly — if the site serves on the apex and `www`
  only redirects, use the apex, or every download takes a needless redirect)
- `BACKEND_URL` → **leave as the onrender.com URL**. SSLCommerz callbacks are
  server-to-server and must reach this service directly, not through Vercel.
- `DRF_NUM_PROXIES` → **`2`**. This is the trap: there is now an extra hop
  (browser → Vercel → Render → Django), so at `1` DRF reads Vercel's egress IP
  as the client and *every visitor lands in the same throttle bucket* — the
  10/min booking-lookup limit would then be shared by the whole internet.
  Confirm after deploy by hitting a throttled endpoint from two different
  networks and checking they do not exhaust each other's budget.

Note that the payment redirect still passes through the backend's own host, so
onrender.com flashes briefly during checkout. Only Option A removes that.
