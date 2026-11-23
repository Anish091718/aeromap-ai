@echo off
echo === AeroMap AI setup ===
python -m venv venv
call venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt
echo.
echo Setup complete. Ab run.bat chalao.
pause
