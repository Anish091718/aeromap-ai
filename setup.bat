@echo off
echo === AeroMap AI setup ===
python -m venv venv
if errorlevel 1 (
    echo Python was not found. Install Python 3.10+ from python.org and tick "Add to PATH".
    pause
    exit /b 1
)
call venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt
echo.
echo Setup complete. Now run run.bat
pause
