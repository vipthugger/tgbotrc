import asyncio
from aiogram import Bot, Dispatcher, types
from aiogram.enums import ParseMode
from aiogram.types import ChatPermissions
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import Command
from aiogram.exceptions import TelegramBadRequest
from datetime import datetime, timedelta
import re
import os
from dotenv import load_dotenv
import logging

# Налаштування логування
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# Load environment variables
load_dotenv()

# Get token from environment variable
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "YOUR_BOT_TOKEN")
bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

resale_topic_id = None  # ID гілки для оголошень
processed_media_groups = {}  # Словник для зберігання повідомлень медіагруп
user_warnings = {}  # Словник для зберігання часу останнього предупреждения для каждого пользователя

# Регулярні вирази для визначення ціни
price_pattern = re.compile(r"(\d+[\.,]?\d*)\s?(грн|k|к|kг|тис|₴|[₴])?", re.IGNORECASE)

async def cleanup_old_warnings():
    """Очистка старых записей о предупреждениях"""
    while True:
        current_time = datetime.now()
        users_to_remove = []
        for user_id, warning_time in user_warnings.items():
            if (current_time - warning_time) > timedelta(minutes=5):
                users_to_remove.append(user_id)
        for user_id in users_to_remove:
            user_warnings.pop(user_id, None)
        await asyncio.sleep(300)  # Проверка каждые 5 минут

async def can_send_warning(user_id: int, force: bool = False) -> bool:
    """Проверяет, можно ли отправить предупреждение пользователю"""
    if force:
        logging.info(f"Принудительная отправка предупреждения для user_id={user_id}")
        return True

    current_time = datetime.now()
    if user_id in user_warnings:
        last_warning_time = user_warnings[user_id]
        time_since_last = (current_time - last_warning_time).total_seconds()
        logging.info(f"Проверка интервала предупреждений для user_id={user_id}: прошло {time_since_last} секунд")
        if time_since_last < 30:
            logging.info(f"Пропуск предупреждения: слишком рано (нужно подождать еще {30 - time_since_last:.1f} сек)")
            return False
    user_warnings[user_id] = current_time
    logging.info(f"Разрешена отправка предупреждения для user_id={user_id}")
    return True

async def send_warning_message(chat_id: int, thread_id: int | None, text: str, delete_after: int = 5, force: bool = False, user_id: int = None) -> None:
    """Send a warning message to the chat and delete it after specified number of seconds"""
    try:
        if user_id and not await can_send_warning(user_id, force):
            logging.info(f"Пропуск предупреждения для user_id={user_id} (слишком частые нарушения)")
            return

        logging.info(f"Подготовка к отправке предупреждения:")
        logging.info(f"- chat_id: {chat_id}")
        logging.info(f"- thread_id: {thread_id}")
        logging.info(f"- user_id: {user_id}")
        logging.info(f"- force: {force}")
        logging.info(f"- текст: {text}")

        bot_message = await bot.send_message(chat_id=chat_id, text=text, message_thread_id=thread_id)
        logging.info(f"Предупреждение успешно отправлено (message_id={bot_message.message_id})")

        await asyncio.sleep(delete_after)
        await bot_message.delete()
        logging.info(f"Предупреждение удалено через {delete_after} секунд")
    except TelegramBadRequest as e:
        logging.error(f"Ошибка при отправке/удалении предупреждения: {e}")
        logging.error(f"Параметры: chat_id={chat_id}, thread_id={thread_id}, user_id={user_id}, force={force}")

