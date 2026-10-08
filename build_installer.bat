@echo off
setlocal
cd /d "%~dp0"

where conda >nul 2>&1
if errorlevel 1 (
    echo [ERROR] conda was not found. Use the py310_env build environment.
    pause
    exit /b 1
)

echo Building the EXEs and Windows installer...
call conda run --no-capture-output -n py310_env python -B scripts/build_installer.py --rebuild %*
if errorlevel 1 (
    echo [ERROR] Installer build failed. See the error above.
    pause
    exit /b 1
)

echo Installer and SHA256 file are in dist.
pause
