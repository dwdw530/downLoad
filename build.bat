@echo off
setlocal

echo ====================================
echo PyDownloader - Build EXE
echo ====================================
echo.

set "CONDA_ENV=py310_env"

where conda >nul 2>&1
if errorlevel 1 (
    echo [ERROR] conda not found in PATH.
    echo Please install Anaconda/Miniconda and ensure "conda" is available.
    pause
    exit /b 1
)

echo [1/2] Checking PyInstaller in %CONDA_ENV% ...
call conda run -n %CONDA_ENV% python -m pip show pyinstaller >nul 2>&1
if errorlevel 1 (
    echo PyInstaller not found. Installing...
    call conda run -n %CONDA_ENV% python -m pip install pyinstaller
    if errorlevel 1 (
        echo [ERROR] Failed to install PyInstaller.
        pause
        exit /b 1
    )
)

echo [2/2] Building EXE ...
call conda run -n %CONDA_ENV% python "scripts/build_exe.py"
if errorlevel 1 (
    echo.
    echo [ERROR] Build failed.
    pause
    exit /b 1
)

echo.
echo Done.
echo Output: dist\*.exe
echo.
pause

