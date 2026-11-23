import asyncio
import base64
import binascii
import hmac
import datetime
import json
import logging
import os
import re
import time
from collections import defaultdict, deque
from pathlib import Path
from uuid import uuid4

import httpx
from aiohttp import web
from livekit import api

STATIC_DIR = Path(__file__).resolve().parent / "static"
LIVEKIT_URL = os.getenv("LIVEKIT_URL", "ws://localhost:7880")
# Публичный WSS-адрес для клиентов (отдаётся в JSON /token, /v1/token)
LIVEKIT_PUBLIC_URL = os.getenv("LIVEKIT_PUBLIC_URL", LIVEKIT_URL)
API_KEY = os.getenv("LIVEKIT_API_KEY", "")
API_SECRET = os.getenv("LIVEKIT_API_SECRET", "")

# Авторизация: Bearer API-key для /v1/* (кроме /v1/health).
# Путь /token (веб-клиент) закрывается Basic Auth на уровне nginx.
CLIENT_API_KEY = os.getenv("CLIENT_API_KEY", "")

# Лимиты
MAX_SESSIONS = int(os.getenv("MAX_SESSIONS", "3"))
TOKEN_TTL_RAW = os.getenv("TOKEN_TTL", "10m")
TOKEN_RATE_LIMIT = int(os.getenv("TOKEN_RATE_LIMIT", "10"))  # запросов/мин с одного IP


def _parse_ttl(raw: str) -> datetime.timedelta:
    """Парсит '10m', '2h', '3600', '1d' в timedelta. Без суффикса — секунды."""
    raw = raw.strip().lower()
    m = re.match(r"^(\d+)\s*([smhd]?)$", raw)
    if not m:
        return datetime.timedelta(minutes=10)
    val = int(m.group(1))
    unit = m.group(2) or "s"
    return {
        "s": datetime.timedelta(seconds=val),
        "m": datetime.timedelta(minutes=val),
        "h": datetime.timedelta(hours=val),
        "d": datetime.timedelta(days=val),
    }[unit]


TOKEN_TTL = _parse_ttl(TOKEN_TTL_RAW)

# Сервисы для /v1/health
INFERENCE_URL = os.getenv("INFERENCE_URL", "http://inference:8650")
INFERENCE_TOKEN = os.getenv("INFERENCE_TOKEN", "")
LLM_HEALTH_URL = os.getenv("LLM_HEALTH_URL", "http://llm:8080/health")
ADMIN_USER = os.getenv("RAG_ADMIN_USER", "rag-admin")
ADMIN_PASSWORD = os.getenv("RAG_ADMIN_PASSWORD", "")
PUBLIC_ORIGIN = os.getenv("PUBLIC_ORIGIN", "")
HTTP_CLIENT = web.AppKey("http_client", httpx.AsyncClient)
NO_STORE = {"Cache-Control": "no-store"}
# LiveKit для health-проверки: хост:порт без схемы (livekit в network_mode: host)
LIVEKIT_HEALTH_ADDR = os.getenv("LIVEKIT_HEALTH_ADDR", "127.0.0.1:7880")

APP_VERSION = os.getenv("APP_VERSION", "2.0.0")

ROOM_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")

logging.basicConfig(level=logging.INFO, format='{"ts": "%(asctime)s", "level": "%(levelname)s", "logger": "%(name)s", "msg": %(message)r}')
logger = logging.getLogger("token-server")

# --- Счётчики для /v1/metrics ---
metrics = {
    "tokens_issued": 0,
    "auth_rejected": 0,
    "rate_limited": 0,
    "server_busy": 0,
    "bad_room_name": 0,
}
# Rate limit: IP -> очередь timestamp-ов за последнюю минуту
_rate_buckets: dict[str, deque[float]] = defaultdict(deque)


def log_event(**fields):
    logger.info(json.dumps(fields, ensure_ascii=False))


def client_ip(request) -> str:
    # nginx проксирует, реальный адрес в X-Real-IP
    return request.headers.get("X-Real-IP") or request.remote


def check_api_key(request) -> bool:
    auth = request.headers.get("Authorization", "")
    return bool(CLIENT_API_KEY) and hmac.compare_digest(auth.encode(), f"Bearer {CLIENT_API_KEY}".encode())


