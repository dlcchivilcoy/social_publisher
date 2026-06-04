@echo off
cd /d C:\Users\Diario\social_publisher
"venv\Scripts\python.exe" main.py --run-now --pages 8,9 >> "logs\task_scheduler.log" 2>&1
