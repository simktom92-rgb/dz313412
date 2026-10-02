from __future__ import annotations

import asyncio
import json
import os
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputFile, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters


BASE_DIR = Path(__file__).resolve().parent
PROFILES_PATH = BASE_DIR / "data" / "profiles.json"
DATABASE_PATH = BASE_DIR / "data" / "users.sqlite3"


def init_database() -> None:
    with closing(sqlite3.connect(DATABASE_PATH)) as db, db:
        db.execute("CREATE TABLE IF NOT EXISTS profiles (user_id INTEGER PRIMARY KEY, payload TEXT NOT NULL)")


def get_own_profile(user_id: int) -> dict | None:
    with closing(sqlite3.connect(DATABASE_PATH)) as db, db:
        row = db.execute("SELECT payload FROM profiles WHERE user_id = ?", (user_id,)).fetchone()
    return json.loads(row[0]) if row else None


def save_own_profile(user_id: int, profile: dict) -> None:
    with closing(sqlite3.connect(DATABASE_PATH)) as db, db:
        db.execute("INSERT OR REPLACE INTO profiles VALUES (?, ?)", (user_id, json.dumps(profile, ensure_ascii=False)))


def own_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📷 Изменить фото", callback_data="edit:photo"), InlineKeyboardButton("✏️ Имя", callback_data="edit:name")],
        [InlineKeyboardButton("📝 Описание", callback_data="edit:about")],
        [InlineKeyboardButton("🎂 Возраст", callback_data="edit:age"), InlineKeyboardButton("📍 Город", callback_data="edit:city")],
        [InlineKeyboardButton("🔄 Заполнить заново", callback_data="create")],
        [InlineKeyboardButton("🏠 Меню", callback_data="menu")],
    ])


def cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("Отмена", callback_data="cancel")]])


PROMPTS = {
    "name": "Как тебя зовут? Введи имя (до 40 символов).",
    "age": "Сколько тебе лет? Введи возраст от 18 до 99.",
    "city": "Из какого ты города? (до 60 символов)",
    "about": "Расскажи о себе и кого хочешь найти (до 600 символов).",
    "photo": "Отправь фотографию для своей анкеты как фото, а не файлом.",
}
FIELDS = list(PROMPTS)


async def show_own_profile(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.type != "private":
        await update.effective_message.reply_text("Создавать и менять анкету можно в личном чате с ботом.")
        return
    profile = get_own_profile(update.effective_user.id)
    if not profile:
        await update.effective_message.reply_text("У тебя пока нет анкеты. Давай создадим!", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("➕ Создать анкету", callback_data="create")]]))
        return
    await context.bot.send_photo(chat_id=update.effective_chat.id, photo=profile["photo"], caption=f"💌 {profile['name']}, {profile['age']}\n📍 {profile['city']}\n\n{profile['about']}", reply_markup=own_keyboard())


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop("profile_form", None)
    await update.effective_message.reply_text("Заполнение отменено. Сохранённая анкета осталась прежней.", reply_markup=menu_keyboard())


async def receive_profile_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    form = context.user_data.get("profile_form")
    if not form:
        await update.effective_message.reply_text("Выбери действие в меню.", reply_markup=menu_keyboard())
        return
    field = form["field"]
    message = update.effective_message
    value = (message.text or "").strip()
    if field == "photo":
        if not message.photo:
            await message.reply_text(PROMPTS[field], reply_markup=cancel_keyboard())
            return
        value = message.photo[-1].file_id
    elif field == "age":
        if not value.isascii() or not value.isdigit() or not 18 <= int(value) <= 99:
            await message.reply_text(PROMPTS[field], reply_markup=cancel_keyboard())
            return
        value = int(value)
    elif not value or len(value) > {"name": 40, "city": 60, "about": 600}[field]:
        await message.reply_text(PROMPTS[field], reply_markup=cancel_keyboard())
        return
    form["draft"][field] = value
    if form["mode"] == "create" and field != FIELDS[-1]:
        form["field"] = FIELDS[FIELDS.index(field) + 1]
        await message.reply_text(PROMPTS[form["field"]], reply_markup=cancel_keyboard())
        return
    save_own_profile(update.effective_user.id, form["draft"])
    context.user_data.pop("profile_form", None)
    await message.reply_text("✅ Анкета сохранена!")
    await show_own_profile(update, context)


@dataclass(frozen=True)
class Profile:
    id: str
    name: str
    age: int
    city: str
    photo: Path
    about: str
    interests: list[str]

    @property
    def caption(self) -> str:
        interests = ", ".join(self.interests)
        return (
            f"💌 {self.name}, {self.age}\n"
            f"📍 {self.city}\n\n"
            f"{self.about}\n\n"
            f"Интересы: {interests}"
        )


