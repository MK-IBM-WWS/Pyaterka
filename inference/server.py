import asyncio
import hmac
import json
import os
import sqlite3
from pathlib import Path

from aiohttp import web

from knowledge import Knowledge
from models import CpuModels, SAMPLE_RATE

SECRET = os.environ.get("INFERENCE_TOKEN", "")
STATE = web.AppKey("state", dict)
SLOTS = web.AppKey("slots", dict)


@web.middleware
async def internal_api(request, handler):
    if request.path != "/healthz" and (
        not SECRET or not hmac.compare_digest(request.headers.get("Authorization", "").encode(), f"Bearer {SECRET}".encode())
    ):
        return web.json_response({"error": "unauthorized"}, status=401)
    try:
        return await handler(request)
    except (ValueError, TypeError) as exc:
        return web.json_response({"error": str(exc)}, status=400)
    except KeyError:
        return web.json_response({"error": "Запись не найдена."}, status=404)
    except sqlite3.IntegrityError:
        return web.json_response({"error": "В этой категории уже есть запись с таким заголовком."}, status=409)


async def startup(app):
    models = await asyncio.to_thread(CpuModels)
    path = Path(os.getenv("RAG_DB", "/data/knowledge.sqlite3"))
    path.parent.mkdir(parents=True, exist_ok=True)
    knowledge = Knowledge(path, models.embed, "BAAI/bge-m3-v1",
                          min_similarity=float(os.getenv("RAG_MIN_SIMILARITY", "0.52")))
    app[STATE].update(models=models, knowledge=knowledge)


async def health(request):
    ready = bool(request.app[STATE])
    return web.json_response({"status": "ok" if ready else "loading"}, status=200 if ready else 503)


async def run_model(request, slot, func, *args):
    async with request.app[SLOTS][slot]:
        return await asyncio.to_thread(func, *args)


async def recognize(request):
    body = await request.read()
    sample_rate = int(request.headers.get("X-Sample-Rate", "16000"))
    if sample_rate not in {8000, 16000, 24000, 32000, 44100, 48000} or len(body) % 2:
        raise ValueError("Некорректный формат звука.")
    if not body or len(body) / (2 * sample_rate) > 90:
        raise ValueError("Реплика должна длиться до 90 секунд.")
    text = await run_model(request, "whisper", request.app[STATE]["models"].transcribe, body, sample_rate)
    return web.json_response({"text": text})


async def synthesize(request):
    text = (await request.json()).get("text", "")
    if not isinstance(text, str) or not text.strip() or len(text) > 800:
        raise ValueError("Некорректный текст для озвучивания.")
    pcm = await run_model(request, "voice", request.app[STATE]["models"].synthesize, text)
    return web.Response(body=pcm, content_type="application/octet-stream", headers={"X-Sample-Rate": str(SAMPLE_RATE)})


async def search(request):
    body = await request.json()
    query = body.get("query", "")
    if not isinstance(query, str) or not 1 <= len(query.strip()) <= 650:
        raise ValueError("Некорректный поисковый запрос.")
    results = await run_model(request, "search", request.app[STATE]["knowledge"].search, query.strip())
    return web.json_response({"results": results})


async def documents(request):
    db = request.app[STATE]["knowledge"]
    doc_id = int(request.match_info["id"]) if "id" in request.match_info else None
    if request.method == "GET":
        return web.json_response({"documents": await asyncio.to_thread(db.list)})
    if request.method == "DELETE":
        if not await run_model(request, "write", db.delete, doc_id):
            raise KeyError(doc_id)
        return web.Response(status=204)
    try:
        data = await request.json()
    except json.JSONDecodeError:
        raise ValueError("Некорректный JSON.") from None
    importing = request.path.endswith("/import")
    ids = await run_model(request, "write", db.write, data if importing else [data], doc_id, importing)
    return web.json_response({"ids": ids}, status=200 if importing or doc_id else 201)


def create_app(initialize=startup):
    app = web.Application(client_max_size=10 * 1024**2, middlewares=[internal_api])
    app[STATE] = {}
    app[SLOTS] = {key: asyncio.Semaphore(1) for key in ("whisper", "voice", "search", "write")}
    app.on_startup.append(initialize)
    app.router.add_get("/healthz", health)
    app.router.add_post("/stt", recognize)
    app.router.add_post("/tts", synthesize)
    app.router.add_post("/search", search)
    app.router.add_get("/documents", documents)
    app.router.add_post("/documents", documents)
    app.router.add_post("/documents/import", documents)
    app.router.add_put(r"/documents/{id:[0-9]+}", documents)
    app.router.add_delete(r"/documents/{id:[0-9]+}", documents)
    return app


if __name__ == "__main__":
    if not SECRET:
        raise RuntimeError("INFERENCE_TOKEN is required")
    web.run_app(create_app(), port=8650)
