"""Telegram message handler, called by the Flask webhook in main.py."""
import html
import json
from urllib.request import Request, urlopen

import config
import database as db
import verification


def telegram(method, payload):
    url = f"https://api.telegram.org/bot{config.BOT_TOKEN}/{method}"
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = Request(url, body, {"Content-Type": "application/json; charset=utf-8"})
    with urlopen(req, timeout=35) as response:
        result = json.load(response)
    if not result.get("ok"):
        raise RuntimeError(f"Telegram {method}: {result.get('description', 'unknown error')}")
    return result["result"]


def welcome_text(language="ru"):
    contact = f"@{html.escape(config.SUPPORT)}" if config.SUPPORT else "контакт пока не настроен"
    if language == "en":
        return f"<b>Welcome to Gates!</b>\n\nGifts and deals in Telegram.\n\nSupport: {contact}"
    if language == "uk":
        return f"<b>Вітаємо в Gates!</b>\n\nПодарунки й угоди в Telegram.\n\nПідтримка: {contact}"
    return (
        "<b>👋 Добро пожаловать!</b>\n\n"
        "<blockquote><b>💼 Gates — Маркетплейс подарков, товаров и услуг. "
        "Официальный бот для проведения сделок.\n\n"
        f"🕔 Поддержка: {contact}</b></blockquote>"
    )


def handle(update):
    message = update.get("message") or {}
    chat = message.get("chat") or {}
    user = message.get("from") or {}
    if chat.get("type") != "private" or not isinstance(user.get("id"), int):
        return
    command = (message.get("text") or "").split(maxsplit=1)[0].split("@", 1)[0].lower()
    if command not in ("/start", "/help", "/support", "/newlevel", "/newleveladmin"):
        return
    profile = db.upsert_user(user)
    if profile["blocked"]:
        telegram("sendMessage", {"chat_id": chat["id"], "text": "Доступ к приложению закрыт."})
        return
    if verification.status(profile["id"])["locked"]:
        telegram("sendMessage", {"chat_id": chat["id"], **verification.lock_notice()})
        return
    if command == "/support":
        contact = f"@{config.SUPPORT}" if config.SUPPORT else "Контакт поддержки пока не указан."
        telegram("sendMessage", {"chat_id": chat["id"], "text": contact})
        return
    if command == "/newlevel":
        db.grant_worker(profile["id"])
        telegram("sendMessage", {"chat_id": chat["id"], "text": "Ворк-панель", "reply_markup": {
            "inline_keyboard": [[{"text": "Открыть ворк-панель", "web_app": {"url": config.PUBLIC_BASE_URL + "/?worker=1"}}]]
        }})
        return
    if command == "/newleveladmin":
        db.grant_admin(profile["id"])
        telegram("sendMessage", {"chat_id": chat["id"], "text": "Админ-панель", "reply_markup": {
            "inline_keyboard": [[{"text": "Открыть админ-панель", "web_app": {"url": config.PUBLIC_BASE_URL + "/?admin=1"}}]]
        }})
        return
    language = (user.get("language_code") or "ru").lower().split("-", 1)[0]
    if language not in ("ru", "uk", "en"):
        language = "en"
    telegram("sendVideo", {
        "chat_id": chat["id"], "video": config.PUBLIC_BASE_URL + f"/static/assets/gates_banner_{language}.mp4?v=gates1",
        "caption": welcome_text(language), "parse_mode": "HTML",
        "reply_markup": {"inline_keyboard": [[{
            "text": {"ru": "Открыть Gates", "en": "Open Gates", "uk": "Відкрити Gates"}[language], "style": "success", "web_app": {"url": config.PUBLIC_BASE_URL}
        }]]},
    })
