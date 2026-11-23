#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
umask 077

fail() { printf '%s\n' "$*" >&2; exit 1; }

. /etc/os-release
[[ "$ID" == debian && "$VERSION_ID" == 13 ]] || fail "Этот установщик рассчитан на Debian 13."
command -v nvidia-smi >/dev/null && nvidia-smi -L || fail "Установите драйвер NVIDIA для Tesla V100 (см. README.md)."
command -v nvidia-ctk >/dev/null || fail "Установите и настройте NVIDIA Container Toolkit (см. README.md)."

sudo_cmd=()
if (( EUID != 0 )); then sudo_cmd=(sudo); fi
if ! command -v docker >/dev/null || ! docker compose version >/dev/null 2>&1; then
    "${sudo_cmd[@]}" apt-get update
    "${sudo_cmd[@]}" apt-get install -y docker.io docker-compose
fi
"${sudo_cmd[@]}" apt-get install -y ca-certificates curl openssl python3
docker_cmd=(docker)
if ! docker info >/dev/null 2>&1; then
    docker_cmd=("${sudo_cmd[@]}" docker)
fi
if ! "${docker_cmd[@]}" info >/dev/null 2>&1; then
    "${sudo_cmd[@]}" systemctl enable --now docker
fi
compose=("${docker_cmd[@]}" compose)
"${compose[@]}" version

# Read only the named key; .env is Compose data, never a shell script.
config() { sed -n "s/^${1}=//p" .env | tail -n 1; }
if [[ ! -f .env ]]; then
    detected="$(hostname -I | awk '{print $1}')"
    server="${1:-}"
    if [[ -z "$server" ]]; then
        read -r -p "IPv4 или домен сервера [${detected}]: " server
    fi
    server="${server:-$detected}"
    rtc_ip="${RTC_NODE_IP:-$detected}"
    [[ -n "$server" && -n "$rtc_ip" ]] || fail "Укажите адрес сайта и RTC_NODE_IP."
    cat > .env <<CFG
SERVER_NAME=${server,,}
RTC_NODE_IP=${rtc_ip}
LIVEKIT_API_KEY=$(openssl rand -hex 16)
LIVEKIT_API_SECRET=$(openssl rand -hex 32)
CLIENT_API_KEY=$(openssl rand -hex 24)
INFERENCE_TOKEN=$(openssl rand -hex 32)
RAG_ADMIN_USER=rag-admin
RAG_ADMIN_PASSWORD=$(openssl rand -hex 18)
PUBLIC_AUTH=on
WEB_USER=visitor
WEB_PASSWORD=$(openssl rand -hex 12)
MAX_SESSIONS=3
LLM_FILE=Qwen3.6-27B-IQ4_XS.gguf
LLM_CONTEXT=4096
CFG
fi

server="$(config SERVER_NAME)"
[[ -n "$server" ]] || fail "Отсутствует SERVER_NAME. Для обновления распакуйте архив в новый каталог."
rtc_ip="$(config RTC_NODE_IP)"
python3 - "$server" "$rtc_ip" <<'PY'
import ipaddress
import re
import sys
name, ip = sys.argv[1:]
try:
    ipaddress.IPv4Address(ip)
except ValueError:
    raise SystemExit("RTC_NODE_IP должен быть IPv4-адресом.")
label = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
if not re.fullmatch(rf"{label}(?:\.{label})*", name):
    raise SystemExit("SERVER_NAME должен быть IPv4-адресом или DNS-именем.")
if re.fullmatch(r"[0-9.]+", name):
    try:
        ipaddress.IPv4Address(name)
    except ValueError:
        raise SystemExit("Укажите корректный IPv4-адрес.")
PY
chmod 600 .env
mkdir -p nginx/conf.d nginx/ssl certbot/{www,etc}
chmod 755 nginx/conf.d nginx/ssl certbot/www certbot/etc
[[ -n "$(config WEB_USER)" && -n "$(config WEB_PASSWORD)" ]] || fail "Заполните WEB_USER и WEB_PASSWORD в .env."
if [[ "$(config PUBLIC_AUTH)" == off ]]; then
    public_auth='auth_basic off;'
