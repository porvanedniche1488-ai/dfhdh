# -*- coding: utf-8 -*-
"""
Telegram-бот: сигнали Binance Futures + платна підписка в Telegram Stars.

Команди користувача:
  /start  - привітання
  /help   - довідка
  /buy    - купити пропуск (оплата зірками Telegram)
  /status - статус підписки
  /signal - поточний топ-сигнал (для активних)

Команди адміна (ADMIN_ID):
  /setprice <зірки>     - ціна пропуску
  /setdays <днів>       - тривалість пропуску
  /grant <uid> [днів]   - видач/подовження доступу вручну
  /revoke <uid>         - зняти доступ
  /broadcast <текст>    - розсилка всім активним
  /stats                - статистика
  /scan                 - примусовий скан ринку + розсилка
"""
from __future__ import annotations

import asyncio
import logging
import os
import time

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application, CommandHandler, ContextTypes, MessageHandler, filters,
    CallbackQueryHandler, PreCheckoutQueryHandler,
)

import config
import database as db

db.init_db()  # гарантуємо наявність таблиць до побудови HELP_TEXT

from analyzer import Analyzer, format_signal

logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
log = logging.getLogger("bot")

# глобальне посилання на Application — щоб scheduler міг надсилати повідомлення
_app: Application | None = None

HELP_TEXT = (
    "🤖 <b>Binance Signals Bot</b>\n\n"
    "<b>Що робить:</b> аналізує ринок Binance Futures і надсилає готові угоди:\n"
    "🟢 ДОВГИЙ/КОРОТКИЙ ЛОНГ, 🔴 ДОВГИЙ/КОРОТКИЙ ШОРТ\n"
    "У кожній угоді: монета, ціна входу, стоп-лос, 2 тейк-профіти та оцінка шансу.\n"
    "Сигнали приходять автоматично раз на {interval} хв.\n\n"
    "<b>Команди:</b>\n"
    "/buy — купити пропуск (⭐️ Telegram Stars)\n"
    "/status — мій доступ\n"
    "/signal — останній сигнал просто зараз\n"
    "/help — ця довідка\n\n"
    "<b>Оплата:</b> ⭐️ {price} за {days} днів. Без очікування — оплата через Telegram "
    "і доступ одразу."
).format(interval=config.SCAN_INTERVAL_MIN, price=db.get_price(), days=db.get_sub_days())


async def is_admin(uid: int) -> bool:
    return uid == config.ADMIN_ID


# ---------------- user commands ----------------

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    db.register_user(u.id, u.username, u.first_name)
    price = db.get_price()
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("⭐️ Купити пропуск", callback_data="buy")]])
    if db.is_active(u.id):
        left = db.sub_info(u.id)
        await update.message.reply_text(
            f"Привіт, {u.first_name}! ✅ Твій доступ активний ще {left/86400:.1f} дн. "
            f"Сигнали приходитимуть автоматично раз на {config.SCAN_INTERVAL_MIN} хв.",
            reply_markup=kb,
        )
    else:
        await update.message.reply_text(
            f"Привіт, {u.first_name}! 👋\n\n"
            f"Я даю готові угоди по Binance Futures: лонг/шорт, стоп-лос, 2 тейки, шанс.\n"
            f"Раз на {config.SCAN_INTERVAL_MIN} хвилин — найкраща сделка прямо в чат.\n\n"
            f"🎫 Пропуск: ⭐️{price} на {db.get_sub_days()} днів.",
            reply_markup=kb,
        )


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP_TEXT, parse_mode=ParseMode.HTML)


async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    if db.is_active(u.id):
        left = db.sub_info(u.id)
        await update.message.reply_text(f"✅ Доступ активний. Залишилось: {left/86400:.1f} дн.")
    else:
        await update.message.reply_text(
            f"❌ Пропуску немає. /buy — коштує ⭐️{db.get_price()} на {db.get_sub_days()} днів."
        )