def rate_limited(ip: str) -> bool:
    now = time.monotonic()
    bucket = _rate_buckets[ip]
    while bucket and bucket[0] < now - 60:
        bucket.popleft()
    if len(bucket) >= TOKEN_RATE_LIMIT:
        return True
    bucket.append(now)
    return False


async def livekit_room_count() -> int | None:
    """Число активных комнат. None - LiveKit API недоступен."""
    lk_api = api.LiveKitAPI(url=LIVEKIT_URL, api_key=API_KEY, api_secret=API_SECRET)
    try:
        resp = await asyncio.wait_for(
            lk_api.room.list_rooms(api.ListRoomsRequest()), timeout=5,
        )
        return len(resp.rooms)
    except Exception:
        return None
    finally:
        await lk_api.aclose()


async def svc_livekit() -> str:
    try:
        host, _, port = LIVEKIT_HEALTH_ADDR.rpartition(":")
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host or "127.0.0.1", int(port or 7880)),
            timeout=3,
        )
        writer.close()
        await writer.wait_closed()
        return "ok"
    except Exception:
        return "error"


async def svc_http(url):
    try:
        async with httpx.AsyncClient(trust_env=False) as client:
            resp = await client.get(url, timeout=3)
            return "ok" if resp.status_code == 200 else "error"
    except httpx.HTTPError:
        return "error"


async def health(request):
    livekit, inference, llm = await asyncio.gather(
        svc_livekit(), svc_http(f"{INFERENCE_URL}/healthz"), svc_http(LLM_HEALTH_URL),
    )
    services = {"livekit": livekit, "inference": inference, "llm": llm}
    return web.json_response({
        "status": "ok" if all(value == "ok" for value in services.values()) else "degraded",
        "version": APP_VERSION, "services": services,
    })


async def ready(request):
    return web.json_response({"status": "ok"})


async def get_token(request):
    versioned = request.path.startswith("/v1")
    if versioned and not check_api_key(request):
        metrics["auth_rejected"] += 1
        log_event(event="auth_rejected", ip=client_ip(request))
        return web.json_response({"error": "unauthorized"}, status=401)

    ip = client_ip(request)
    if rate_limited(ip):
        metrics["rate_limited"] += 1
        log_event(event="rate_limited", ip=ip)
        return web.json_response({"error": "too many requests"}, status=429)

    room_name = request.query.get("room", "")
    if not ROOM_NAME_RE.fullmatch(room_name):
        metrics["bad_room_name"] += 1
        return web.json_response({"error": "invalid room name"}, status=400)

    room_count = await livekit_room_count()
    if room_count is None:
        log_event(event="livekit_api_error", ip=ip)
        return web.json_response({"error": "livekit unavailable"}, status=503)
    if room_count >= MAX_SESSIONS:
        metrics["server_busy"] += 1
        log_event(event="server_busy", ip=ip, rooms=room_count)
        return web.json_response({"error": "server busy"}, status=429)

    identity = "user_" + uuid4().hex[:8]

    token = (
        api.AccessToken(API_KEY, API_SECRET)
        .with_grants(
            api.VideoGrants(
                room=room_name,
                room_join=True,
                can_publish=True,
                can_subscribe=True,
                can_publish_data=False,
                can_publish_sources=["microphone"],
            )
        )
        .with_identity(identity)
        .with_ttl(TOKEN_TTL)
    )
    metrics["tokens_issued"] += 1
    log_event(event="token_issued", ip=ip, room=room_name, rooms=room_count)
    return web.json_response({"token": token.to_jwt(), "url": LIVEKIT_PUBLIC_URL})


