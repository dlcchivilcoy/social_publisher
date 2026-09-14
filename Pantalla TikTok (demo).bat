@echo off
title Pantalla de confirmacion TikTok - para grabar el demo
cd /d C:\Users\Diario\social_publisher

echo ============================================================
echo   PANTALLA DE CONFIRMACION DE TIKTOK  -  para grabar
echo ============================================================
echo.
echo  Abre la pantalla en diarioweb.vercel.app, que es el dominio
echo  que TikTok tiene declarado. El boton del mail apunta al otro
echo  dominio y por eso no sirve para el video.
echo.
echo  El nombre de la carpeta esta al pie del mail de aprobacion,
echo  entre comillas angulares. Ejemplo:
echo    corresponsal_2026-09-10_matias-zucchi_mtvlcxu2aks3
echo.
echo  OJO: al tocar "Publicar" se publica de verdad.
echo ------------------------------------------------------------
echo.

"venv\Scripts\python.exe" demo_pantalla_tiktok.py

echo.
pause
