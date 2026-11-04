@echo off
rem  pc run       -> start backend + frontend + agent
rem  pc install   -> add this folder to PATH so "pc run" works from anywhere
set "CMD=%~1"
if /i "%CMD%"=="run" goto :run
if /i "%CMD%"=="install" goto :install
echo Usage:
echo   pc run [--no-agent ^| --no-web ^| --no-server ^| --no-browser]
echo   pc install
exit /b 0

:run
shift
call "%~dp0start.bat" %1 %2 %3 %4 %5
exit /b %errorlevel%

:install
powershell -NoProfile -Command "$d='%~dp0'.TrimEnd('\'); $p=[Environment]::GetEnvironmentVariable('Path','User'); if(-not $p){$p=''}; if(($p -split ';') -notcontains $d){[Environment]::SetEnvironmentVariable('Path',($p.TrimEnd(';')+';'+$d).TrimStart(';'),'User'); Write-Host 'Added to PATH. Open a NEW PowerShell window, then run: pc run'} else {Write-Host 'Already in PATH. Run: pc run'}"
exit /b 0
