@echo off
cd /d C:\Users\Diario\social_publisher
"venv\Scripts\python.exe" main.py --yt-notes >> "logs\task_scheduler.log" 2>&1
