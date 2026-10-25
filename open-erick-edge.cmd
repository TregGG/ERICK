@echo off
setlocal
start "ERICK local app" "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe" --no-proxy-server --user-data-dir="%TEMP%\erick-local-edge" "http://127.0.0.1:8091/passenger/"
