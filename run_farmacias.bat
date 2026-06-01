@echo off
cd /d C:\Users\Diario\social_publisher
"venv\Scripts\python.exe" main.py --farmacias >> "logs\task_scheduler.log" 2>&1
