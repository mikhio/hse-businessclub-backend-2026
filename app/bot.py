import asyncio
import logging
from io import BytesIO

import httpx
from aiogram import Bot, Dispatcher, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    ErrorEvent,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from app.auth import actor_headers
from app.config import Settings
from app.qr import MAX_IMAGE_BYTES


class BackendError(Exception):
    pass


class BackendClient:
    def __init__(self, settings: Settings, transport=None):
        self.master_key = settings.api_key
        self.client = httpx.AsyncClient(
            base_url=settings.backend_url,
            timeout=20,
            transport=transport,
        )

    async def close(self):
        await self.client.aclose()

    async def request(self, method: str, path: str, actor_id: int, **kwargs):
        try:
            response = await self.client.request(
                method, path, headers=actor_headers(self.master_key, actor_id), **kwargs
            )
        except httpx.HTTPError as exc:
            raise BackendError("Сервис временно недоступен. Попробуйте ещё раз") from exc
        if response.is_error:
            detail = response.json().get("detail", "Не удалось выполнить запрос")
            if not isinstance(detail, str) or response.status_code >= 500:
                detail = "Не удалось выполнить запрос. Попробуйте ещё раз"
            raise BackendError(detail)
        return response

    async def register(self, telegram_id: int, name: str):
        await self.request(
            "POST",
            "/v1/guests/register",
            telegram_id,
            json={"telegram_id": telegram_id, "name": name},
        )

    async def qr(self, telegram_id: int):
        return (await self.request("GET", f"/v1/guests/{telegram_id}/qr", telegram_id)).content

    async def scan(self, organizer_id: int, token: str):
        return (
            await self.request(
                "POST",
                "/v1/organizer/scan",
                organizer_id,
                json={"organizer_id": organizer_id, "token": token},
            )
        ).json()

    async def scan_photo(self, organizer_id: int, image: bytes):
        return (
            await self.request(
                "POST",
                "/v1/organizer/scan-photo",
                organizer_id,
                data={"organizer_id": str(organizer_id)},
                files={"image": ("pass.jpg", image, "image/jpeg")},
            )
        ).json()

    async def check_in(self, organizer_id: int, guest_id: str):
        return (
            await self.request(
                "POST",
                f"/v1/organizer/guests/{guest_id}/check-in",
                organizer_id,
                json={"organizer_id": organizer_id},
            )
        ).json()


def card_text(card: dict) -> str:
    attendance = "Приход уже отмечен" if card["checked_in"] else "Приход ещё не отмечен"
    return (
        f"Гость: {card['name']}\nКатегория: {card['category']}\n"
        f"Баллы за это мероприятие: {card['points']}\n{attendance}"
    )


def card_keyboard(card: dict, points: int) -> InlineKeyboardMarkup | None:
    if card["checked_in"]:
        return None
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"Отметить приход · +{points}", callback_data=f"in:{card['id']}"
                )
            ]
        ]
    )


def create_router() -> Router:
    router = Router()
    router.message.filter(F.chat.type == "private")
    router.callback_query.filter(F.message.chat.type == "private")

    @router.message(Command("start", "qr"))
    async def guest_qr(message: Message, api: BackendClient, settings: Settings):
        await api.register(message.from_user.id, message.from_user.full_name[:120])
        image = await api.qr(message.from_user.id)
        caption = "Ваш персональный пропуск. Покажите QR организатору на входе."
        if message.from_user.id in settings.organizers:
            caption += "\nВы организатор: пришлите фото чужого QR или текст кода для проверки."
        await message.answer_photo(
            BufferedInputFile(image, filename="guest-pass.png"), caption=caption
        )

    @router.message(Command("help"))
    async def help_message(message: Message):
        await message.answer(
            "/qr — получить персональный QR.\n"
            "Организатору: пришлите фото QR или текст кода, затем нажмите кнопку прихода.\n"
            "Работа с пропусками доступна только в личном чате с ботом."
        )

    async def show_card(message: Message, card: dict, settings: Settings):
        await message.answer(
            card_text(card), reply_markup=card_keyboard(card, settings.checkin_points)
        )

    @router.message(F.photo | F.document)
    async def photo_scan(message: Message, bot: Bot, api: BackendClient, settings: Settings):
        if message.from_user.id not in settings.organizers:
            await message.answer("Проверять пропуска могут только организаторы. Ваш пропуск: /qr")
            return
        media = message.photo[-1] if message.photo else message.document
        if message.document and not (message.document.mime_type or "").startswith("image/"):
            await message.answer("Пришлите фотографию QR или изображение файлом")
            return
        if media.file_size and media.file_size > MAX_IMAGE_BYTES:
            await message.answer("Пришлите фото размером до 8 МБ")
            return
        output = BytesIO()
        await bot.download(media, destination=output)
        card = await api.scan_photo(message.from_user.id, output.getvalue())
        await show_card(message, card, settings)

    @router.message(F.text)
    async def text_scan(message: Message, api: BackendClient, settings: Settings):
        if message.from_user.id not in settings.organizers:
            await message.answer("Ваш пропуск: /qr. Для справки: /help")
            return
        card = await api.scan(message.from_user.id, message.text.strip())
        await show_card(message, card, settings)

    @router.callback_query(F.data.startswith("in:"))
    async def mark_arrival(callback: CallbackQuery, api: BackendClient, settings: Settings):
        if callback.from_user.id not in settings.organizers:
            await callback.answer("Доступ только организаторам", show_alert=True)
            return
        result = await api.check_in(callback.from_user.id, callback.data.removeprefix("in:"))
        await callback.answer("Приход отмечен" if result["awarded"] else "Приход уже был отмечен")
        try:
            await callback.message.edit_text(card_text(result["guest"]), reply_markup=None)
        except TelegramBadRequest as exc:
            if "message is not modified" not in str(exc):
                raise

    @router.error()
    async def on_error(event: ErrorEvent):
        if isinstance(event.exception, BackendError):
            text = str(event.exception)
        else:
            # Do not log Telegram URLs, bot tokens or QR bearer tokens.
            logging.error("Bot request failed: %s", type(event.exception).__name__)
            text = "Не удалось обработать запрос. Попробуйте ещё раз или /help"
        if event.update.callback_query:
            await event.update.callback_query.answer(text, show_alert=True)
        elif event.update.message:
            await event.update.message.answer(text)
        return True

    return router


async def main():
    settings = Settings()
    if not settings.bot_token:
        raise SystemExit("Set BOT_TOKEN in .env to run the Telegram bot; API works without it")
    api = BackendClient(settings)
    dispatcher = Dispatcher(api=api, settings=settings)
    dispatcher.include_router(create_router())
    bot = Bot(settings.bot_token)
    try:
        await dispatcher.start_polling(bot, allowed_updates=["message", "callback_query"])
    finally:
        await api.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