async def process_media_group_message(message: types.Message, reason: str) -> None:
    """Process all messages in a media group"""
    if not message.media_group_id:
        try:
            chat_id = message.chat.id
            thread_id = message.message_thread_id
            user_id = message.from_user.id
            await message.delete()
            await send_warning_message(chat_id, thread_id, reason, user_id=user_id)
            logging.info(f"Обработано одиночное сообщение от @{message.from_user.username}")
        except TelegramBadRequest as e:
            logging.error(f"Ошибка при обработке одиночного сообщения: {e}")
        return

    media_group = processed_media_groups.get(message.media_group_id, {"messages": [], "processed": False})
    media_group["messages"].append(message)
    media_group["reason"] = reason
    processed_media_groups[message.media_group_id] = media_group
    logging.info(f"Добавлено сообщение в медиагруппу {message.media_group_id} от @{message.from_user.username}. Всего в группе: {len(media_group['messages'])}")

    # Почекаємо трохи, щоб зібрати всі повідомлення медіагрупи
    await asyncio.sleep(1)

    if not media_group["processed"]:
        media_group["processed"] = True
        logging.info(f"Начало обработки медиагруппы {message.media_group_id}")

        # Видаляємо всі повідомлення групи
        chat_id = message.chat.id
        thread_id = message.message_thread_id
        user_id = message.from_user.id
        for msg in media_group["messages"]:
            try:
                await msg.delete()
                logging.info(f"Удалено сообщение {msg.message_id} из медиагруппы {message.media_group_id}")
            except TelegramBadRequest as e:
                logging.error(f"Ошибка при удалении сообщения из медиагруппы: {e}")

        # Надсилаємо одне повідомлення про порушення
        await send_warning_message(chat_id, thread_id, reason, user_id=user_id)

        # Очищаємо дані медіагрупи
        processed_media_groups.pop(message.media_group_id, None)
        logging.info(f"Медиагруппа {message.media_group_id} полностью обработана")

@dp.message(Command(commands=["notification"]))
async def send_notification(message: types.Message):
    """Send rules notification (admin only)"""
    # Проверяем, является ли отправитель администратором
    if message.from_user.id in [admin.user.id for admin in await message.chat.get_administrators()]:
        try:
            # Удаляем команду администратора
            await message.delete()
            logging.info(f"Команда /notification видалена")

            # Отправляем правила в чат
            await bot.send_message(
                chat_id=message.chat.id,
                text=RULES_TEXT,
                message_thread_id=message.message_thread_id,
                disable_notification=False
            )
            logging.info(f"Відправлено правила за командою адміністратора")
        except TelegramBadRequest as e:
            logging.error(f"Помилка при видаленні команди або відправці правил: {e}")
    else:
        try:
            # Если отправитель не администратор, удаляем сообщение
            await message.delete()
            logging.info(f"Видалено спробу використання команди /notification користувачем без прав")
        except TelegramBadRequest as e:
            logging.error(f"Помилка при видаленні команди від користувача: {e}")
        await send_warning_message(message.chat.id, message.message_thread_id, "❌ Ця команда тільки для адміністраторів.")

@dp.message(Command(commands=["resale_topic"]))
async def set_resale_topic(message: types.Message):
    """Set the topic for resale messages (admin only)"""
    global resale_topic_id
    if message.from_user.id in [admin.user.id for admin in await message.chat.get_administrators()]:
        resale_topic_id = message.message_thread_id
        try:
            await message.delete()
            logging.info(f"Команда /resale_topic видалена")
        except TelegramBadRequest as e:
            logging.error(f"Помилка при видаленні команди: {e}")
        
        # Отправляем сообщение о активации бота
        await send_warning_message(message.chat.id, message.message_thread_id, "✅ Бот тепер контролює цю гілку на відповідність правилам.")
        
        # Сразу отправляем правила в эту тему
        try:
            await bot.send_message(
                chat_id=message.chat.id,
                text=RULES_TEXT,
                message_thread_id=message.message_thread_id,
                disable_notification=True
            )
            logging.info(f"Відправлено початкове нагадування про правила у гілку resale_topic")
        except Exception as e:
            logging.error(f"Помилка при відправці початкового нагадування: {e}")
    else:
        try:
            await message.delete()
        except TelegramBadRequest as e:
            logging.error(f"Помилка при видаленні команди: {e}")
        await send_warning_message(message.chat.id, message.message_thread_id, "❌ Ця команда тільки для адміністраторів.")

