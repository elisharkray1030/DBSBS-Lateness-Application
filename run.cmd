@echo off
rem Double-clickable launcher: runs run.ps1 with the execution policy bypassed.
rem Accepts the bash-style --flag spellings and maps them to PowerShell params.
setlocal
set "ARGS=%*"
if defined ARGS set "ARGS=%ARGS:--no-seed=-NoSeed%"
if defined ARGS set "ARGS=%ARGS:--local=-Local%"
if defined ARGS set "ARGS=%ARGS:--yes=-Yes%"
if defined ARGS set "ARGS=%ARGS:--backup=-Backup%"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run.ps1" %ARGS%
endlocal