async def metrics_endpoint(request):
    if not check_api_key(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    active = await livekit_room_count()
    body = (
        "# TYPE ai_voice_tokens_issued counter\n"
        f"ai_voice_tokens_issued {metrics['tokens_issued']}\n"
        "# TYPE ai_voice_auth_rejected counter\n"
        f"ai_voice_auth_rejected {metrics['auth_rejected']}\n"
        "# TYPE ai_voice_rate_limited counter\n"
        f"ai_voice_rate_limited {metrics['rate_limited']}\n"
        "# TYPE ai_voice_server_busy counter\n"
        f"ai_voice_server_busy {metrics['server_busy']}\n"
        "# TYPE ai_voice_active_rooms gauge\n"
        f"ai_voice_active_rooms {active if active is not None else -1}\n"
    )
    return web.Response(text=body, content_type="text/plain")


async def index(request):
    return web.FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-store"})


async def owl_pose(request):
    return web.FileResponse(
        STATIC_DIR / "avatars" / f"owl-{request.match_info['pose']}.webp",
        headers={"Content-Type": "image/webp"},
    )


def admin_authorized(request):
    scheme, _, encoded = request.headers.get("Authorization", "").partition(" ")
    if scheme.lower() != "basic" or not ADMIN_PASSWORD:
        return False
    try:
        credentials = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        return False
    return hmac.compare_digest(credentials, f"{ADMIN_USER}:{ADMIN_PASSWORD}".encode())


def admin_challenge():
    return web.json_response(
        {"error": "Войдите с учётной записью администратора."}, status=401,
        headers={**NO_STORE, "WWW-Authenticate": 'Basic realm="IU5 Knowledge", charset="UTF-8"'},
    )


async def admin_page(request):
    if not admin_authorized(request):
        return admin_challenge()
    return web.FileResponse(Path(__file__).with_name("admin.html"), headers=NO_STORE)


async def admin_api(request):
    if not admin_authorized(request):
        return admin_challenge()
    if request.method != "GET":
        origin = request.headers.get("Origin")
        if request.headers.get("Sec-Fetch-Site") == "cross-site" or (origin and origin != PUBLIC_ORIGIN):
            return web.json_response({"error": "Недопустимый источник запроса."}, status=403, headers=NO_STORE)
        if request.content_type != "application/json":
            return web.json_response({"error": "Требуется application/json."}, status=415, headers=NO_STORE)
    path = "/documents"
    if request.path.endswith("/import"):
        path += "/import"
    elif "id" in request.match_info:
        path += "/" + request.match_info["id"]
    try:
        response = await request.app[HTTP_CLIENT].request(
            request.method, INFERENCE_URL + path,
            headers={"Authorization": f"Bearer {INFERENCE_TOKEN}", "Content-Type": "application/json"},
            content=await request.read() if request.method in {"PUT", "POST"} else None,
        )
        if response.status_code < 500:
            return web.Response(status=response.status_code, body=response.content,
                                content_type="application/json", headers=NO_STORE)
    except httpx.HTTPError:
        logger.exception("RAG service unavailable")
    return web.json_response({"error": "База знаний недоступна. Повторите позже."}, status=503, headers=NO_STORE)


async def http_client(app):
    async with httpx.AsyncClient(timeout=240, trust_env=False) as client:
        app[HTTP_CLIENT] = client
        yield


def create_app():
    app = web.Application(client_max_size=6 * 1024**2)
    app.cleanup_ctx.append(http_client)
    app.router.add_get("/ready", ready)
    app.router.add_get("/v1/health", health)
    app.router.add_get("/v1/token", get_token)
    app.router.add_get("/v1/metrics", metrics_endpoint)
    app.router.add_get("/token", get_token)
    app.router.add_get("/admin", admin_page)
    app.router.add_get("/admin/", admin_page)
    app.router.add_get("/admin/api/documents", admin_api)
    app.router.add_post("/admin/api/documents", admin_api)
    app.router.add_post("/admin/api/documents/import", admin_api)
    app.router.add_put(r"/admin/api/documents/{id:[0-9]+}", admin_api)
    app.router.add_delete(r"/admin/api/documents/{id:[0-9]+}", admin_api)
    app.router.add_get("/", index)
    app.router.add_get("/index.html", index)
    app.router.add_get(
        r"/avatars/owl-{pose:neutral|raise-(?:low|mid|high)|thinking|cap|beak-(?:half|open)}.webp",
        owl_pose,
    )
    app.router.add_static("/", STATIC_DIR)
    return app


if __name__ == "__main__":
    web.run_app(create_app(), port=80)