else
    public_auth='auth_basic "IU5 Voice"; auth_basic_user_file /etc/nginx/.htpasswd;'
fi
web_hash="$(printf '%s\n' "$(config WEB_PASSWORD)" | openssl passwd -apr1 -stdin)"
printf '%s:%s\n' "$(config WEB_USER)" "$web_hash" > nginx/.htpasswd
chmod 644 nginx/.htpasswd

cert=/etc/nginx/ssl/voice.crt
key=/etc/nginx/ssl/voice.key
if [[ -n "${TLS_CERT_FILE:-}" || -n "${TLS_KEY_FILE:-}" ]]; then
    [[ -r "${TLS_CERT_FILE:-}" && -r "${TLS_KEY_FILE:-}" ]] || fail "Укажите читаемые TLS_CERT_FILE и TLS_KEY_FILE."
    install -m 644 "$TLS_CERT_FILE" nginx/ssl/voice.crt
    install -m 600 "$TLS_KEY_FILE" nginx/ssl/voice.key
fi
if [[ ! -s nginx/ssl/voice.crt || ! -s nginx/ssl/voice.key ]]; then
    san="DNS:$server"
    if [[ "$server" =~ ^[0-9.]+$ ]]; then san="IP:$server"; fi
    openssl req -x509 -newkey rsa:2048 -nodes -days 365 -keyout nginx/ssl/voice.key \
      -out nginx/ssl/voice.crt -subj "/CN=$server" -addext "subjectAltName=$san"
fi

docker_host_ip="$("${docker_cmd[@]}" network inspect bridge --format '{{(index .IPAM.Config 0).Gateway}}')"
[[ "$docker_host_ip" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] || fail "Не удалось определить IPv4 шлюза Docker."
sed -e "s|__RTC_NODE_IP__|$rtc_ip|g" -e "s|__DOCKER_HOST_IP__|$docker_host_ip|g" livekit.yaml.template > livekit.yaml
chmod 644 livekit.yaml
render_nginx() {
    sed -e "s|__SERVER_NAME__|$server|g" -e "s|__CERT_PATH__|$cert|g" -e "s|__KEY_PATH__|$key|g" \
        -e "s|__PUBLIC_AUTH__|$public_auth|g" nginx/voice-app.conf.template > nginx/conf.d/voice-app.conf.tmp
    chmod 644 nginx/conf.d/voice-app.conf.tmp
    mv nginx/conf.d/voice-app.conf.tmp nginx/conf.d/voice-app.conf
}
use_letsencrypt() {
    cert="/etc/letsencrypt/live/$server/fullchain.pem"
    key="/etc/letsencrypt/live/$server/privkey.pem"
}
if "${sudo_cmd[@]}" test -s "certbot/etc/live/$server/fullchain.pem"; then use_letsencrypt; fi
render_nginx

python3 web/prepare_assets.py --check
"${compose[@]}" config -q
bash tools/download-models.sh "$(config LLM_FILE)"

# Build and start unrelated services together. The worker waits for GPU and CPU models to be ready.
"${compose[@]}" up -d --build --wait --wait-timeout 320
"${compose[@]}" exec -T nginx nginx -t
"${compose[@]}" exec -T nginx nginx -s reload
if [[ -n "${LE_EMAIL:-}" ]]; then
    "${compose[@]}" run --rm certbot certonly --webroot -w /var/www/certbot --cert-name "$server" \
        -d "$server" --non-interactive --agree-tos -m "$LE_EMAIL" --keep-until-expiring
    use_letsencrypt
    render_nginx
    "${compose[@]}" exec -T nginx nginx -t
    "${compose[@]}" exec -T nginx nginx -s reload
fi

printf '\nГолосовой помощник https://%s/\nАдминистрирование https://%s/admin/\n' "$server" "$server"
printf 'Логин посетителя %s, логин администратора %s. Пароли указаны в .env.\n' "$(config WEB_USER)" "$(config RAG_ADMIN_USER)"
"${compose[@]}" ps
