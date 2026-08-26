@echo off
title Autorizar TikTok - Diario La Campana
cd /d C:\Users\Diario\social_publisher

echo ============================================================
echo   AUTORIZAR TIKTOK  -  Diario La Campana
echo ============================================================
echo.
echo  Se va a abrir el navegador para que le des permiso a la app.
echo.
echo  IMPORTANTE:
echo    1) Inicia sesion con la cuenta de TikTok DEL DIARIO.
echo    2) Acepta TODOS los permisos (incluido publicar videos).
echo.
echo  Cuando termines en el navegador, volve a esta ventana.
echo ------------------------------------------------------------
echo.

"venv\Scripts\python.exe" tiktok_auth.py

echo.
echo ============================================================
echo  Si arriba dice que guardo el token, ya esta listo.
echo  Si ves un error, sacale una foto/captura y pasamelo.
echo ============================================================
echo.
pause