@dp.message(lambda message: resale_topic_id and message.message_thread_id == resale_topic_id)
async def delete_wrong_messages(message: types.Message):
    """Handle messages in the resale topic"""
    if message.from_user.id in [admin.user.id for admin in await message.chat.get_administrators()]:
        return  # Адмінам можна все

    # Проверяем сначала наличие текста или подписи к фото
    message_text = message.text or message.caption
    if not message_text:
        # Если нет ни текста, ни подписи к фото
        await process_media_group_message(
            message,
            f"❌ @{message.from_user.username}, ваше повідомлення було видалено. Необхідно додати текст з хештегом #куплю або #продам."
        )
        return

    # Проверка хештегов
    if not any(word in message_text.lower() for word in ["#куплю", "#продам"]):
        await process_media_group_message(
            message,
            f"❌ @{message.from_user.username}, ваше повідомлення було видалено, оскільки воно не містить хештегів '#куплю' або '#продам'."
        )
        return

    # Проверка запрещенных типов медиа
    if any([
        message.sticker,
        message.voice,
        message.video_note,
        message.animation
    ]):
        await process_media_group_message(
            message,
            f"❌ @{message.from_user.username}, ваше повідомлення було видалено. В гілці оголошень дозволені тільки текстові повідомлення з фото та хештегами #куплю або #продам."
        )
        return

    # Перевірка ціни (ціна повинна бути >= 3000 грн) тільки для объявлений #продам
    is_selling = "#продам" in message_text.lower()  # Проверяем, это объявление о продаже
    
    logging.info(f"Перевірка ціни для повідомлення от @{message.from_user.username}, тип: {'#продам' if is_selling else '#куплю'}")
    
    if is_selling:  # Проверяем цену только для объявлений #продам
        # Ищем цену вблизи слова "цена" или "ціна"
        price_pattern_with_label = re.compile(r"(?:цена|ціна|price|вартість)\s*:?\s*(\d+[\.,]?\d*)\s*(?:грн|k|к|kг|тис|₴|[₴])?", re.IGNORECASE)
        price_match_with_label = price_pattern_with_label.search(message_text)
        
        # Если не нашли по слову "цена", ищем по обычному паттерну
        if price_match_with_label:
            price_match = price_match_with_label
            logging.info(f"Знайдено ціну по ключовому слову")
        else:
            price_match = price_pattern.search(message_text)
            logging.info(f"Цiна по ключовому слову не знайдена, використовуємо звичайний пошук")
        
        if price_match:
            price_value = price_match.group(1)
            logging.info(f"Знайдено ціну в повідомленні: {price_value}")
            if price_value:
                price_value = price_value.replace(",", ".").lower()
                price_value = float(price_value) if price_value.isdigit() or price_value.replace('.', '', 1).isdigit() else None
                
                # Проверяем, указана ли цена в "k" или "к" (тысячах)
                price_unit = price_match.group(2) if len(price_match.groups()) > 1 else None
                if price_unit and price_unit.lower() in ['k', 'к', 'kг']:
                    price_value = price_value * 1000
                    
                logging.info(f"Оброблене значення ціни: {price_value}")
                deletion_message = ""
                if price_value and price_value < 3000:  # Оставляем < чтобы цена 3000 грн была допустима
                    deletion_message = f"❌ @{message.from_user.username}, ваше повідомлення було видалено, оскільки ціна оголошення менше 3000 грн."
                    logging.info(f"Видалено повідомлення з низькою ціною від @{message.from_user.username} (ціна: {price_value} грн)")
                    try:
                        chat_id = message.chat.id
                        thread_id = message.message_thread_id
                        user_id = message.from_user.id
                        logging.info(f"Підготовка до видалення повідомлення: chat_id={chat_id}, thread_id={thread_id}, user_id={user_id}")
                        logging.info(f"Текст предупреждения: {deletion_message}")

                        await message.delete()
                        logging.info("Повідомлення успішно видалено")

                        await send_warning_message(chat_id, thread_id, deletion_message, force=True, user_id=user_id)
                        logging.info("Відправлено предупреждение о низкой цене")
                    except TelegramBadRequest as e:
                        logging.error(f"Помилка при видаленні повідомлення з низькою ціною: {e}")
                return