async def cb_buy(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Кнопка 'купити' — кидаємо invoice зі сплатою зірками."""
    q = update.callback_query
    await q.answer()
    price = db.get_price()
    await ctx.bot.send_invoice(
        chat_id=q.from_user.id,
        title=f"Пропуск на {db.get_sub_days()} днів",
        description="Доступ до сигналів Binance Futures: лонг/шорт зі стопами та тейками.",
        payload=f"sub_{q.from_user.id}_{int(time.time())}",
        provider_token="",           # порожньо для Telegram Stars
        currency="XTR",              # XTR = Telegram Stars
        prices=[LabeledPrice(label="Пропуск", amount=price)],
    )


async def cmd_buy(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    price = db.get_price()
    await ctx.bot.send_invoice(
        chat_id=update.effective_chat.id,
        title=f"Пропуск на {db.get_sub_days()} днів",
        description="Доступ до сигналів Binance Futures.",
        payload=f"sub_{update.effective_user.id}_{int(time.time())}",
        provider_token="",
        currency="XTR",
        prices=[LabeledPrice(label="Пропуск", amount=price)],
    )


async def on_precheckout(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.pre_checkout_query.answer(ok=True)


async def on_successful_payment(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    sp = update.message.successful_payment
    uid = update.effective_user.id
    db.add_sub(uid, db.get_sub_days())
    log.info("Payment success uid=%s stars=%s", uid, sp.total_amount)
    await update.message.reply_text(
        f"🎉 Оплачено! Пропуск активовано на {db.get_sub_days()} днів.\n"
        f"Перший сигнал — найближчим часом, далі раз на {config.SCAN_INTERVAL_MIN} хв."
    )


async def cmd_signal(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    if not db.is_active(u.id) and not await is_admin(u.id):
        await update.message.reply_text(f"🔒 Сигнали за підпискою. /buy — ⭐️{db.get_price()}")
        return
    msg = await update.message.reply_text("⏳ Сканую ринок…")
    sigs = await ctx.bot.data["analyzer"].scan_market(config.TOP_N_SYMBOLS, max_signals=1)
    if not sigs:
        await msg.edit_text("Зараз сильних сетапів немає. Чекай наступного скану або /signal пізніше.")
    else:
        await msg.edit_text(format_signal(sigs[0]))


# ---------------- admin commands ----------------

async def cmd_setprice(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.effective_user.id):
        return
    try:
        val = int(ctx.args[0])
        assert 1 <= val <= 100000
    except Exception:
        await update.message.reply_text("Формат: /setprice 50  (ціна в зірках)")
        return
    db.set_price(val)
    await update.message.reply_text(f"💰 Нова ціна пропуску: ⭐️{val}")


async def cmd_setdays(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.effective_user.id):
        return
    try:
        val = int(ctx.args[0])
        assert 1 <= val <= 3650
    except Exception:
        await update.message.reply_text("Формат: /setdays 30")
        return
    db.set_sub_days(val)
    await update.message.reply_text(f"📅 Тривалість пропуску: {val} днів")


async def cmd_grant(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.effective_user.id):
        return
    try:
        uid = int(ctx.args[0])
        days = int(ctx.args[1]) if len(ctx.args) > 1 else db.get_sub_days()
    except Exception:
        await update.message.reply_text("Формат: /grant 123456789 [днів]")
        return
    db.add_sub(uid, days)
    await update.message.reply_text(f"✅ Видано {days} дн. користувачу {uid}")


async def cmd_revoke(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.effective_user.id):
        return
    try:
        uid = int(ctx.args[0])
    except Exception:
        await update.message.reply_text("Формат: /revoke 123456789")
        return
    with db.db() as c:
        c.execute("DELETE FROM subs WHERE uid=?", (uid,))
    await update.message.reply_text(f"🚫 Доступ знято для {uid}")


async def cmd_broadcast(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.effective_user.id):
        return
    text = " ".join(ctx.args)
    if not text:
        await update.message.reply_text("Формат: /broadcast Привіт усім!")
        return
    users = db.active_users()
    sent, failed = 0, 0
    for uid in users:
        try:
            await ctx.bot.send_message(uid, text)
            sent += 1
        except Exception:
            failed += 1
    await update.message.reply_text(f"📣 Розіслано: {sent}, помилок: {failed}")


async def cmd_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.effective_user.id):
        return
    with db.db() as c:
        total = c.execute("SELECT COUNT(*) n FROM users").fetchone()["n"]
        active = len(db.active_users())
    await update.message.reply_text(
        f"📊 Всього юзерів: {total}\n✅ З доступом: {active}\n💰 Ціна: ⭐️{db.get_price()} / {db.get_sub_days()} дн."
    )


async def cmd_scan(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.effective_user.id):
        return
    await update.message.reply_text("🔍 Запускаю скан…")
    await scan_job()
    await update.message.reply_text("✅ Скан завершено (див. розсилку підписникам).")


# ---------------- scheduler: signals to subscribers ----------------

async def post_init(app: Application):
    """Ініціалізація після старту: аналайзер + планувальник скану ринку."""
    global _app
    # у python-telegram-bot v21+ post_init отримує ApplicationBuilder, а не Application
    application = getattr(app, "application", None) or app
    _app = application
    if hasattr(application, "data"):
        application.data["analyzer"] = Analyzer()
    sched = AsyncIOScheduler(timezone="UTC")
    sched.add_job(scan_job, trigger="interval", minutes=config.SCAN_INTERVAL_MIN)
    sched.start()
    if hasattr(application, "data"):
        application.data["sched"] = sched
    log.info("Scheduler started every %d min", config.SCAN_INTERVAL_MIN)
    try:
        await start_health_server()
    except Exception as e:
        log.warning("Health server failed to start: %s", e)


async def scan_job():
    """Періодичне завдання: скан ринку та розсилка активним підписникам."""
    if _app is None:
        return
    analyzer = getattr(_app, "data", {}).get("analyzer") or Analyzer()
    _app.data["analyzer"] = analyzer
    try:
        sigs = await analyzer.scan_market(config.TOP_N_SYMBOLS, max_signals=3)
    except Exception as e:
        log.error("scan failed: %s", e)
        return
    if not sigs:
        log.info("no signals this round")
        return
    users = db.active_users()
    log.info("broadcasting %d signal(s) to %d users", len(sigs), len(users))
    for uid in users:
        for s in sigs:
            try:
                await _app.bot.send_message(uid, format_signal(s))
                await asyncio.sleep(0.1)  # антифлуд
            except Exception as e:
                log.warning("send to %s failed: %s", uid, e)


async def err_handler(update: object, ctx: ContextTypes.DEFAULT_TYPE):
    log.error("Update error: %s", ctx.error)


async def start_health_server():
    """Мікро HTTP-сервер з /health — щоб Railway бачив, що бот живий,
    і не вбивав процес (підмінна змінна PORT задається автоматично)."""
    from aiohttp import web

    async def health(request):
        return web.json_response({"status": "ok", "bot": "binance-signals"})

    app = web.Application()
    app.router.add_get("/health", health)
    app.router.add_get("/", health)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", int(os.environ.get("PORT", "8080")))
    await site.start()
    log.info("Health server on port %s", os.environ.get("PORT", "8080"))


def main():
    db.init_db()
    app = Application.builder().token(config.BOT_TOKEN).build()
    app.post_init = post_init

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("buy", cmd_buy))
    app.add_handler(CommandHandler("signal", cmd_signal))
    app.add_handler(CallbackQueryHandler(cb_buy, pattern="^buy$"))

    # admin
    app.add_handler(CommandHandler("setprice", cmd_setprice))
    app.add_handler(CommandHandler("setdays", cmd_setdays))
    app.add_handler(CommandHandler("grant", cmd_grant))
    app.add_handler(CommandHandler("revoke", cmd_revoke))
    app.add_handler(CommandHandler("broadcast", cmd_broadcast))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("scan", cmd_scan))

    # payments
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, on_successful_payment))
    from telegram.ext import PreCheckoutQueryHandler
    app.add_handler(PreCheckoutQueryHandler(on_precheckout))

    app.add_error_handler(err_handler)

    print("Bot is running… Ctrl+C to stop.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
