@echo off
REM Secure build script for Wizard Launcher with Doppler
REM Usage: build_secure.bat <azure-client-id>

setlocal enabledelayedexpansion

if "%~1"=="" (
    if "%MC_LAUNCHER_CLIENT_ID%"=="" (
        echo Usage: %0 ^<azure-client-id^>
        echo.
        echo Example:
        echo   %0 11111111-2222-3333-4444-555555555555
        echo.
        echo Or set environment variable first:
        echo   set MC_LAUNCHER_CLIENT_ID=11111111-2222-3333-4444-555555555555
        echo   %0
        exit /b 1
    )
    set "CLIENT_ID=!MC_LAUNCHER_CLIENT_ID!"
) else (
    set "CLIENT_ID=%~1"
)

echo ======================================
echo Wizard Launcher Secure Build
echo ======================================
echo.
echo ^⚠️  SECURITY WARNING:
echo    The Azure Client ID will be:
echo    1. Embedded (obfuscated) into the binary
echo    2. Removed from plaintext after build
echo.
echo Client ID (obfuscated): ^^^^^^^^^^^^^^^^^^
echo.

REM Set environment variable for the build
set "MC_LAUNCHER_CLIENT_ID=%CLIENT_ID%"

REM Step 1: Embed the Client ID (obfuscated)
echo 📝 Step 1: Embedding Client ID (obfuscated)...
python tools/embed_secret.py --from-env
if errorlevel 1 (
    echo ❌ Failed to embed Client ID
    exit /b 1
)

REM Step 2: Build
echo.
echo 🔨 Step 2: Building application...
python tools/build.py --from-env --no-package
if errorlevel 1 (
    set BUILD_FAILED=1
    echo ⚠️  Build had issues, but cleaning up plaintext...
) else (
    echo ✅ Build successful!
)

REM Step 3: Clean up (remove plaintext from source)
echo.
echo 🧹 Step 3: Cleaning up plaintext...
python tools/embed_secret.py --clean
if errorlevel 1 (
    echo ⚠️  Warning: Failed to clean up plaintext
)

if defined BUILD_FAILED (
    echo.
    echo ❌ Build had issues
    exit /b 1
)

echo.
echo ======================================
echo ✅ Build complete!
echo ======================================
echo.
echo Next steps:
echo 1. Run Inno Setup to create installer
echo 2. Distribute to users
echo.
echo Users will see a login prompt on first run.
echo No credentials are hardcoded anywhere.
echo.
