
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
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

# Налаштування логування
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

# Get token from environment variable
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "YOUR_BOT_TOKEN")
bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

# Регулярні вирази для визначення ціни
price_pattern = re.compile(r"(ціна:|price:|цена:).*?(\d+[\.,]?\d*)\s*(грн|k|к|kг|тис|₴|[₴])?", re.IGNORECASE)
price_fallback_pattern = re.compile(r"(\d+[\.,]?\d*)\s*(грн|k|к|kг|тис|₴|[₴])?", re.IGNORECASE)

# Global variables
processed_media_groups = {}
user_warnings = {}
resale_topic_id = None
report_chat_id = None
processed_message_groups = set()  # Track processed message groups to avoid duplicate warnings
reported_messages = set()  # Track reported messages to avoid duplicates

async def extract_price(text: str) -> float | None:
    """Extract price from text"""
    try:
        # Сначала ищем цену после ключевых слов
        matches = price_pattern.search(text.lower())
        if matches:
            price_str = matches.group(2).replace(',', '.')
            try:
                price = float(price_str)
                if matches.group(3) and matches.group(3).lower() in ['k', 'к', 'тис']:
                    price *= 1000
                logger.info(f"Извлечена цена после ключевого слова: {price} грн")
                return price
            except ValueError:
                pass

        # Если не нашли цену после ключевых слов, ищем просто числа
        matches = price_fallback_pattern.finditer(text.lower())
        max_price = 0

        for match in matches:
            price_str = match.group(1).replace(',', '.')
            try:
                price = float(price_str)
                if match.group(2) and match.group(2).lower() in ['k', 'к', 'тис']:
                    price *= 1000
                max_price = max(max_price, price)
            except ValueError:
                continue

        logger.info(f"Извлечена максимальная цена: {max_price} грн")
        return max_price if max_price > 0 else None
    except Exception as e:
        logger.error(f"Ошибка при извлечении цены: {e}")
        return None

async def can_manage_messages(chat_id: int) -> bool:
    """Check if bot has permission to delete messages"""
    try:
        chat_member = await bot.get_chat_member(chat_id, bot.id)
        # Проверяем правильные атрибуты в объекте chat_member
        is_admin = getattr(chat_member, "status", None) in ["administrator", "creator"]
        can_delete = getattr(chat_member, "can_delete_messages", False)
        
        logger.info(f"Bot permissions in chat {chat_id}: is_admin={is_admin}, can_delete={can_delete}")
        return is_admin and can_delete
    except Exception as e:
        logger.error(f"Error checking bot permissions: {e}")
        return False

async def delete_message_safe(message: types.Message) -> bool:
    """Safely delete a message with permission check"""
    try:
        if not await can_manage_messages(message.chat.id):
            logger.warning(f"Bot doesn't have permission to delete messages in chat {message.chat.id}")
            return False
        await message.delete()
        return True
    except Exception as e:
        logger.error(f"Error deleting message: {e}")
        return False

# Command handler specifically defined BEFORE the general message handler
@dp.message(Command("resale_topic"))
async def set_resale_topic(message: types.Message):
    """Set the topic for resale messages (admin only)"""
    global resale_topic_id
    logger.info(f"Received /resale_topic command from @{message.from_user.username}")
    
    try:
        # Get admin list
        chat_admins = await message.chat.get_administrators()
        admin_ids = [admin.user.id for admin in chat_admins]
        
        if message.from_user.id in admin_ids:
            logger.info(f"Admin {message.from_user.username} authorized to set resale topic")
            resale_topic_id = message.message_thread_id
            
            try:
                await message.delete()
                logger.info(f"Command /resale_topic deleted")
            except Exception as e:
                logger.error(f"Failed to delete command message: {e}")
            
            # Send a confirmation message
            await bot.send_message(
                chat_id=message.chat.id,
                text="✅ Бот тепер контролює цю гілку на відповідність правилам.",
                message_thread_id=message.message_thread_id
            )
            
            # Send rules to this topic
            await bot.send_message(
                chat_id=message.chat.id,
                text=RULES_TEXT,
                message_thread_id=message.message_thread_id,
                disable_notification=False
            )
            logger.info(f"Rules sent by admin command: {message.text}")
        else:
            logger.info(f"Non-admin {message.from_user.username} tried to use admin command")
            try:
                await message.delete()
            except Exception as e:
                logger.error(f"Failed to delete unauthorized command: {e}")
            await message.answer("❌ Ця команда тільки для адміністраторів.")
    except Exception as e:
        logger.error(f"Error in set_resale_topic: {e}", exc_info=True)

