# Building an installable APK

The Android application is a Java native Android project (not a Streamlit WebView). The APK contains no secret keys or crawler logic; it talks to the Python cloud API via HTTPS.

## Option A: Android Studio on Windows

1. Install Android Studio from https://developer.android.com/studio .
2. Install Android SDK Platform 35, SDK Build Tools 35.x and JDK 17 (Android Studio includes a suitable JBR).
3. Extract the entire project. Open **android/PlotWatchAndroid** using **Open** in Android Studio.
4. If Android Studio asks for a Gradle distribution, choose local Gradle 8.9 after running `BUILD_APK_WINDOWS.bat` once (which downloads it under `.local-gradle/gradle-8.9`). This project includes a bootstrap batch script instead of the binary Gradle wrapper JAR.
5. On Windows, double-click `BUILD_APK_WINDOWS.bat` to download Gradle (once) and compile the Android debug APK. If the Android SDK is installed in the default location, the script locates it automatically.
6. Result: `android/PlotWatchAndroid/app/build/outputs/apk/debug/app-debug.apk`.
7. Copy `app-debug.apk` to your supervisor's phone. After approving installation from the file manager, install it.

The debug APK is signed with the standard Android debug key, suitable for testing or direct personal installation. To publish on the Play Store or distribute widely, use Android Studio **Build > Generate Signed Bundle / APK**, keep your release signing key private and stable.

## Option B: Build without Android Studio (GitHub Actions)

1. Create a GitHub repository containing the entire project root (including `.github/workflows`). Do not commit `.env` or your real API keys.
2. Open repository **Actions > Build Android APK > Run workflow**.
3. Wait for the build to succeed; download the `PakistanPlotWatch-APK` artifact from the workflow run.
4. Unzip the artifact and transfer `app-debug.apk` to the Android phone.

This method uses GitHub-hosted Android SDK and Gradle. The APK is not created until that workflow actually runs successfully.

## First run

1. Install and open Pakistan PlotWatch.
2. Open **Settings**.
3. Paste `https://<your-backend-domain>` from Railway and the `PLOTWATCH_APP_TOKEN` server value.
4. Select **Save connection** and **Test server and login**.
5. Search: Plot number `301`, city `Rawalpindi`, society `Bahria Town Phase 8`, block `Block J`.
6. The app polls the cloud server for completion, then shows verified pages and separate unverified discoveries.

**Do not enter `http://localhost:8000` in the Android APK.** On a phone, localhost is the phone, not your Windows computer. HTTPS is mandatory.

## Troubleshooting

* `SDK location not found`: Set `ANDROID_HOME` or install SDK through Android Studio, then rerun the builder.
* `Failed to find Platform SDK 35`: Install API 35 using Android Studio SDK Manager.
* `Cannot download Gradle`: You need internet on the Windows machine for first build, or use the GitHub Actions method.
* `401`: The PlotWatch app access token must match the backend environment variable.
* `HTTP 400 from Serper`: Backend API discovery has failed. Check backend logs and Serper dashboard. The APK cannot fix upstream authentication or account errors.
* `No verified results`: May be robots restrictions, JavaScript-only webpages, inaccessible sources, or no matching indexed advertisement. This is not proof a plot is unsold.
* `503/offline`: Make sure Railway backend is deployed, awake, and configured with a persistent volume.
