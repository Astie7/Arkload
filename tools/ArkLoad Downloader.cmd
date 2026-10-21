@echo off
rem ArkLoad - Modern Download Manager.
rem This is the one and only application: it starts the PySide6 window when PySide6 is
rem installed and falls back to the classic Tkinter window when it is not.
setlocal
cd /d "%~dp0.."
python -c "import PySide6" >nul 2>&1
if not errorlevel 1 (
    start "" pythonw "tools\arkload\main.py" %*
    exit /b 0
)
where pythonw >nul 2>&1
if not errorlevel 1 (
    start "" pythonw "tools\mediafire_gui.py" %*
    exit /b 0
)
where pyw >nul 2>&1
if not errorlevel 1 (
    start "" pyw "tools\mediafire_gui.py" %*
    exit /b 0
)
python "tools\arkload\main.py" %*
if errorlevel 1 pause
