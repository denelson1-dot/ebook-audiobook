@echo off
REM Launch the web UI from a source checkout, without activating the virtualenv.
REM Installed copies just use the `ebook-audiobook` command instead.
cd /d "%~dp0"

set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" (
  echo No virtualenv found at .venv\. Set one up first:
  echo   python -m venv .venv ^&^& .venv\Scripts\pip install -e ".[dev]"
  echo (see CONTRIBUTING.md^)
  exit /b 1
)

"%PY%" -m ebook_audiobook.cli web %*
