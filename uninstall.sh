#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
if [[ ! -f .env ]]; then
    printf '%s\n' "Установка в этом каталоге не найдена."
    exit 0
fi
compose=(docker compose)
if ! docker info >/dev/null 2>&1 && (( EUID != 0 )); then compose=(sudo docker compose); fi
"${compose[@]}" down --remove-orphans
printf '%s\n' "Контейнеры остановлены и удалены. База знаний, настройки, сертификаты и модели сохранены."