@dp.message(Command("set_report_chat"))
async def set_report_chat(message: types.Message):
    """Set the chat for receiving reports (admin only)"""
    global report_chat_id
    logger.info(f"Received /set_report_chat command from @{message.from_user.username}")
    
    try:
        # Get admin list
        chat_admins = await message.chat.get_administrators()
        admin_ids = [admin.user.id for admin in chat_admins]
        
        if message.from_user.id in admin_ids:
            report_chat_id = message.chat.id
            logger.info(f"Report chat set to {message.chat.id}")
            await message.reply("✅ Цей чат встановлено для отримання скарг.")
        else:
            logger.info(f"Non-admin {message.from_user.username} tried to set report chat")
            await message.reply("❌ Ця команда тільки для адміністраторів.")
    except Exception as e:
        logger.error(f"Error in set_report_chat: {e}")
        await message.reply("❌ Помилка при встановленні чату для скарг.")

@dp.message(Command("report"))
async def handle_report(message: types.Message):
    """Handle report command"""
    try:
        # Check if message is a reply
        if not message.reply_to_message:
            await message.reply("❌ Ви повинні відповісти на повідомлення, щоб залишити скаргу.")
            return

        # Check if report chat is set
        if not report_chat_id:
            await message.reply("❌ Адміністратори ще не налаштували чат для скарг.")
            return

        # Get report reason
        reason = message.text.replace("/report", "").strip()
        if not reason:
            reason = "Причина не вказана"

        # Check if message was already reported
        msg_id = f"{message.chat.id}_{message.reply_to_message.message_id}"
        if msg_id in reported_messages:
            await message.reply("❌ Це повідомлення вже було відправлено адміністраторам.")
            return

        # Mark message as reported
        reported_messages.add(msg_id)

        # Get message link if possible
        message_link = ""
        try:
            message_link = f"https://t.me/c/{str(message.chat.id)[4:]}/{message.reply_to_message.message_id}"
        except Exception as e:
            logger.error(f"Error getting message link: {e}")

        # Prepare report message
        report_text = (
            f"🔔 Нова скарга!\n"
            f"📌 Відправник: @{message.from_user.username or 'Anonymous'}\n"
            f"📢 Причина: {reason}\n"
            f"🔗 Посилання на повідомлення: {message_link}"
        )

        # Send report to admin chat
        report_msg = await bot.send_message(
            chat_id=report_chat_id,
            text=report_text,
            reply_to_message_id=None
        )
        
        # Forward reported message
        forwarded = await message.reply_to_message.forward(report_chat_id)

        await message.reply("✅ Скаргу надіслано адміністраторам.")
        logger.info(f"Report sent from @{message.from_user.username}")

    except Exception as e:
        logger.error(f"Error handling report: {e}")
        await message.reply("❌ Помилка при обробці скарги.")

