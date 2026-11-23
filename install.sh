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
packages=()
if ! command -v docker >/dev/null; then
    packages+=(docker.io docker-compose)
elif ! docker compose version >/dev/null 2>&1 &&
    ! "${sudo_cmd[@]}" docker compose version >/dev/null 2>&1; then
    packages+=(docker-compose)
fi
[[ -s /etc/ssl/certs/ca-certificates.crt ]] || packages+=(ca-certificates)
openssl version >/dev/null 2>&1 || packages+=(openssl)
python3 --version >/dev/null 2>&1 || packages+=(python3)
if ! curl --version >/dev/null 2>&1 && ! wget --version >/dev/null 2>&1; then
    packages+=(curl)
fi

if ((${#packages[@]} > 0)); then
    printf 'Установка недостающих пакетов: %s\n' "${packages[*]}"
    "${sudo_cmd[@]}" apt-get update ||
        fail "Не удалось обновить индексы APT. См. раздел диагностики APT в README.md."
    "${sudo_cmd[@]}" apt-get install -y --no-remove "${packages[@]}" ||
        fail "APT не смог установить недостающие пакеты. См. раздел диагностики APT в README.md."
fi
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
config() {
    if [[ -f .env ]]; then sed -n "s/^${1}=//p" .env | tail -n 1; fi
}
previous_name="$(config SERVER_NAME)"
server="${1:-$previous_name}"
if [[ -z "$server" || ( $# == 0 && "$server" =~ ^[0-9.]+$ ) ]]; then
    read -r -p "DNS-имя сервера: " server || fail "Укажите DNS-имя: bash install.sh pyaterka.example.org"
fi
server="${server%.}"
server="${server,,}"
python3 - "$server" <<'PY'
import re
import sys

name = sys.argv[1]
label = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
if len(name) > 253 or not re.fullmatch(rf"{label}(?:\.{label})*", name) or re.fullmatch(r"[0-9.]+", name):
    raise SystemExit("Укажите DNS-имя сервера, например pyaterka.example.org.")
PY
getent -s dns ahostsv4 "$server" >/dev/null ||
    fail "DNS-имя $server не разрешается в IPv4 на сервере. Проверьте DNS-запись и повторите установку."
export SERVER_NAME="$server"

if [[ ! -f .env ]]; then
    cat > .env <<CFG
SERVER_NAME=${server}
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
elif [[ "$server" != "$previous_name" ]] || grep -q '^RTC_NODE_IP=' .env; then
    env_tmp="$(mktemp .env.XXXXXX)"
    awk '!/^(SERVER_NAME|RTC_NODE_IP)=/' .env > "$env_tmp"
    printf 'SERVER_NAME=%s\n' "$server" >> "$env_tmp"
    mv "$env_tmp" .env
fi
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

tls_dir=nginx/ssl
if [[ -n "${TLS_CERT_FILE:-}" || -n "${TLS_KEY_FILE:-}" ]]; then
    [[ -r "${TLS_CERT_FILE:-}" && -r "${TLS_KEY_FILE:-}" ]] || fail "Укажите читаемые TLS_CERT_FILE и TLS_KEY_FILE."
    openssl x509 -in "$TLS_CERT_FILE" -noout -checkhost "$server" ||
        fail "Сертификат из TLS_CERT_FILE должен подходить для $server."
    install -m 644 "$TLS_CERT_FILE" "$tls_dir/voice.crt"
    install -m 600 "$TLS_KEY_FILE" "$tls_dir/voice.key"
elif [[ ! -s "$tls_dir/voice.key" ]] ||
    ! openssl x509 -in "$tls_dir/voice.crt" -noout -checkhost "$server" >/dev/null 2>&1; then
    tls_dir="nginx/ssl/self-signed/$server"
    mkdir -p "$tls_dir"
    if [[ ! -s "$tls_dir/voice.crt" || ! -s "$tls_dir/voice.key" ]]; then
        openssl req -x509 -newkey rsa:2048 -nodes -days 365 -keyout "$tls_dir/voice.key" \
          -out "$tls_dir/voice.crt" -subj "/CN=$server" -addext "subjectAltName=DNS:$server"
    fi
fi
cert="/etc/$tls_dir/voice.crt"
key="/etc/$tls_dir/voice.key"

docker_host_ip="$("${docker_cmd[@]}" network inspect bridge --format '{{(index .IPAM.Config 0).Gateway}}')"
[[ "$docker_host_ip" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] || fail "Не удалось определить IPv4 шлюза Docker."
sed -e "s|__DOCKER_HOST_IP__|$docker_host_ip|g" livekit.yaml.template > livekit.yaml
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
