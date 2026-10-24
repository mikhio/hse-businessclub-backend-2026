from datetime import datetime, timezone
from io import BytesIO

import httpx
import pytest
from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.methods import AnswerCallbackQuery, EditMessageText, GetFile, SendMessage, SendPhoto
from aiogram.types import File, Message, Update
from PIL import Image

from app.bot import BackendClient, create_router
from app.qr import decode_qr


class FakeTelegramSession(BaseSession):
    """Real aiogram dispatcher/handlers, fake Telegram HTTP boundary only."""

    def __init__(self):
        super().__init__()
        self.calls = []
        self.image = b""

    async def close(self):
        pass

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        if isinstance(method, GetFile):
            return File(file_id="offline-photo", file_unique_id="offline", file_path="photo.jpg")
        if isinstance(method, AnswerCallbackQuery):
            return True
        if isinstance(method, (SendMessage, SendPhoto, EditMessageText)):
            return Message(
                message_id=100,
                date=datetime.now(timezone.utc),
                chat={"id": int(method.chat_id), "type": "private"},
                text=getattr(method, "text", None),
            )
        raise AssertionError(f"Unexpected Telegram method: {type(method).__name__}")

    async def stream_content(
        self, url, headers=None, timeout=30, chunk_size=65536, raise_for_status=True
    ):
        yield self.image


def message_update(user_id: int, text=None, photo=False, group=False, update_id=1):
    message = {
        "message_id": update_id,
        "date": 1792839600,
        "from": {"id": user_id, "is_bot": False, "first_name": "Тестовый гость"},
        "chat": {"id": -1001 if group else user_id, "type": "group" if group else "private"},
    }
    if text:
        message["text"] = text
        if text.startswith("/"):
            message["entities"] = [{"type": "bot_command", "offset": 0, "length": len(text)}]
    if photo:
        message["photo"] = [
            {"file_id": "offline-photo", "file_unique_id": "offline", "width": 500, "height": 500}
        ]
    return Update.model_validate({"update_id": update_id, "message": message})


def callback_update(user_id: int, data: str, update_id=3):
    return Update.model_validate(
        {
            "update_id": update_id,
            "callback_query": {
                "id": f"callback-{update_id}",
                "chat_instance": "offline-instance",
                "from": {"id": user_id, "is_bot": False, "first_name": "Организатор"},
                "message": {
                    "message_id": 100,
                    "date": 1792839600,
                    "chat": {"id": user_id, "type": "private"},
                    "text": "Карточка гостя",
                },
                "data": data,
            },
        }
    )


@pytest.mark.database
async def test_real_handlers_photo_to_card_to_idempotent_button(app, settings):
    session = FakeTelegramSession()
    bot = Bot("123456:OFFLINE_FAKE_TOKEN_DO_NOT_USE", session=session)
    api = BackendClient(settings, transport=httpx.ASGITransport(app=app))
    dispatcher = Dispatcher(api=api, settings=settings)
    dispatcher.include_router(create_router())
    try:
        await dispatcher.feed_update(bot, message_update(1001, "/start"))
        sent_photo = next(call for call in session.calls if isinstance(call, SendPhoto))
        token = decode_qr(sent_photo.photo.data)
        # Telegram compresses photos to JPEG; use compressed data at the download boundary.
        output = BytesIO()
        Image.open(BytesIO(sent_photo.photo.data)).convert("RGB").save(output, "JPEG", quality=70)
        session.image = output.getvalue()
        await dispatcher.feed_update(bot, message_update(9001, photo=True, update_id=2))
        card = [call for call in session.calls if isinstance(call, SendMessage)][-1]
        assert "Баллы за это мероприятие: 0" in card.text
        callback_data = card.reply_markup.inline_keyboard[0][0].callback_data
        await dispatcher.feed_update(bot, callback_update(9001, callback_data))
        edited = [call for call in session.calls if isinstance(call, EditMessageText)][-1]
        assert "Баллы за это мероприятие: 10" in edited.text
        assert "Приход уже отмечен" in edited.text
        await dispatcher.feed_update(bot, callback_update(9002, callback_data, update_id=4))
        answer = [call for call in session.calls if isinstance(call, AnswerCallbackQuery)][-1]
        assert answer.text == "Приход уже был отмечен"
        card_after = await api.scan(9001, token)
        assert len(card_after["history"]) == 1
        assert card_after["points"] == 10
        # A non-organizer cannot exploit a copied callback button.
        await dispatcher.feed_update(bot, callback_update(1001, callback_data, update_id=5))
        answer = [call for call in session.calls if isinstance(call, AnswerCallbackQuery)][-1]
        assert answer.show_alert is True
        assert answer.text == "Доступ только организаторам"
    finally:
        await api.close()
        await bot.session.close()


async def test_group_messages_are_ignored(settings):
    session = FakeTelegramSession()
    bot = Bot("123456:OFFLINE_FAKE_TOKEN_DO_NOT_USE", session=session)
    dispatcher = Dispatcher(settings=settings)
    dispatcher.include_router(create_router())
    await dispatcher.feed_update(bot, message_update(1001, "/start", group=True))
    assert session.calls == []
    await bot.session.close()