@dp.message(Command("notification"))
async def send_notification(message: types.Message):
    """Send rules notification (admin only)"""
    try:
        logger.info(f"Command handler triggered for: {message.text}")
        # Get admin list
        chat_admins = await message.chat.get_administrators()
        admin_ids = [admin.user.id for admin in chat_admins]
        
        if message.from_user.id in admin_ids:
            logger.info(f"Admin {message.from_user.username} authorized to send rules")
            try:
                await message.delete()
            except Exception as e:
                logger.error(f"Failed to delete command message: {e}")
            
            await bot.send_message(
                chat_id=message.chat.id,
                text=RULES_TEXT,
                message_thread_id=getattr(message, "message_thread_id", None),
                disable_notification=False
            )
            logger.info(f"Rules sent by admin command: {message.text}")
        else:
            logger.info(f"Non-admin {message.from_user.username} tried to use admin command")
            try:
                await message.delete()
            except Exception as e:
                logger.error(f"Failed to delete unauthorized command: {e}")
            await message.answer("❌ Ця команда тільки для адміністраторів.")
    except Exception as e:
        logger.error(f"Error in send_notification: {e}", exc_info=True)

# General message handler AFTER the command handlers
@dp.message()
async def handle_messages(message: types.Message):
    """Handle all messages"""
    try:
        # Get message text
        message_text = message.text or message.caption or ""
        username = message.from_user.username or f"user_{message.from_user.id}"
        
        # Detect message type for logging
        if message.sticker:
            content_type = "sticker"
        elif message.animation:
            content_type = "GIF"
        elif message.video:
            content_type = "video"
        elif message.photo:
            content_type = "photo"
        else:
            content_type = "text"
            
        logger.info(f"Processing {content_type} from @{username}: {message_text[:100]}...")

        # Skip command messages completely - critical fix
        if message_text.startswith('/'):
            logger.info(f"Skipping command message: {message_text}")
            return
            
        try:
            # Check if user is admin more safely
            chat_admins = await message.chat.get_administrators()
            admin_ids = [admin.user.id for admin in chat_admins]
            is_admin = message.from_user.id in admin_ids

            if is_admin:
                logger.info(f"Skipping admin message from @{username}")
                return

        except Exception as e:
            logger.error(f"Error checking admin status: {e}")
            # If we can't check admin status, process message anyway
            is_admin = False
        
        # Handle resale topic messages if applicable
        if resale_topic_id and message.message_thread_id == resale_topic_id:
            # Helper function to send warning and delete it after 5 seconds
            async def send_warning_and_delete(chat_id, text, thread_id=None):
                warning_msg = await bot.send_message(
                    chat_id=chat_id,
                    text=text,
                    message_thread_id=thread_id
                )
                # Schedule deletion after 5 seconds
                await asyncio.sleep(5)
                try:
                    await bot.delete_message(chat_id=chat_id, message_id=warning_msg.message_id)
                    logger.info(f"Warning message deleted after 5 seconds")
                except Exception as e:
                    logger.error(f"Failed to delete warning message: {e}")
            
            # For media groups, collect all messages before processing
            media_group_id = message.media_group_id
            
            if media_group_id:
                # Store message in group and wait for others
                if media_group_id not in processed_media_groups:
                    processed_media_groups[media_group_id] = {
                        'messages': [],
                        'text': '',
                        'timer': None
                    }
                
                group = processed_media_groups[media_group_id]
                group['messages'].append(message)
                if message_text:
                    group['text'] = message_text
                
                # Cancel existing timer if any
                if group['timer']:
                    group['timer'].cancel()
                
                # Set new timer to process group
                async def process_media_group():
                    await asyncio.sleep(1)  # Wait for all messages
                    group = processed_media_groups.get(media_group_id)
                    if not group:
                        return
                        
                    # Check group rules
                    text = group['text']
                    warning_message = None
                    
                    if not text:
                        warning_message = f"❌ @{username}, ваше повідомлення було видалено, оскільки воно не містить опису."
                    elif "#продам" in text.lower():
                        price = await extract_price(text)
                        if price is None or price < 3000:
                            warning_message = f"❌ @{username}, ваше повідомлення було видалено, оскільки мінімальна ціна оголошення — 3000 грн."
                    elif not any(tag in text.lower() for tag in ["#куплю", "#продам"]):
                        warning_message = f"❌ @{username}, ваше повідомлення було видалено, оскільки воно не містить хештегів '#куплю' або '#продам'."
                    
                    if warning_message:
                        logger.info(f"Media group violation: {warning_message}")
                        # Delete all messages in group
                        for msg in group['messages']:
                            await delete_message_safe(msg)
                        await send_warning_and_delete(
                            message.chat.id,
                            warning_message,
                            message.message_thread_id
                        )
                    
                    # Cleanup group
                    processed_media_groups.pop(media_group_id, None)
                
                group['timer'] = asyncio.create_task(process_media_group())
                return
                
            # Handle single photo
            elif message.photo and not message_text:
                logger.info(f"Single photo without text")
                warning_message = f"❌ @{username}, ваше повідомлення було видалено, оскільки воно не містить опису."
                if await delete_message_safe(message):
                    await send_warning_and_delete(
                        message.chat.id,
                        warning_message,
                        message.message_thread_id
                    )
                return
                
            # Determine the reason for deletion (if any) with priority for price issues
            warning_message = None
            
            # First priority: Check for price in #продам messages
            if message_text and "#продам" in message_text.lower():
                price = await extract_price(message_text)
                logger.info(f"Extracted price for selling post: {price}")
                
                if price is None or price < 3000:
                    logger.info(f"Price too low or not found: {price}")
                    warning_message = f"❌ @{username}, ваше повідомлення було видалено, оскільки мінімальна ціна оголошення — 3000 грн."
            
            # Second priority: Check for missing hashtags
            elif message_text and not any(word in message_text.lower() for word in ["#куплю", "#продам"]):
                logger.info(f"Message in resale topic without required hashtags")
                warning_message = f"❌ @{username}, ваше повідомлення було видалено, оскільки воно не містить хештегів '#куплю' або '#продам'."
            
            # Third priority: Check for media content without text
            elif (message.sticker or 
                (message.animation and not message_text) or 
                (message.video and not message_text) or 
                (message.photo and not message_text)):
                logger.info(f"{content_type} without valid text in resale topic")
                warning_message = f"❌ @{username}, ваше повідомлення було видалено, оскільки воно не відповідає правилам."
            
            # If we have a reason to delete, delete all messages in the media group
            if warning_message:
                if media_group_id:
                    # Store message in media group tracking dict if not exists
                    if media_group_id not in processed_media_groups:
                        processed_media_groups[media_group_id] = []
                    processed_media_groups[media_group_id].append(message)
                    
                    # If this is the first message, schedule deletion
                    if is_first_in_group:
                        async def delete_media_group():
                            # Wait a bit for all media group messages to arrive
                            await asyncio.sleep(2)
                            # Delete all messages in the group
                            for msg in processed_media_groups.get(media_group_id, []):
                                await delete_message_safe(msg)
                            # Clean up the group
                            processed_media_groups.pop(media_group_id, None)
                        
                        asyncio.create_task(delete_media_group())
                else:
                    await delete_message_safe(message)
                    
                await send_warning_and_delete(
                    message.chat.id,
                    warning_message,
                    message.message_thread_id
                )
                return
            
            logger.info("Message in resale topic passed all checks")
            return
        
        # Process regular messages (non-resale topic)
        # For stickers, GIFs, videos and photos without text, we don't need to process them
        if message.sticker or message.animation or message.video or (message.photo and not message_text):
            logger.info(f"Ignoring {content_type} in regular chat")
            return
            
        # Skip empty text messages
        if not message_text:
            logger.info("Ignoring message without text in regular chat")
            return
            
        # Проверяем длину сообщения (не более 500 символов)
        if len(message_text) > 500:
            logger.info(f"Message too long: {len(message_text)} characters")
            if await delete_message_safe(message):
                warning_msg = await message.answer(
                    f"❌ @{username}, ваше повідомлення було видалено, оскільки воно не відповідає правилам."
                )
                # Schedule deletion after 5 seconds
                await asyncio.sleep(5)
                try:
                    await bot.delete_message(chat_id=message.chat.id, message_id=warning_msg.message_id)
                    logger.info(f"Warning message deleted after 5 seconds")
                except Exception as e:
                    logger.error(f"Failed to delete warning message: {e}")
            return

        # Проверяем начинается ли с #Продам
        is_selling = message_text.lower().startswith("#продам")
        
        # Если сообщение не о продаже, игнорируем его
        if not is_selling:
            logger.info("Message is not a selling post")
            return

        # Для объявлений о продаже проверяем цену
        price = await extract_price(message_text)
        logger.info(f"Extracted price: {price}")

        if price is None or price < 3000:
            logger.info(f"Price too low or not found: {price}")
            if await delete_message_safe(message):
                warning_msg = await message.answer(
                    f"❌ @{username}, ваше повідомлення було видалено, оскільки мінімальна ціна оголошення — 3000 грн."
                )
                # Schedule deletion after 5 seconds
                await asyncio.sleep(5)
                try:
                    await bot.delete_message(chat_id=message.chat.id, message_id=warning_msg.message_id)
                    logger.info(f"Warning message deleted after 5 seconds")
                except Exception as e:
                    logger.error(f"Failed to delete warning message: {e}")
            return

        logger.info("Message passed all checks")

    except Exception as e:
        logger.error(f"Error processing message: {e}", exc_info=True)