def load_profiles() -> list[Profile]:
    raw_profiles = json.loads(PROFILES_PATH.read_text(encoding="utf-8"))
    return [
        Profile(
            id=item["id"],
            name=item["name"],
            age=item["age"],
            city=item["city"],
            photo=BASE_DIR / item["photo"],
            about=item["about"],
            interests=item["interests"],
        )
        for item in raw_profiles
    ]


PROFILES = load_profiles()


def profile_keyboard(profile: Profile) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("❤️ Лайк", callback_data=f"like:{profile.id}"),
                InlineKeyboardButton("➡️ Дальше", callback_data="next"),
            ],
            [
                InlineKeyboardButton("👋 Пропустить", callback_data=f"skip:{profile.id}"),
                InlineKeyboardButton("📊 Статистика", callback_data="stats"),
            ],
        ]
    )


def menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("Начать смотреть анкеты", callback_data="next")],
         [InlineKeyboardButton("👤 Моя анкета", callback_data="own")],
         [InlineKeyboardButton("➕ Создать / заполнить заново", callback_data="create")]]
    )


def get_state(context: ContextTypes.DEFAULT_TYPE) -> dict:
    state = context.user_data.setdefault("demo_state", {})
    state.setdefault("index", 0)
    state.setdefault("likes", 0)
    state.setdefault("skips", 0)
    return state


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "Привет! Это быстрый демо-бот знакомств.\n\n"
        "Тут можно листать фейковые анкеты, ставить лайки и показывать статистику "
        "как в маленьком прототипе."
    )
    if update.message:
        context.user_data.pop("profile_form", None)
        await update.message.reply_text(text, reply_markup=menu_keyboard())


async def show_profile(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = get_state(context)
    profile = PROFILES[state["index"] % len(PROFILES)]
    state["index"] += 1

    query = update.callback_query
    chat_id = update.effective_chat.id if update.effective_chat else None
    if query:
        chat_id = query.message.chat_id

    if chat_id is None:
        return

    with profile.photo.open("rb") as image:
        await context.bot.send_photo(
            chat_id=chat_id,
            photo=InputFile(image, filename=profile.photo.name),
            caption=profile.caption,
            reply_markup=profile_keyboard(profile),
        )


async def handle_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return

    await query.answer()
    state = get_state(context)
    action, _, profile_id = query.data.partition(":")

    if action in {"own", "create", "edit"} and update.effective_chat.type != "private":
        await query.message.reply_text("Открой личный чат с ботом для своей анкеты.")
        return
    if action == "cancel":
        await cancel(update, context)
        return
    if action == "menu":
        context.user_data.pop("profile_form", None)
        await query.message.reply_text("Выбери действие:", reply_markup=menu_keyboard())
        return
    if action == "own":
        context.user_data.pop("profile_form", None)
        await show_own_profile(update, context)
        return
    if action == "create":
        context.user_data["profile_form"] = {"mode": "create", "field": "name", "draft": {}}
        await query.message.reply_text(PROMPTS["name"], reply_markup=cancel_keyboard())
        return
    if action == "edit":
        profile = get_own_profile(update.effective_user.id)
        if not profile:
            await show_own_profile(update, context)
            return
        if profile_id not in PROMPTS:
            return
        context.user_data["profile_form"] = {"mode": "edit", "field": profile_id, "draft": profile}
        await query.message.reply_text(PROMPTS[profile_id], reply_markup=cancel_keyboard())
        return

    if action == "like":
        state["likes"] += 1
        profile = next((item for item in PROFILES if item.id == profile_id), None)
        name = profile.name if profile else "анкете"
        await query.message.reply_text(f"Лайк отправлен: {name}. Если это было бы продом, тут создавался бы матч.")
        await show_profile(update, context)
        return

    if action == "skip":
        state["skips"] += 1
        await query.message.reply_text("Ок, пропускаем.")
        await show_profile(update, context)
        return

    if action == "stats":
        await query.message.reply_text(
            f"Статистика демо:\n❤️ лайков: {state['likes']}\n👋 пропусков: {state['skips']}"
        )
        return

    if action == "next":
        await show_profile(update, context)


def main() -> None:
    init_database()
    load_dotenv()
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise RuntimeError("Добавь BOT_TOKEN в .env. Пример есть в .env.example")

    application = Application.builder().token(token).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("profile", show_own_profile))
    application.add_handler(CommandHandler("cancel", cancel))
    application.add_handler(CallbackQueryHandler(handle_action))
    application.add_handler(MessageHandler(filters.ChatType.PRIVATE & ~filters.COMMAND, receive_profile_input))
    asyncio.set_event_loop(asyncio.new_event_loop())
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
