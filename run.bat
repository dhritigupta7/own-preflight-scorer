@echo off
REM One-click launcher for the OWN Pre-Flight Scorer.
REM Opens the app in your browser at http://localhost:8501
cd /d "%~dp0"
python -m streamlit run app.py
pause
