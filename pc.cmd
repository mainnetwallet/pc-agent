@echo off
rem  pc            -> start backend + frontend + agent
rem  pc --no-agent -> same, with options (--no-agent --no-web --no-server --no-browser)
rem  pc install    -> add this folder to PATH so "pc" works from anywhere
rem  pc run        -> same as "pc" (kept for convenience)
set "CMD=%~1"
if "%CMD%"=="" goto :start
if /i "%CMD%"=="run" goto :run
if /i "%CMD%"=="install" goto :install
if /i "%CMD%"=="help" goto :usage
if "%CMD:~0,2%"=="--" goto :start
goto :usage

:start
call "%~dp0start.bat" %*
exit /b %errorlevel%

:run
shift
call "%~dp0start.bat" %1 %2 %3 %4 %5
exit /b %errorlevel%

:usage
echo Usage:
echo   pc                start everything
echo   pc [--no-agent] [--no-web] [--no-server] [--no-browser]
echo   pc install        add to PATH (once), then use "pc" from any folder
exit /b 0

:install
powershell -NoProfile -Command "$d='%~dp0'.TrimEnd('\'); $p=[Environment]::GetEnvironmentVariable('Path','User'); if(-not $p){$p=''}; if(($p -split ';') -notcontains $d){[Environment]::SetEnvironmentVariable('Path',($p.TrimEnd(';')+';'+$d).TrimStart(';'),'User'); Write-Host 'Added to PATH. Open a NEW PowerShell window, then just type: pc'} else {Write-Host 'Already in PATH. Just type: pc'}"
exit /b 0
