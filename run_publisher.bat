@echo off
cd /d C:\Users\Diario\social_publisher
call venv\Scripts\activate.bat
python main.py --run-now >> logs\task_scheduler.log 2>&1