@dp.message(lambda message: message.new_chat_members is not None)
async def welcome_new_member(message: types.Message):
    """Welcome new chat members"""
    for new_member in message.new_chat_members:
        username = f"@{new_member.username}" if new_member.username else "новий учасник"
        await send_warning_message(
            message.chat.id,
            message.message_thread_id,
            f"🤗 Вітаємо, {username}! Ознайомтеся з правилами, щоб уникнути непорозумінь та комфортно спілкуватися у чаті.",
            delete_after=45,
            user_id=new_member.id
        )

RULES_TEXT = """Правила гілки ПРОДАЖ / КУПІВЛЯ 📌

1. У цій гілці дозволено лише оголошення з хештегами #куплю або #продам.
2. Для оголошень з хештегом #продам обов'язково вказувати ціну. Мінімальна сума – 3000 грн.
3. Для оголошень з хештегом #куплю вказувати бюджет/ціну не обов'язково.
4. Оголошення, що не відповідають тематиці або не містять необхідної інформації, будуть видалятися.
5. Адміністрація залишає за собою право видаляти повідомлення без попередження.

⚠️ Порушення правил може призвести до видалення повідомлень або блокування користувача. Дотримання цих правил допоможе підтримувати порядок у чаті.""" # Актуальний текст правил

async def send_rules_reminder():
    """Відправка нагадувань про правила кожні 60 хвилин тільки в гілку resale_topic"""
    logging.info("Запущено завдання відправки нагадувань про правила")
    while True:
        try:
            # Очікуємо 60 хвилин перед відправкою нагадування
            await asyncio.sleep(3600)  # 60 хвилин = 3600 секунд

            # Перевіряємо, чи встановлена гілка resale_topic
            if resale_topic_id is None:
                logging.info("Гілка resale_topic не встановлена. Пропуск відправки нагадувань")
                continue

            # Якщо гілка встановлена, знаходимо її чат (глобальна змінна resale_topic_id 
            # містить ID гілки, але нам також потрібен chat_id)
            # Використовуємо останнє відоме значення chat_id з останнього повідомлення в цій гілці
            updates = await bot.get_updates(limit=100, timeout=1)
            chat_id = None

            for update in updates:
                if (update.message and 
                    update.message.message_thread_id == resale_topic_id):
                    chat_id = update.message.chat.id
                    break

            if not chat_id:
                logging.warning("Не вдалося визначити chat_id для гілки resale_topic")
                continue

            logging.info(f"Підготовка до відправки нагадування про правила у гілку resale_topic (chat_id={chat_id}, thread_id={resale_topic_id})")

            try:
                # Відправляємо нагадування в гілку resale_topic
                await bot.send_message(
                    chat_id=chat_id,
                    text=RULES_TEXT,
                    message_thread_id=resale_topic_id,
                    disable_notification=True  # Щоб не турбувати користувачів
                )
                logging.info(f"Відправлено нагадування про правила у гілку resale_topic")
            except Exception as e:
                logging.error(f"Помилка при відправці нагадування у гілку resale_topic: {e}")

            logging.info("Цикл відправки нагадувань про правила завершено")
        except Exception as e:
            logging.error(f"Помилка у завданні відправки нагадувань: {e}")
            await asyncio.sleep(60)  # Чекаємо хвилину перед повторною спробою


async def main():
    # Start cleanup task
    asyncio.create_task(cleanup_old_warnings())
    asyncio.create_task(send_rules_reminder()) #added task for reminder
    # Initialize Bot instance with a default parse mode
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
