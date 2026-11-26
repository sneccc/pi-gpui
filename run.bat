@echo off
rem Launch Pi GPUI. Usage: run.bat [project-folder]
rem With no argument the app opens normally; pick the project and session in the UI.
rem Builds first if the exe is missing. pi is found on PATH (or set PI_EXECUTABLE).
setlocal
set "PATH=%PATH%;%USERPROFILE%\.cargo\bin"
set "EXE=%~dp0target\release\pi-gpui.exe"
if not exist "%EXE%" (
    pushd "%~dp0"
    cargo build --release || (popd & exit /b 1)
    popd
)
if "%~1"=="" (
    start "" "%EXE%"
) else (
    start "" "%EXE%" "%~f1"
)
