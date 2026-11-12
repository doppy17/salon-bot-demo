"""HTTP-заглушка для хостинга: Render (и любой PaaS) требует слушать $PORT.

Бот работает через long polling, поэтому веб-сервер нужен только чтобы платформа
видела живой процесс: /healthz отдаёт 200, если поллинг запущен.
"""
import os

from aiohttp import web

STARTED = False


async def healthz(request: web.Request) -> web.Response:
    return web.json_response({"ok": STARTED, "service": "salon-bot-demo"})


async def root(request: web.Request) -> web.Response:
    return web.Response(
        text="Telegram-бот записи клиентов. Откройте бота в Telegram: /start",
        content_type="text/plain",
    )


def make_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/", root)
    app.router.add_get("/healthz", healthz)
    return app


async def start(port: int | None = None) -> web.AppRunner:
    """Поднимает сервер на $PORT. Возвращает runner, чтобы вызывающий мог закрыть."""
    port = port or int(os.getenv("PORT", "8080"))
    runner = web.AppRunner(make_app())
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    return runner
