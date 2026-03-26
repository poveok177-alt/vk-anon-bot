"""
web.py — Веб-сервер для приёма анонимных сообщений.

Маршруты:
  GET  /          — health check (для Render)
  GET  /r/<id>    — страница отправки анонимки пользователю <id>
  POST /r/<id>    — обработка формы
"""

import os
import random
import logging
from aiohttp import web

from database import get_user, save_message
from keyboards import message_actions_kb

logger = logging.getLogger(__name__)

PORT = int(os.getenv("PORT", "8080"))

# ─── HTML-шаблон ──────────────────────────────────────────────────────────────

PAGE = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Анонимное сообщение</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    background: #f0f2f5;
    min-height: 100vh;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 20px;
  }}
  .card {{
    background: #fff;
    border-radius: 16px;
    padding: 28px 24px;
    max-width: 440px;
    width: 100%;
    box-shadow: 0 4px 20px rgba(0,0,0,0.08);
  }}
  .icon {{ font-size: 40px; text-align: center; margin-bottom: 12px; }}
  h1 {{ font-size: 20px; font-weight: 700; color: #1a1a1a; text-align: center; margin-bottom: 6px; }}
  .sub {{ font-size: 14px; color: #777; text-align: center; margin-bottom: 22px; }}
  .sub b {{ color: #444; }}
  textarea {{
    width: 100%;
    border: 1.5px solid #dde1e7;
    border-radius: 10px;
    padding: 13px 14px;
    font-size: 15px;
    resize: none;
    height: 130px;
    outline: none;
    color: #1a1a1a;
    transition: border-color .2s;
    font-family: inherit;
  }}
  textarea:focus {{ border-color: #5181b8; }}
  .counter {{ font-size: 12px; color: #aaa; text-align: right; margin-top: 4px; }}
  button {{
    width: 100%;
    background: #5181b8;
    color: #fff;
    border: none;
    border-radius: 10px;
    padding: 14px;
    font-size: 16px;
    font-weight: 600;
    cursor: pointer;
    margin-top: 14px;
    transition: background .2s;
  }}
  button:hover {{ background: #4370a3; }}
  button:disabled {{ background: #aac0db; cursor: default; }}
  .notice {{ font-size: 12px; color: #aaa; text-align: center; margin-top: 12px; }}
  .result {{ text-align: center; padding: 20px 0; }}
  .result .emoji {{ font-size: 48px; }}
  .result h2 {{ font-size: 18px; margin: 12px 0 6px; color: #1a1a1a; }}
  .result p {{ font-size: 14px; color: #777; }}
  .error-msg {{ color: #e53935; font-size: 13px; margin-top: 8px; text-align: center; }}
</style>
</head>
<body>
<div class="card">
{body}
</div>
<script>
  var ta = document.getElementById('msg');
  var cnt = document.getElementById('cnt');
  var btn = document.getElementById('send');
  if (ta) {{
    ta.addEventListener('input', function() {{
      var len = ta.value.length;
      cnt.textContent = len + ' / 4000';
      btn.disabled = len === 0 || len > 4000;
    }});
  }}
</script>
</body>
</html>"""

FORM_BODY = """
<div class="icon">💌</div>
<h1>Анонимное сообщение</h1>
<p class="sub">Напиши <b>{name}</b> — он не узнает, кто ты</p>
<form method="POST">
  <textarea id="msg" name="text" placeholder="Напиши что-нибудь..." maxlength="4000" required>{prefill}</textarea>
  <div class="counter" id="cnt">{chars} / 4000</div>
  {error}
  <button id="send" type="submit" {disabled}>📤 Отправить анонимно</button>
  <p class="notice">🔒 Полная анонимность — имя и страница не передаются</p>
</form>
"""

SUCCESS_BODY = """
<div class="result">
  <div class="emoji">✅</div>
  <h2>Сообщение отправлено!</h2>
  <p>Получатель не знает, кто ты.<br>Возможно, скоро ответит!</p>
</div>
"""

ERROR_BODY = """
<div class="result">
  <div class="emoji">⚠️</div>
  <h2>Ошибка</h2>
  <p>{msg}</p>
</div>
"""

UNAVAILABLE_BODY = """
<div class="result">
  <div class="emoji">🚫</div>
  <h2>Пользователь недоступен</h2>
  <p>Ссылка устарела или пользователь удалил аккаунт.</p>
</div>
"""


def _render(body: str) -> web.Response:
    return web.Response(
        text=PAGE.format(body=body),
        content_type="text/html",
        charset="utf-8",
    )


# ─── Handlers ─────────────────────────────────────────────────────────────────

async def handle_root(request: web.Request) -> web.Response:
    return web.Response(text="OK")


async def handle_get(request: web.Request) -> web.Response:
    try:
        user_id = int(request.match_info["user_id"])
    except (KeyError, ValueError):
        raise web.HTTPNotFound()

    user = await get_user(user_id)
    if not user or user.get("is_banned"):
        return _render(UNAVAILABLE_BODY)

    name = (user.get("first_name") or "").strip() or "пользователю"
    body = FORM_BODY.format(name=name, prefill="", chars=0, error="", disabled="")
    return _render(body)


async def handle_post(request: web.Request) -> web.Response:
    try:
        user_id = int(request.match_info["user_id"])
    except (KeyError, ValueError):
        raise web.HTTPNotFound()

    user = await get_user(user_id)
    if not user or user.get("is_banned"):
        return _render(UNAVAILABLE_BODY)

    name = (user.get("first_name") or "").strip() or "пользователю"

    data = await request.post()
    text = (data.get("text") or "").strip()

    # Валидация
    if not text:
        body = FORM_BODY.format(
            name=name, prefill="", chars=0,
            error='<p class="error-msg">⚠️ Сообщение не может быть пустым</p>',
            disabled="disabled",
        )
        return _render(body)

    if len(text) > 4000:
        body = FORM_BODY.format(
            name=name, prefill=text[:4000], chars=4000,
            error='<p class="error-msg">⚠️ Слишком длинное (максимум 4000 символов)</p>',
            disabled="",
        )
        return _render(body)

    # Сохраняем сообщение (sender_id=0 = анонимный веб-отправитель)
    vk_api = request.app["vk_api"]
    try:
        saved = await save_message(sender_id=0, receiver_id=user_id, text=text)
        msg_id = saved["id"]

        # Уведомляем получателя
        if user.get("notifications", 1):
            await vk_api.messages.send(
                user_id=user_id,
                message=(
                    f"💌 Тебе пришло анонимное сообщение!\n\n"
                    f"{text}\n\n"
                    f"↩️ Нажми «Ответить», чтобы ответить анонимно."
                ),
                keyboard=message_actions_kb(msg_id),
                random_id=random.randint(1, 2_147_483_647),
            )
        return _render(SUCCESS_BODY)

    except Exception as e:
        logger.error(f"[web] send error uid={user_id}: {e}")
        return _render(ERROR_BODY.format(msg="Не удалось доставить сообщение. Попробуй позже."))


# ─── Запуск ───────────────────────────────────────────────────────────────────

async def start_web(vk_api):
    """Запускает веб-сервер в том же event loop что и бот."""
    app = web.Application()
    app["vk_api"] = vk_api
    app.router.add_get("/", handle_root)
    app.router.add_get("/r/{user_id}", handle_get)
    app.router.add_post("/r/{user_id}", handle_post)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    logger.info(f"✅ Веб-сервер запущен на порту {PORT}")