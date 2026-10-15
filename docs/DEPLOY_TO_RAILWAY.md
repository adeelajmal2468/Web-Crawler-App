# Host the crawler for use anywhere (Railway)

Architecture: Android APK (HTTPS) -> FastAPI -> Serper discovery -> bounded, robots-aware site crawler -> persistent SQLite volume -> 15-day rechecks in a single always-on process.

## 1. Push project to GitHub

Create an empty private GitHub repository, extract this ZIP, and upload the whole project root. Use VS Code or Git for version control. No API key goes into the repository.

## 2. Create a Railway service

1. Log in to https://railway.com . Create a new Railway project and service **from your GitHub repository**.
2. In the service **Settings**, set **Root Directory** to `/backend`.
3. Railway detects `/backend/Dockerfile`. The container launches `uvicorn api:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1`.
4. In **Variables**, add exactly:

    `SERPER_API_KEY` = your real Serper key

    `PLOTWATCH_APP_TOKEN` = a long random string (at least 32 random bytes; example generation below)

    `PLOTWATCH_DATA_DIR` = `/data`

5. In the Railway project canvas, attach a **persistent Volume to this service**, with mount path `/data`. Check your plan's volume eligibility and pricing first.
6. Keep **one replica**, do not enable service sleeping/serverless, and use the service plan suitable for continuous running. A local SQLite file plus in-process scheduler cannot safely scale across multiple replicas.
7. In **Settings > Networking**, choose **Generate Domain**. Example: `https://your-service.up.railway.app`. Railway handles HTTPS.
8. Open `https://your-service.up.railway.app/health`. It should show `{"status":"ok","service":"Pakistan PlotWatch"}`.
9. Test an authenticated request (PowerShell):

    `$headers = @{ Authorization = "Bearer YOUR_LONG_APP_TOKEN" }`

    `Invoke-RestMethod -Uri "https://your-service.up.railway.app/api/watches" -Headers $headers`

10. In the installed Android app open **Settings** and enter the HTTPS URL and the **app access token**, not the Serper key. Select **Test server and login**.

## Generate a secure access token

    python -c "import secrets; print(secrets.token_urlsafe(32))"

Keep this token private. The Android pilot uses one shared access token for all supervisors/devices and stores it in app-private preferences. This is a demonstration authentication model, not a production multi-user identity system. Anyone with the token can access all watches. Rotate the token if a phone or APK configuration is compromised.

## How does the 15-day monitoring work?

* `POST /api/watches` saves a plot and queues a background job.
* A single server worker checks queued jobs roughly every 20 seconds while the service is running.
* On a fully completed crawl, the database sets the next check for 15 days later.
* A failed or partial crawl is scheduled to retry in one day.
* The app itself need not remain open. However, Railway must stay up, with the same persistent volume. If your account is paused or credits expire, automatic checks stop.
* The app currently shows statuses when opened. Push notifications and email alerts are not included.

## Important crawling limitations

* A URL is **discovered** from search results first; only permitted accessible HTML is parsed as webpage evidence.
* It cannot bypass website permission restrictions, CAPTCHAs, logins, or JavaScript-only data.
* Some sites may block Railway data center IPs even when they work on your laptop.
* Plot identifiers are ambiguous between societies/blocks; a snippet match alone is never a verified sale.
* No results means no match on the accessible sampled pages, NOT a verified claim that the plot is not for sale.
* Search credits, Railway compute time, and persistent volumes may incur charges.

## Backend local smoke test (without Android)

    cd backend
    python -m venv .venv
    .venv\Scripts\activate
    pip install -r requirements.txt
    copy .env.example .env

Edit `.env` to set the real keys. Do **not** run with the example placeholders. Start:

    uvicorn api:app --host 127.0.0.1 --port 8000

And browse `http://127.0.0.1:8000/health`. Local HTTP is for developer checks only; Android production app accepts HTTPS exclusively.

## Operational limitations

The worker runs inside the same single web process. For a classroom pilot this is simplest. For a large number of users, switch to managed PostgreSQL, a dedicated queue worker and separate scheduler before scaling horizontally. Railway deployment of this package is not yet verified with real credentials; run a real search and inspect the log before distributing the APK.
