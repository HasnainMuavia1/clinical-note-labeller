@echo off
setlocal EnableDelayedExpansion
title Clinical Note Labeller - Setup
color 0F

REM ============================================================================
REM  Clinical Note Labeller - one-file installer and launcher.
REM
REM  Run it once: it installs Docker Desktop if missing, downloads the app,
REM  starts it, and opens the browser. Run it again any time to relaunch.
REM  Nothing else needs to be installed or configured.
REM ============================================================================

REM ---- settings (the build script fills these in) ----------------------------
set "COMPOSE_URL=https://raw.githubusercontent.com/HasnainMuavia1/clinical-note-labeller/main/docker-compose.prod.yml"
set "IMAGE_TAG=@@IMAGE_TAG@@"
set "OPENAI_API_KEY=@@OPENAI_API_KEY@@"
set "OPENAI_MINI_MODEL_ID=@@OPENAI_MINI_MODEL_ID@@"
set "LLAMA_CLOUD_API_KEY=@@LLAMA_CLOUD_API_KEY@@"
set "APP_API_KEY=@@APP_API_KEY@@"

set "INSTALL_DIR=%LOCALAPPDATA%\ClinicalNoteLabeller"
REM Results live next to the .bat / .exe the client double-clicked, not in a hidden profile folder.
if defined CNL_DATA_DIR (
    set "DATA_DIR=%CNL_DATA_DIR%"
) else if defined CNL_LAUNCHER (
    for %%I in ("%CNL_LAUNCHER%") do set "DATA_DIR=%%~dpIClinicalNoteLabeller"
) else (
    set "DATA_DIR=%~dp0ClinicalNoteLabeller"
)
set "DOCKER_EXE=%ProgramFiles%\Docker\Docker\Docker Desktop.exe"
set "DOCKER_URL=https://desktop.docker.com/win/main/amd64/Docker%%20Desktop%%20Installer.exe"
if defined CNL_LAUNCHER (
    for %%I in ("%CNL_LAUNCHER%") do set "BUNDLED_DOCKER=%%~dpIdocker\DockerDesktopInstaller.exe"
) else (
    set "BUNDLED_DOCKER=%~dp0docker\DockerDesktopInstaller.exe"
)

echo.
echo   ==========================================================
echo      CLINICAL NOTE LABELLER
echo      Automatic setup - this window will tell you when to
echo      do something. Otherwise just leave it running.
echo   ==========================================================
echo.

REM ---- 1. Docker present? ----------------------------------------------------
echo [1/6] Checking for Docker...
where docker >nul 2>&1
if %ERRORLEVEL% EQU 0 goto :docker_present

if exist "%BUNDLED_DOCKER%" (
    echo       Using the bundled Docker installer.
    copy /y "%BUNDLED_DOCKER%" "%TEMP%\DockerDesktopInstaller.exe" >nul
) else (
    echo       Docker is not installed. Downloading it now (about 600 MB).
    echo       This is a one-time step and can take several minutes.
    echo.
    curl.exe -fL --retry 3 --retry-delay 2 --progress-bar -o "%TEMP%\DockerDesktopInstaller.exe" "%DOCKER_URL%"
)
if not exist "%TEMP%\DockerDesktopInstaller.exe" (
    echo.
    echo   [X] Could not get Docker Desktop. Put DockerDesktopInstaller.exe in
    echo       a "docker" folder next to this file, or check the internet and
    echo       run this file again.
    goto :fail
)

echo.
echo       Installing Docker Desktop. Approve the Windows prompt if it appears.
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "Start-Process -FilePath '%TEMP%\DockerDesktopInstaller.exe' -ArgumentList 'install','--quiet','--accept-license' -Verb RunAs -Wait"

REM PATH is not refreshed inside a running process; add Docker's bin directly.
set "PATH=%PATH%;%ProgramFiles%\Docker\Docker\resources\bin"
where docker >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo   ==========================================================
    echo      RESTART REQUIRED
    echo      Docker Desktop has been installed but Windows must be
    echo      restarted before it can run.
    echo.
    echo      1. Restart this computer.
    echo      2. Run this same file again.
    echo         Everything after this is automatic.
    echo   ==========================================================
    echo.
    pause
    exit /b 0
)

:docker_present
echo       Docker is installed.

REM ---- 2. Docker engine running? ---------------------------------------------
echo [2/6] Starting the Docker engine...
docker info >nul 2>&1
if %ERRORLEVEL% EQU 0 goto :engine_ready

if exist "%DOCKER_EXE%" start "" "%DOCKER_EXE%"
echo       Waiting for Docker to start (this can take a minute or two)...

for /l %%i in (1,1,90) do (
    docker info >nul 2>&1
    if !ERRORLEVEL! EQU 0 goto :engine_ready
    <nul set /p "=."
    timeout /t 4 /nobreak >nul
)

echo.
echo   [X] Docker did not finish starting.
echo       If this machine has just installed Docker for the first time,
echo       restart Windows and run this file again.
goto :fail

