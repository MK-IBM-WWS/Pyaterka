"""Backend regressions. Run: python3 -m unittest discover -s tests -v"""

import base64
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import jwt
from aiohttp.test_utils import TestClient, TestServer

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "web"))
import token_server as server


class WebTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.patches = [
            patch.object(server, "ADMIN_PASSWORD", "private-admin"),
            patch.object(server, "PUBLIC_ORIGIN", "https://voice.example.test"),
            patch.object(server, "API_KEY", "test-livekit-key"),
            patch.object(server, "API_SECRET", "local-test-secret-with-at-least-32-characters"),
            patch.object(server, "CLIENT_API_KEY", "test-client-key"),
            patch.object(server, "LIVEKIT_PUBLIC_URL", "wss://voice.example.test"),
            patch.object(server, "livekit_room_count", AsyncMock(return_value=0)),
            patch.object(server, "svc_livekit", AsyncMock(return_value="ok")),
            patch.object(server, "svc_http", AsyncMock(return_value="ok")),
        ]
        for item in self.patches:
            item.start()
        server._rate_buckets.clear()
        self.client = TestClient(TestServer(server.create_app()))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        for item in reversed(self.patches):
            item.stop()

    async def test_page_is_available_without_working_directory_dependency(self):
        for path in ("/", "/index.html"):
            response = await self.client.get(path)
            self.assertEqual(response.status, 200)
            self.assertEqual(response.headers["Cache-Control"], "no-store")
            body = await response.text()
            header = body.split('<header class="topbar">', 1)[1].split("</header>", 1)[0]
            self.assertLess(header.index('class="department-logo"'), header.index("<h1>"))
            self.assertLess(header.index("<h1>"), header.index('id="controls-toggle"'))
            self.assertIn('aria-controls="controls"', header)
            self.assertIn('id="connect"', header)
            self.assertNotIn('id="rec-btn"', body)
            self.assertNotIn('id="status"', body)
            self.assertNotIn('Диагностика', body)

    async def test_browser_modules_are_served_as_javascript(self):
        for path in (
            "/vendor/livekit-client-2.5.0.esm.mjs",
            "/assistant.mjs",
            "/owl.mjs",
            "/beak.mjs",
        ):
            response = await self.client.get(path)
            self.assertEqual(response.status, 200, path)
            self.assertIn("javascript", response.headers["Content-Type"], path)
            self.assertNotIn("<html", (await response.text())[:200].lower(), path)

    async def test_avatar_poses_are_available_as_webp(self):
        for pose in ("neutral", "raise-low", "raise-mid", "raise-high", "thinking", "cap", "beak-half", "beak-open"):
            response = await self.client.get(f"/avatars/owl-{pose}.webp")
            self.assertEqual(response.status, 200, pose)
            self.assertEqual(response.content_type, "image/webp", pose)
            self.assertEqual((await response.read())[8:12], b"WEBP", pose)

    async def test_versioned_token_requires_bearer_authentication(self):
        response = await self.client.get("/v1/token?room=test")
        self.assertEqual(response.status, 401)
        self.assertEqual((await response.json())["error"], "unauthorized")

    async def test_valid_token_has_room_grant_and_public_url(self):
        response = await self.client.get(
            "/v1/token?room=voice_regression",
            headers={"Authorization": "Bearer test-client-key"},
        )
        self.assertEqual(response.status, 200)
        data = await response.json()
        self.assertEqual(data["url"], "wss://voice.example.test")
        claims = jwt.decode(data["token"], server.API_SECRET, algorithms=["HS256"])
        self.assertEqual(claims["video"]["room"], "voice_regression")
        self.assertTrue(claims["video"]["roomJoin"])
        self.assertTrue(claims["video"]["canPublish"])
        self.assertTrue(claims["sub"].startswith("user_"))

    async def test_nginx_web_client_token_route_is_preserved(self):
        # Basic Auth is enforced by nginx, rather than by the Python service.
        response = await self.client.get("/token?room=voice_legacy")
        self.assertEqual(response.status, 200)
        self.assertIn("token", await response.json())

    async def test_invalid_room_is_rejected(self):
        response = await self.client.get("/token?room=bad%20room")
        self.assertEqual(response.status, 400)

    async def test_unavailable_livekit_returns_diagnostic_error(self):
        server.livekit_room_count.return_value = None
        response = await self.client.get("/token?room=voice_test")
        self.assertEqual(response.status, 503)
        self.assertEqual((await response.json())["error"], "livekit unavailable")

    async def test_full_server_returns_busy_error(self):
        server.livekit_room_count.return_value = server.MAX_SESSIONS
        response = await self.client.get("/token?room=voice_test")
        self.assertEqual(response.status, 429)
        self.assertEqual((await response.json())["error"], "server busy")

    async def test_repeated_requests_are_limited(self):
        with patch.object(server, "TOKEN_RATE_LIMIT", 1):
            self.assertEqual((await self.client.get("/token?room=first")).status, 200)
            response = await self.client.get("/token?room=second")
            self.assertEqual(response.status, 429)
            self.assertEqual((await response.json())["error"], "too many requests")

    async def test_health_remains_public_and_reports_degraded_services(self):
        server.svc_livekit.return_value = "error"
        response = await self.client.get("/v1/health")
        self.assertEqual(response.status, 200)
        self.assertEqual((await response.json())["status"], "degraded")

    async def test_admin_page_and_proxy_require_separate_credentials(self):
        response = await self.client.get("/admin/")
        self.assertEqual(response.status, 401)
        self.assertEqual((await self.client.get("/admin.html")).status, 404)
        credential = base64.b64encode(b"rag-admin:private-admin").decode()
        headers = {"Authorization": "Basic " + credential}
        response = await self.client.get("/admin/", headers=headers)
        self.assertEqual(response.status, 200)
        self.assertIn("База знаний ИУ5", await response.text())
        self.assertEqual((await self.client.get("/admin/api/documents")).status, 401)
        upstream = self.client.server.app[server.HTTP_CLIENT]
        with patch.object(upstream, "request", AsyncMock(return_value=httpx.Response(201, json={"ids":[1]}))) as request:
            endpoint = "/admin/api/documents"
            denied = await self.client.post(endpoint, headers={**headers,"Origin":"https://different.example"}, json={})
            self.assertEqual(denied.status, 403)
            created = await self.client.post(endpoint, headers={**headers,"Origin":server.PUBLIC_ORIGIN}, json={})
            self.assertEqual(created.status, 201)
            self.assertEqual(await created.json(), {"ids":[1]})
            request.assert_awaited_once()

    async def test_metrics_remain_protected(self):
        response = await self.client.get("/v1/metrics")
        self.assertEqual(response.status, 401)


if __name__ == "__main__":
    unittest.main()
