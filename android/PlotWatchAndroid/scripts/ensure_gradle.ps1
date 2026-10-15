param([Parameter(ValueFromRemainingArguments = $true)][string[]]$GradleArgs)
$ErrorActionPreference = 'Stop'
$project = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$version = '8.9'
$downloadRoot = Join-Path $project '.local-gradle'
$gradleExe = Join-Path $downloadRoot "gradle-$version\bin\gradle.bat"
if (-not (Test-Path $gradleExe)) {
  Write-Host "Fetching official Gradle $version (one-time download) ..."
  New-Item -ItemType Directory -Force -Path $downloadRoot | Out-Null
  $zipPath = Join-Path $downloadRoot "gradle-$version-bin.zip"
  if (-not (Test-Path $zipPath)) {
    Invoke-WebRequest -Uri "https://services.gradle.org/distributions/gradle-$version-bin.zip" -OutFile $zipPath
  }
  Expand-Archive -Path $zipPath -DestinationPath $downloadRoot -Force
}
if (-not $env:JAVA_HOME) {
  $jbr = Join-Path $env:ProgramFiles 'Android\Android Studio\jbr'
  if (Test-Path (Join-Path $jbr 'bin\java.exe')) { $env:JAVA_HOME = $jbr }
}
if (-not $env:ANDROID_HOME) {
  $sdk = Join-Path $env:LOCALAPPDATA 'Android\Sdk'
  if (Test-Path $sdk) { $env:ANDROID_HOME = $sdk }
}
if ($env:ANDROID_HOME) {
  $sdkText = $env:ANDROID_HOME.Replace('\','/')
  Set-Content -Path (Join-Path $project 'local.properties') -Value "sdk.dir=$sdkText"
}
if (-not $GradleArgs -or $GradleArgs.Count -eq 0) { $GradleArgs = @('assembleDebug') }
Push-Location $project
try {
  & $gradleExe @GradleArgs
  exit $LASTEXITCODE
} finally { Pop-Location }