RULES_TEXT = """Правила гілки ПРОДАЖ / КУПІВЛЯ 📌

1. У цій гілці дозволено лише оголошення з хештегами #куплю або #продам.
2. Для оголошень з хештегом #продам обов'язково вказувати ціну. Мінімальна сума – 3000 грн.
3. Для оголошень з хештегом #куплю вказувати бюджет/ціну не обов'язково.
4. Оголошення, що не відповідають тематиці або не містять необхідної інформації, будуть видалятися.
5. Адміністрація залишає за собою право видаляти повідомлення без попередження.

⚠️ Порушення правил може призвести до видалення повідомлень або блокування користувача. Дотримання цих правил допоможе підтримувати порядок у чаті."""

# HTTP server for healthcheck
class SimpleHTTPRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        self.wfile.write(b'Bot is running')

def run_http_server():
    port = int(os.environ.get("PORT", 8080))
    server_address = ('', port)
    httpd = HTTPServer(server_address, SimpleHTTPRequestHandler)
    logger.info(f"Starting HTTP server on port {port}")
    httpd.serve_forever()

async def send_warning_message(chat_id, thread_id, text, delete_after=5, user_id=None):
    """Send a warning message and delete it after specified time"""
    try:
        warning_msg = await bot.send_message(
            chat_id=chat_id,
            text=text,
            message_thread_id=thread_id
        )
        
        logger.info(f"Sent warning message to user ID {user_id if user_id else 'unknown'}")
        
        if delete_after > 0:
            # Schedule deletion after specified time
            await asyncio.sleep(delete_after)
            try:
                await bot.delete_message(chat_id=chat_id, message_id=warning_msg.message_id)
                logger.info(f"Warning message deleted after {delete_after} seconds")
            except Exception as e:
                logger.error(f"Failed to delete warning message: {e}")
                
        return warning_msg
    except Exception as e:
        logger.error(f"Error sending warning message: {e}")
        return None

async def cleanup_task():
    """Periodically clean up tracked media groups"""
    while True:
        try:
            # Clear processed media groups every minute
            processed_message_groups.clear()
            logger.info("Cleaned up processed media groups")
            await asyncio.sleep(60)  # Wait for 1 minute
        except Exception as e:
            logger.error(f"Error in cleanup task: {e}")
            await asyncio.sleep(60)  # Wait even if there's an error

async def main():
    try:
        # Start HTTP server in separate thread
        threading.Thread(target=run_http_server, daemon=True).start()
        
        # Start cleanup task
        asyncio.create_task(cleanup_task())

        logger.info("Bot started")

        # Initialize Bot instance with a default parse mode and reset webhook
        await bot.session.close()
        await dp.start_polling(bot, reset_webhook=True)
    except Exception as e:
        logger.error(f"Critical error in main: {e}")

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

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped")
    except Exception as e:
        logger.error(f"Critical error: {e}")
