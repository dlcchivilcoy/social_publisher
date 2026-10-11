@echo off
REM Pide la API key de Buffer (oculta), la prueba y la guarda en .env + secrets de GitHub.
setlocal
cd /d "%~dp0"
"venv\Scripts\python.exe" cargar_clave_buffer.py
pause
