@echo off
rem Abre el Editor de notas de la web (Diario La Campana) con doble clic.
cd /d "%~dp0"
start "" "%~dp0venv\Scripts\pythonw.exe" "%~dp0editor_notas_gui.py"
