@echo off
rem Doppelklick-Start fuer Windows: ruft einrichten.ps1 auf, auch wenn
rem PowerShell-Skripte sonst gesperrt sind. Optionen werden weitergereicht,
rem z. B.  einrichten.cmd -Test
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0einrichten.ps1" %*
echo.
pause
