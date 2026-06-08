#!/bin/bash
# Wrapper del publicador en el server Linux.
# 1) Baja la carpeta del dia desde Google Drive (rclone).
# 2) Corre main.py con los argumentos que reciba (los pasa tal cual el cron).
# Uso: ./correr.sh --run-now --pages 3,5,7   |   ./correr.sh --dry-run
set -euo pipefail

cd /home/ubuntu/social_publisher

# Trae notas/fotos/tapa/PDF desde Drive a la carpeta local que lee el .env.
# 'sync' deja la carpeta local igual al remoto (borra lo que ya no este en Drive).
rclone sync gdrive:Diario /home/ubuntu/diario

# Publica. La salida queda en logs/cron.log con fecha de cada corrida.
echo "===== $(date '+%Y-%m-%d %H:%M:%S %Z') :: main.py $* =====" >> logs/cron.log
./venv/bin/python main.py "$@" >> logs/cron.log 2>&1
