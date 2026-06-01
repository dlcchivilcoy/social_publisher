@echo off
cd /d C:\Users\Diario\social_publisher
"venv\Scripts\python.exe" main.py --tapa >> "logs\task_scheduler.log" 2>&1
