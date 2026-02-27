@echo off
cd /d "%~dp0"
echo Starting local server...
echo.
echo Open in your browser:  http://localhost:8080
echo.
echo Press Ctrl+C to stop the server.
echo.
start http://localhost:8080
python -m http.server 8080
pause
