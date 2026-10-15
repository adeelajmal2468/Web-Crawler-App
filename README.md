# Pakistan PlotWatch: Android + Cloud Crawler

An installable Android front end and a cloud-deployable Python crawler, adapted from the PlotWatch 4.1 open-web version.

## Included

* `android/PlotWatchAndroid/` — native Java Android project (search, plot results, unverified URL evidence, tracked plots, cloud settings, scan history)
* `backend/` — FastAPI REST service; reuses crawler.py, engine.py, serper_client.py, and database.py; SQLite-backed job queue and 15-day scan scheduler
* `docs/DEPLOY_TO_RAILWAY.md` — cloud deployment instructions with persistent volume and HTTPS
* `.github/workflows/build-android-apk.yml` — build an APK using GitHub Actions without installing Android Studio

## Fastest path to give your supervisor an APK

1. Follow [Deploy to Railway](docs/DEPLOY_TO_RAILWAY.md) first. Save the HTTPS URL and your access token.
2. Follow [Build APK](android/PlotWatchAndroid/BUILDING.md) via Windows/Android Studio **or GitHub Actions**.
3. Install `app-debug.apk` on the supervisor's Android phone.
4. Open the app's **Settings** and enter the Railway HTTPS URL and the access token; tap **Test server and login**.
5. Search a plot, then open **Tracked** to see saved plots and evidence.

**Never embed SERPER_API_KEY into Android source, `.env` into GitHub, or commit an APK preconfigured with backend secrets.**

## Tests

Python offline integration tests:

    cd backend
    python -m unittest discover -s tests -v

## Status / honest limitations

* Backend API tests run locally using mock crawler responses. Actual live Serper/search results depend on account/API credits and website access.
* The APK has not been compiled in this workspace because Android SDK/Gradle are not installed here. The source and Windows/GitHub Actions build instructions are supplied; an installable APK is produced once the build succeeds.
* This is a single-user/supervisor pilot (shared bearer token, one server process + one persistent SQLite volume), not a hardened production SaaS app.
* The system records *advertisements* and verification evidence, not a legal guarantee of plot sale status.
