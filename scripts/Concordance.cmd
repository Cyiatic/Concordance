@echo off
setlocal
set "PROJECT_ROOT=%~dp0.."
python "%PROJECT_ROOT%\scripts\concordance_launcher.py" %*
endlocal
