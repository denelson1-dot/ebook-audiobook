@echo off
REM One command to launch the ebook-audiobook web UI on Windows.
REM Uses the project's virtualenv directly, so you don't have to activate it.
cd /d "%~dp0"

set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" (
  echo No virtualenv found at .venv\. Set it up first:
  echo   python -m venv .venv ^&^& .venv\Scripts\pip install -e ".[tts]"
  echo (see README.md^)
  exit /b 1
)

"%PY%" -m app.web %*
