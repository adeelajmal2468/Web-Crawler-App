@echo off
cd /d "%~dp0"
echo =======================================
echo    Pakistan PlotWatch APK Builder
echo =======================================
echo Requires Android Studio SDK API 35 and internet on first build.
call gradlew.bat assembleDebug
if errorlevel 1 (
 echo.
 echo BUILD FAILED. See the error above and BUILDING.md.
 pause
 exit /b 1
)
echo.
echo SUCCESS! APK should be here:
echo %CD%\app\build\outputs\apk\debug\app-debug.apk
pause
