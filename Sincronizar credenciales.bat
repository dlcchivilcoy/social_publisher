@echo off
REM ============================================================================
REM  Sube el .env al secret ENV_FILE de GitHub (el que usa la nube para publicar).
REM
REM  POR QUE ESTE .BAT Y NO POWERSHELL:
REM  PowerShell 5.1 lee los archivos SIN BOM con la pagina de codigos ANSI, asi
REM  que "Diario La Campana" (con la N con virgulilla) se rompia y llegaba a la
REM  nube como "Diario La CampaA+-a". Eso salia publicado tal cual en Facebook.
REM  El redireccionador "<" de cmd manda los BYTES tal cual, sin convertir nada.
REM
REM  Uso: doble clic. No pide nada.
REM ============================================================================
setlocal
cd /d "%~dp0"

if not exist ".env" (
  echo [ERROR] No encuentro el archivo .env en esta carpeta.
  pause
  exit /b 1
)

echo Subiendo .env al secret ENV_FILE...
gh secret set ENV_FILE < .env
if errorlevel 1 (
  echo.
  echo [ERROR] No se pudo subir. Fijate de tener sesion iniciada: gh auth login
  pause
  exit /b 1
)

echo.
echo Listo. La nube ya tiene las credenciales actualizadas.
pause