:engine_ready
echo.
echo       Docker engine is ready.

REM ---- 3. Folders and configuration ------------------------------------------
echo [3/6] Preparing folders...
if not exist "%INSTALL_DIR%" mkdir "%INSTALL_DIR%" >nul 2>&1
if not exist "%DATA_DIR%\workspace" mkdir "%DATA_DIR%\workspace" >nul 2>&1

set "WORKSPACE_DIR=%DATA_DIR%\workspace"
set "WORKSPACE_DIR=!WORKSPACE_DIR:\=/!"

echo       Labelled output will appear in: %DATA_DIR%\workspace

REM ---- 4. Download the compose file and write the configuration --------------
echo [4/6] Downloading the application definition...
curl.exe -fsSL -o "%INSTALL_DIR%\docker-compose.yml" "%COMPOSE_URL%"
if not exist "%INSTALL_DIR%\docker-compose.yml" (
    echo   [X] Could not download the application definition.
    echo       Check the internet connection and run this file again.
    goto :fail
)

call :pick_port 8000 API_PORT
call :pick_port 5173 UI_PORT

> "%INSTALL_DIR%\.env" (
    echo IMAGE_TAG=%IMAGE_TAG%
    echo OPENAI_API_KEY=%OPENAI_API_KEY%
    echo OPENAI_MINI_MODEL_ID=%OPENAI_MINI_MODEL_ID%
    echo LLAMA_CLOUD_API_KEY=%LLAMA_CLOUD_API_KEY%
    echo APP_API_KEY=%APP_API_KEY%
    echo API_PORT=!API_PORT!
    echo UI_PORT=!UI_PORT!
    echo WORKSPACE_DIR=!WORKSPACE_DIR!
)
echo       Configured. Web address will be http://localhost:!UI_PORT!

REM ---- 5. Pull and start -----------------------------------------------------
echo [5/6] Downloading and starting the application...
echo       The first run downloads about 320 MB. Later runs are almost instant.
echo.
pushd "%INSTALL_DIR%"
docker compose pull
if %ERRORLEVEL% NEQ 0 (
    popd
    echo.
    echo   [X] Could not download the application images.
    echo       If this says "denied" or "unauthorized", the published packages
    echo       are still private - see PUBLISHING.md in the repository.
    goto :fail
)
docker compose up -d
if %ERRORLEVEL% NEQ 0 (
    popd
    echo   [X] The application failed to start.
    echo       Run this for details:  docker compose -f "%INSTALL_DIR%\docker-compose.yml" logs
    goto :fail
)
popd

REM ---- 6. Wait for health and open the browser -------------------------------
echo.
echo [6/6] Waiting for the application to be ready...
for /l %%i in (1,1,60) do (
    curl.exe -fsS "http://localhost:!API_PORT!/api/v1/health" >nul 2>&1
    if !ERRORLEVEL! EQU 0 goto :app_ready
    <nul set /p "=."
    timeout /t 3 /nobreak >nul
)
echo.
echo   [X] The application did not become ready in time.
echo       Run this for details:  docker compose -f "%INSTALL_DIR%\docker-compose.yml" logs
goto :fail

:app_ready
echo.
call :make_shortcut

echo.
echo   ==========================================================
echo      READY
echo.
echo      Opening http://localhost:!UI_PORT! in your browser.
echo.
echo      Drag clinical notes onto the page - PDF, DOCX, text
echo      or ZIP - and they are sorted into folders by whether
echo      they contain medical codes and by specialty.
echo.
echo      Results also appear on disk in:
echo        %DATA_DIR%\workspace
echo.
echo      To open it again later, use the "Clinical Note Labeller"
echo      shortcut on your Desktop.
echo   ==========================================================
echo.
start "" "http://localhost:!UI_PORT!/"
echo   You can close this window.
pause
exit /b 0

REM ---- helpers ---------------------------------------------------------------

:pick_port
REM %1 = preferred port, %2 = variable to set. Steps up until a free port is found.
set "_p=%~1"
:pick_port_loop
netstat -an | findstr /r /c:":%_p% .*LISTENING" >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    set /a _p=%_p%+1
    goto :pick_port_loop
)
set "%~2=%_p%"
goto :eof

:make_shortcut
REM Prefer the .exe launcher when the client started from one.
if defined CNL_LAUNCHER (set "SHORTCUT_TARGET=%CNL_LAUNCHER%") else (set "SHORTCUT_TARGET=%~f0")
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Desktop')+'\Clinical Note Labeller.lnk');" ^
  "$s.TargetPath='%SHORTCUT_TARGET%'; $s.WorkingDirectory='%INSTALL_DIR%';" ^
  "$s.Description='Clinical Note Labeller'; $s.Save()" >nul 2>&1
goto :eof

:fail
echo.
echo   Setup did not complete. Nothing has been damaged - you can run this
echo   file again at any time.
echo.
pause
exit /b 1
