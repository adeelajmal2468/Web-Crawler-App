@echo off
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\ensure_gradle.ps1" %*
exit /b %ERRORLEVEL%
