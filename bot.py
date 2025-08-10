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
from cooldown_manager import CooldownManager
from xp_system import XPSystem

# Налаштування логування
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

# Initialize XP system (NEW)
xp_system = XPSystem()

# Initialize cooldown manager (12 hours = 43200 seconds)
cooldown_manager = CooldownManager(cooldown_seconds=43200)

# Get token from environment variable
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not TOKEN or TOKEN == "YOUR_BOT_TOKEN":
    logger.error("TELEGRAM_BOT_TOKEN is not set or is invalid!")
    raise ValueError("TELEGRAM_BOT_TOKEN environment variable is required")

logger.info(f"Token loaded: {TOKEN[:10]}...{TOKEN[-5:] if len(TOKEN) > 15 else 'short'}")
bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

# Регулярні вирази для визначення ціни
# Поддержка формата: 1500, 3.500, 3,500, 3500, 3.5k, etc. + uah, usd
price_pattern = re.compile(r"(ціна:|price:|цена:|ціна\s*:|\$).*?(\d+(?:[.,]\d{3})*(?:[.,]\d{1,2})?)\s*(грн|uah|usd|k|к|kг|тис|₴|\$|гривен)?", re.IGNORECASE)
price_fallback_pattern = re.compile(r"(\d+(?:[.,]\d{3})*(?:[.,]\d{1,2})?)\s*(грн|uah|usd|k|к|kг|тис|₴|\$|гривен)?", re.IGNORECASE)

# Global variables
processed_media_groups = {}
user_warnings = {}
resale_topic_id = None
report_chat_id = None
processed_message_groups = set()  # Track processed message groups to avoid duplicate warnings
reported_messages = set()  # Track reported messages to avoid duplicates
user_warning_cooldown = {}  # Anti-flood for warnings

async def extract_price(text: str) -> float | None:
    """Extract price from text"""
    try:
        logger.info(f"Extracting price from text: {text[:200]}")
        
        # Сначала ищем цену после ключевых слов
        matches = price_pattern.search(text.lower())
        if matches:
            price_str = matches.group(2)
            currency = matches.group(3) if matches.group(3) else ""
            
            # Обрабатываем различные форматы чисел
            original_price_str = price_str
            price_str = price_str.replace(',', '.')  # Заменяем запятые точками
            
            # Если есть несколько точек (например 3.500), убираем точки как разделители тысяч
            if price_str.count('.') > 1 or (price_str.count('.') == 1 and len(price_str.split('.')[-1]) == 3):
                price_str = price_str.replace('.', '', price_str.count('.') - 1) if price_str.count('.') > 1 else price_str.replace('.', '')
            
            try:
                price = float(price_str)
                # Применяем множители
                if currency and currency.lower() in ['k', 'к', 'тис']:
                    price *= 1000
                logger.info(f"Извлечена цена после ключевого слова: {price} грн (исходный текст: '{original_price_str} {currency}')")
                return price
            except ValueError:
                logger.error(f"Ошибка парсинга цены: '{original_price_str}' -> '{price_str}'")
                pass

        # Если не нашли цену после ключевых слов, ищем просто числа больше 100 (минимальная разумная цена)
        matches = price_fallback_pattern.finditer(text)
        max_price = 0
        for match in matches:
            price_str = match.group(1)
            currency = match.group(2) if match.group(2) else ""
            
            # Обрабатываем различные форматы чисел
            original_price_str = price_str
            price_str = price_str.replace(',', '.')  # Заменяем запятые точками
            
            # Если есть несколько точек (например 3.500), убираем точки как разделители тысяч
            if price_str.count('.') > 1 or (price_str.count('.') == 1 and len(price_str.split('.')[-1]) == 3):
                price_str = price_str.replace('.', '', price_str.count('.') - 1) if price_str.count('.') > 1 else price_str.replace('.', '')
            
            try:
                price = float(price_str)
                # Применяем множители
                if currency and currency.lower() in ['k', 'к', 'тис']:
                    price *= 1000
                # Учитываем только цены больше 100 грн
                if price >= 100:
                    max_price = max(max_price, price)
                    logger.debug(f"Найдена цена fallback: {price} грн (исходный: '{original_price_str} {currency}')")
            except ValueError:
                continue

        logger.info(f"Извлечена максимальная цена: {max_price} грн")
        return max_price if max_price > 0 else None
    except Exception as e:
        logger.error(f"Ошибка при извлечении цены: {e}")
        return None

async def get_minimum_price(text: str) -> int:
    """
    Get minimum price based on message content.
    Returns 1500 UAH for #футболка posts, 3000 UAH for others.
    """
    # Check for #футболка hashtag in Ukrainian text
    if re.search(r'#футболка', text, re.IGNORECASE):
        return 1500
    return 3000

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
        
        # If message is part of a media group, handle it specially
        if message.media_group_id:
            media_group_id = message.media_group_id
            
            # Check if we've already processed this media group
            if media_group_id in processed_message_groups:
                return True  # Already handled
            
            # Mark this media group as processed immediately
            processed_message_groups.add(media_group_id)
            
            # Initialize media group collection if needed
            if media_group_id not in processed_media_groups:
                processed_media_groups[media_group_id] = []
            
            # Add current message to the group collection
            processed_media_groups[media_group_id].append(message)
            
            # Schedule deletion task to handle the media group
            async def handle_media_group_deletion():
                try:
                    # Wait for all messages in media group to arrive
                    await asyncio.sleep(2)
                    
                    # Delete all collected messages in the media group
                    deleted_count = 0
                    if media_group_id in processed_media_groups:
                        messages_to_delete = processed_media_groups[media_group_id][:]
                        for msg in messages_to_delete:
                            try:
                                await msg.delete()
                                deleted_count += 1
                            except Exception as e:
                                logger.error(f"Error deleting media group message: {e}")
                        
                        logger.info(f"Deleted {deleted_count} messages from media group {media_group_id}")
                        
                        # Clean up the processed group
                        if media_group_id in processed_media_groups:
                            del processed_media_groups[media_group_id]
                    
                    # Remove from processed groups after handling
                    processed_message_groups.discard(media_group_id)
                    
                except Exception as e:
                    logger.error(f"Error in media group deletion task: {e}")
                    # Cleanup in case of error
                    processed_message_groups.discard(media_group_id)
                    if media_group_id in processed_media_groups:
                        del processed_media_groups[media_group_id]
            
            # Start the deletion task
            asyncio.create_task(handle_media_group_deletion())
            
        else:
            # Single message deletion
            await message.delete()
        
        return True
    except Exception as e:
        logger.error(f"Error deleting message: {e}")
        return False

def get_post_category(text: str) -> str | None:
    """Determine if post is buy/sell category"""
    text_lower = text.lower()
    if '#куплю' in text_lower or '#купим' in text_lower:
        return 'buy'
    elif '#продам' in text_lower or '#продаю' in text_lower:
        return 'sell'
    return None

async def is_user_admin(chat_id: int, user_id: int) -> bool:
    """Check if user is admin in the chat"""
    try:
        chat_admins = await bot.get_chat_administrators(chat_id)
        admin_ids = [admin.user.id for admin in chat_admins]
        return user_id in admin_ids
    except Exception as e:
        logger.error(f"Error checking admin status: {e}")
        return False

async def get_user_rank(user_id: int) -> str:
    """Get user rank for cooldown bonus calculation (NEW FUNCTION)"""
    try:
        user = await xp_system.db.get_user(user_id)
        if user:
            return user['rank']
        return 'Новачок'
    except Exception as e:
        logger.error(f"Error getting user rank: {e}")
        return 'Новачок'

async def delete_warning_after_delay(warning_message: types.Message, delay_seconds: int):
    """Delete warning message after specified delay"""
    try:
        await asyncio.sleep(delay_seconds)
        await warning_message.delete()
    except Exception as e:
        logger.error(f"Error deleting warning message: {e}")

# NEW XP SYSTEM COMMANDS

@dp.message(Command("myprofile"))
async def cmd_myprofile(message: types.Message):
    """Show user's XP profile"""
    try:
        # Check if user is admin
        is_admin = await is_user_admin(message.chat.id, message.from_user.id)
        
        # Get user profile - don't override admin rank display if they have actual rank
        profile = await xp_system.get_user_profile(message.from_user.id, False)  # Always show actual rank
        if not profile:
            await message.reply("❌ Ваш профіль не знайдено. Напишіть повідомлення в чаті, щоб створити профіль.")
            return
        
        # Override display rank for admins only if their actual rank is basic
        display_rank = profile['rank']
        if is_admin and profile['rank'] in ['Новачок', 'Учасник', 'Активіст']:
            display_rank = "Адміністратор"
        
        # Format profile message
        profile_text = f"<b>Профіль користувача</b>\n\n"
        profile_text += f"<b>Ім'я:</b> {profile['first_name'] or 'Не вказано'}\n"
        if profile['username']:
            profile_text += f"<b>Username:</b> @{profile['username']}\n"
        profile_text += f"<b>XP:</b> {profile['xp']}\n"
        profile_text += f"<b>Ранг:</b> {display_rank}\n"
        profile_text += f"<b>XP сьогодні:</b> {profile['daily_xp']}/100\n"
        
        # Show next rank info if applicable (not for special ranks or admin override)
        if profile['next_rank'] and display_rank not in ['Ресейлер', 'Адміністратор', 'Легенда']:
            next_rank = profile['next_rank']
            profile_text += f"\n<b>Наступний ранг:</b> {next_rank['rank']}\n"
            profile_text += f"<b>Потрібно XP:</b> {next_rank['xp_needed']}\n"
        
        # Add rank bonuses for Reseller
        if profile['rank'] == "Ресейлер":
            profile_text += f"\n<b>Бонуси:</b>\n• +1 оголошення на годину"
        
        await message.reply(profile_text)
        
    except Exception as e:
        logger.error(f"Error in myprofile command: {e}")
        await message.reply("❌ Помилка при отриманні профілю.")

@dp.message(Command("perks"))
async def cmd_perks(message: types.Message):
    """Show available ranks and their requirements"""
    try:
        rank_list = xp_system.get_rank_list()
        await message.reply(rank_list)
    except Exception as e:
        logger.error(f"Error in perks command: {e}")
        await message.reply("❌ Помилка при отриманні списку рангів.")

@dp.message(Command("top"))
async def cmd_top(message: types.Message):
    """Show XP leaderboard"""
    try:
        top_users = await xp_system.get_top_users(10)
        if not top_users:
            await message.reply("❌ Рейтинг порожній.")
            return
        
        top_text = "<b>🏆 Топ користувачів за XP</b>\n\n"
        
        for i, user in enumerate(top_users, 1):
            medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"{i}."
            
            # Create clickable nickname that leads to profile (not @mention)
            if user['username']:
                # Display just the username without @ but make it clickable
                username_display = f'<a href="https://t.me/{user["username"]}">{user["username"]}</a>'
            else:
                # For users without username, use first name and link to profile
                display_name = user['first_name'] or "Невідомий"
                username_display = f'<a href="tg://user?id={user["user_id"]}">{display_name}</a>'
            
            top_text += f"{medal} <b>{username_display}</b>\n"
            top_text += f"   {user['xp']} XP • {user['rank']}\n\n"
        
        await message.reply(top_text, disable_web_page_preview=True)
        
    except Exception as e:
        logger.error(f"Error in top command: {e}")
        await message.reply("❌ Помилка при отриманні рейтингу.")

# ADMIN XP COMMANDS

@dp.message(Command("addxp"))
async def cmd_addxp(message: types.Message):
    """Add XP to user (admin only)"""
    try:
        # Check if user is admin
        if not await is_user_admin(message.chat.id, message.from_user.id):
            await message.reply("❌ Ця команда тільки для адміністраторів.")
            return
        
        # Parse command - must reply to user's message
        if not message.reply_to_message:
            await message.reply("❌ Відповідайте на повідомлення користувача, якому хочете дати XP.\nВикористання: /addxp 100")
            return
        
        # Get amount from command text
        try:
            command_parts = (message.text or "").split()
            if len(command_parts) < 2:
                await message.reply("❌ Використання: /addxp кількість")
                return
            amount = int(command_parts[1])
            if amount <= 0:
                await message.reply("❌ Кількість XP повинна бути більше 0.")
                return
        except ValueError:
            await message.reply("❌ Некоректна кількість XP.")
            return
        
        target_user_id = message.reply_to_message.from_user.id
        target_username = message.reply_to_message.from_user.username or "користувач"
        
        # Add XP
        success = await xp_system.add_xp_admin(target_user_id, amount, message.from_user.id)
        if success:
            await message.reply(f"✅ Додано {amount} XP користувачу @{target_username}")
        else:
            await message.reply("❌ Помилка при додаванні XP.")
        
    except Exception as e:
        logger.error(f"Error in addxp command: {e}")
        await message.reply("❌ Помилка при виконанні команди.")

@dp.message(Command("removexp"))
async def cmd_removexp(message: types.Message):
    """Remove XP from user (admin only)"""
    try:
        # Check if user is admin
        if not await is_user_admin(message.chat.id, message.from_user.id):
            await message.reply("❌ Ця команда тільки для адміністраторів.")
            return
        
        # Parse command - must reply to user's message
        if not message.reply_to_message:
            await message.reply("❌ Відповідайте на повідомлення користувача, у якого хочете забрати XP.\nВикористання: /removexp 100")
            return
        
        # Get amount from command text
        try:
            command_parts = (message.text or "").split()
            if len(command_parts) < 2:
                await message.reply("❌ Використання: /removexp кількість")
                return
            amount = int(command_parts[1])
            if amount <= 0:
                await message.reply("❌ Кількість XP повинна бути більше 0.")
                return
        except ValueError:
            await message.reply("❌ Некоректна кількість XP.")
            return
        
        target_user_id = message.reply_to_message.from_user.id
        target_username = message.reply_to_message.from_user.username or "користувач"
        
        # Remove XP
        success = await xp_system.remove_xp_admin(target_user_id, amount, message.from_user.id)
        if success:
            await message.reply(f"✅ Забрано {amount} XP у користувача @{target_username}")
        else:
            await message.reply("❌ Помилка при забиранні XP.")
        
    except Exception as e:
        logger.error(f"Error in removexp command: {e}")
        await message.reply("❌ Помилка при виконанні команди.")

@dp.message(Command("setrank"))
async def cmd_setrank(message: types.Message):
    """Set user rank (admin only)"""
    try:
        # Check if user is admin
        if not await is_user_admin(message.chat.id, message.from_user.id):
            await message.reply("❌ Ця команда тільки для адміністраторів.")
            return
        
        # Parse command - must reply to user's message
        if not message.reply_to_message:
            await message.reply("❌ Відповідайте на повідомлення користувача, якому хочете змінити ранг.\nВикористання: /setrank Ресейлер")
            return
        
        # Get rank from command text
        command_parts = (message.text or "").split(None, 1)
        if len(command_parts) < 2:
            await message.reply("❌ Використання: /setrank назва_рангу")
            return
        rank = command_parts[1]
        
        target_user_id = message.reply_to_message.from_user.id
        target_username = message.reply_to_message.from_user.username or "користувач"
        
        # Set rank
        success = await xp_system.set_rank_admin(target_user_id, rank, message.from_user.id)
        if success:
            await message.reply(f"✅ Встановлено ранг '{rank}' користувачу @{target_username}")
        else:
            await message.reply("❌ Помилка при встановленні рангу.")
        
    except Exception as e:
        logger.error(f"Error in setrank command: {e}")
        await message.reply("❌ Помилка при виконанні команди.")

@dp.message(Command("resetxp"))
async def cmd_resetxp(message: types.Message):
    """Reset user XP (admin only)"""
    try:
        # Check if user is admin
        if not await is_user_admin(message.chat.id, message.from_user.id):
            await message.reply("❌ Ця команда тільки для адміністраторів.")
            return
        
        # Parse command - must reply to user's message
        if not message.reply_to_message:
            await message.reply("❌ Відповідайте на повідомлення користувача, якому хочете скинути XP.\nВикористання: /resetxp")
            return
        
        target_user_id = message.reply_to_message.from_user.id
        target_username = message.reply_to_message.from_user.username or "користувач"
        
        # Reset XP
        success = await xp_system.reset_xp_admin(target_user_id, message.from_user.id)
        if success:
            await message.reply(f"✅ Скинуто XP користувача @{target_username}")
        else:
            await message.reply("❌ Помилка при скидуванні XP.")
        
    except Exception as e:
        logger.error(f"Error in resetxp command: {e}")
        await message.reply("❌ Помилка при виконанні команди.")

# ORIGINAL COMMANDS - UNCHANGED

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
        
        # Get reported user from the replied message
        reported_user = message.reply_to_message.from_user
        
        # Get message link if possible
        message_link = ""
        try:
            message_link = f"https://t.me/c/{str(message.chat.id)[4:]}/{message.reply_to_message.message_id}"
        except Exception as e:
            logger.error(f"Error getting message link: {e}")
        
        # Prepare report message
        report_text = (
            f"<b>🔔 Нова скарга!</b>\n"
            f"Відправник: @{message.from_user.username or 'Anonymous'}\n"
            f"Порушник: @{reported_user.username if reported_user and reported_user.username else 'Anonymous'}\n"
            f"Причина: {reason}\n"
            f"Посилання на повідомлення: {message_link}"
        )
        
        # Send report to admin chat
        report_msg = await bot.send_message(
            chat_id=report_chat_id,
            text=report_text,
            reply_to_message_id=None
        )
        
        # Forward reported message (optional, only log errors without user notification)
        try:
            forwarded = await message.reply_to_message.forward(report_chat_id)
            logger.info(f"Report message forwarded: {forwarded.message_id}")
        except Exception as e:
            logger.error(f"Failed to forward reported message: {e}")
        
        await message.reply("✅ Скаргу відправлено адміністрації.")
        logger.info(f"Report sent from {message.from_user.username} about {reported_user.username if reported_user else 'Unknown'}")
        
        # NEW: Give XP for valid report (after admin confirmation would be better, but this works)
        if message.from_user and not message.from_user.is_bot:
            try:
                await xp_system.db.create_or_update_user(
                    message.from_user.id,
                    message.from_user.username,
                    message.from_user.first_name
                )
                await xp_system.db.add_xp(message.from_user.id, 5, "Відправка скарги")
            except Exception as e:
                logger.error(f"Error adding XP for report: {e}")
        
    except Exception as e:
        logger.error(f"Error handling report: {e}")
        await message.reply("❌ Помилка при відправці скарги.")

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

@dp.message(Command("resetcd"))
async def reset_cooldown_command(message: types.Message):
    """Reset cooldown for user (admin only)"""
    try:
        # Get admin list
        chat_admins = await message.chat.get_administrators()
        admin_ids = [admin.user.id for admin in chat_admins]
        
        if message.from_user.id not in admin_ids:
            logger.info(f"Non-admin {message.from_user.username} tried to use admin command")
            await send_warning_and_delete(
                message.chat.id,
                "❌ Ця команда тільки для адміністраторів.",
                message.message_thread_id,
                3
            )
            return
        
        # Parse command: /resetcd @username [buy|sell|all]
        # Default is 'all' if not specified
        args = message.text.split()
        if len(args) < 2:
            await send_warning_and_delete(
                message.chat.id,
                "❌ Використання: /resetcd @username [buy|sell|all]",
                message.message_thread_id,
                3
            )
            return
        
        # Get username (remove @ if present)
        username = args[1].replace('@', '')
        
        # Get category (default to 'all')
        category = 'all'
        if len(args) >= 3 and args[2].lower() in ['buy', 'sell', 'all', 'куплю', 'продам']:
            category = args[2].lower()
        
        # Convert Ukrainian to English
        if category == 'куплю':
            category = 'buy'
        elif category == 'продам':
            category = 'sell'
        
        # Find user in chat
        try:
            # First try to get user ID directly from command if it's mentioned
            user_id = None
            
            # Check if there's a numeric ID in the command
            for arg in message.text.split():
                if arg.isdigit():
                    user_id = int(arg)
                    break
            
            # If not found by ID, look in admins list
            if not user_id:
                for member in chat_admins:
                    if member.user.username and member.user.username.lower() == username.lower():
                        user_id = member.user.id
                        break
            
            # If still not found, try to use reply to message
            if not user_id and message.reply_to_message:
                user_id = message.reply_to_message.from_user.id
            
            if not user_id:
                await send_warning_and_delete(
                    message.chat.id,
                    f"❌ Користувач @{username} не знайдений.",
                    message.message_thread_id,
                    5
                )
                return
            
            # Reset cooldown
            success = cooldown_manager.reset_cooldown(user_id, category)
            
            if success:
                category_text = {
                    'buy': '#куплю',
                    'sell': '#продам',
                    'all': 'всіх категорій'
                }.get(category, category)
                
                await send_warning_and_delete(
                    message.chat.id,
                    f"✅ Кулдаун для @{username} у категорії {category_text} скинуто.",
                    message.message_thread_id,
                    5
                )
                logger.info(f"Cooldown reset for user {user_id} in category {category} by admin {message.from_user.username}")
            else:
                await send_warning_and_delete(
                    message.chat.id,
                    f"❌ Кулдаун для @{username} не знайдено або вже неактивний.",
                    message.message_thread_id,
                    5
                )
        
        except Exception as e:
            logger.error(f"Error finding user for cooldown reset: {e}")
            await send_warning_and_delete(
                message.chat.id,
                f"❌ Помилка при пошуку користувача @{username}.",
                message.message_thread_id,
                5
            )
    
    except Exception as e:
        logger.error(f"Error in reset_cooldown_command: {e}")
        await send_warning_and_delete(
            message.chat.id,
            "❌ Помилка при скиданні кулдауна.",
            message.message_thread_id,
            3
        )

# Rules text constant
RULES_TEXT = """
III. ПРАВИЛА ГІЛКИ ПРОДАЖУ / КУПІВЛІ

 3.1. Мінімальна вартість речей, дозволених до продажу:
 • Загальні товари — від 3000 грн.
 • Категорія #футболка — від 1500 грн.
 3.2. Заборонено продавати підробки, браковані речі або речі з дефектами без чіткого опису всіх недоліків.
 3.3. Обмеження на публікації:
 • Не більше 1 оголошення кожного типу (#продам або #куплю) раз на 12 годин.
 • Заборонено дублювати або оновлювати оголошення з тією ж самою річчю до закінчення кулдауну.
 3.4. Оголошення мають містити: якісні фото, хештег (#продам, #куплю або #футболка), ціну, розмір та стан речі.
 3.5. Угоди проводяться виключно на відповідальність покупця та продавця. Адміністрація не несе повної відповідальності за наслідки угод.
 3.6. Заборонено токсичну або хамську поведінку. Спірні ситуації вирішуйте конструктивно.
 3.7. Обмін речами дозволений лише за взаємною згодою сторін.
 3.8. Заборонено публікувати оголошення, що не стосуються одягу, взуття або аксесуарів.
 3.9. Заборонено будь-яким чином обходити або перешкоджати роботі бота, у тому числі:
 • публікувати повідомлення, які технічно не обробляються ботом;
 • змінювати формат з метою приховати порушення;
 • додавати в одне оголошення декілька різних речей;
 • зловживати технічними недоліками чи помилками.
 3.10. Категорично заборонена реклама сторонніх каналів, посилань або сервісів без дозволу адміністрації.

⚠️ Порушення правил може призвести до видалення повідомлень, обмеження функцій або блокування користувача.

💬 Для скарги: відповідайте на повідомлення командою /report [причина]
"""

async def send_warning_and_delete(chat_id: int, text: str, thread_id: int = None, delete_after: int = 3, user_id: int = None):
    """Send a warning message and delete it after specified time (with anti-flood)"""
    try:
        # Anti-flood protection - only one warning per user per 30 seconds
        # But always send the first warning
        if user_id:
            current_time = datetime.now()
            last_warning_time = user_warning_cooldown.get(user_id)
            
            if last_warning_time and (current_time - last_warning_time).total_seconds() < 30:
                logger.info(f"Warning suppressed for user {user_id} due to anti-flood")
                return
            
            # Send warning and update cooldown
            user_warning_cooldown[user_id] = current_time
        
        # Add user mention for resale topic warnings if user_id provided
        final_text = text
        if user_id and thread_id == resale_topic_id:
            # Get username for proper @mention
            try:
                # Get user info from database
                user_data = await xp_system.db.get_user(user_id)
                if user_data and user_data.get('username'):
                    username = user_data['username']
                    # Format text with @username as bold prefix
                    if text.startswith('❌'):
                        final_text = f"❌<b>@{username}</b>, {text[2:].strip()}"
                    elif text.startswith('⏰') or text.startswith('<b>⏰'):
                        final_text = f"⏰<b>@{username}</b>, {text.replace('⏰', '').replace('<b>', '').replace('</b>', '').strip()}"
                    else:
                        final_text = f"<b>@{username}</b>, {text}"
                else:
                    # Fallback to mention link if no username
                    final_text = f'<a href="tg://user?id={user_id}">👤</a> {text}'
            except Exception as e:
                logger.error(f"Error getting username for mention: {e}")
                final_text = f'<a href="tg://user?id={user_id}">👤</a> {text}'
        
        warning_msg = await bot.send_message(
            chat_id=chat_id,
            text=final_text,
            message_thread_id=thread_id
        )
        
        # Schedule deletion after specified time (default 3 seconds)
        asyncio.create_task(delete_warning_after_delay(warning_msg, delete_after))
        
    except Exception as e:
        logger.error(f"Error sending warning message: {e}")

# MAIN MESSAGE HANDLER - MODIFIED TO INCLUDE XP AND RESELLER BONUS

@dp.message()
async def handle_message(message: types.Message):
    """Main message handler with XP processing and original functionality"""
    try:
        # NEW: Process XP for all messages (not just resale topic)
        if message.from_user and not message.from_user.is_bot:
            try:
                message_text = message.text or message.caption or ""
                xp_result = await xp_system.process_message_xp(
                    message.from_user.id,
                    message.from_user.username or "",
                    message.from_user.first_name or "",
                    message_text
                )
                
                # Optional: Notify about rank promotion
                if xp_result and xp_result.get('promoted'):
                    try:
                        promotion_text = (
                            f"🎉 <b>{message.from_user.first_name}</b> отримує новий ранг: "
                            f"<b>{xp_result['new_rank']}</b>!"
                        )
                        await bot.send_message(
                            chat_id=message.chat.id,
                            text=promotion_text,
                            message_thread_id=message.message_thread_id
                        )
                    except Exception as e:
                        logger.error(f"Error sending promotion message: {e}")
                        
            except Exception as e:
                logger.error(f"Error processing XP for user {message.from_user.id}: {e}")
        
        # ORIGINAL RESALE FUNCTIONALITY - COMPLETE FROM ORIGINAL CODE
        # Skip if no resale topic set
        if resale_topic_id is None:
            return
        
        # Only process messages in the resale topic
        if message.message_thread_id != resale_topic_id:
            return
        
        # Skip bot messages
        if message.from_user.is_bot:
            return
        
        # Skip admin messages - admins have full permissions
        try:
            chat_admins = await message.chat.get_administrators()
            admin_ids = [admin.user.id for admin in chat_admins]
            if message.from_user.id in admin_ids:
                logger.info(f"Skipping admin message from @{message.from_user.username}")
                return
        except Exception as e:
            logger.error(f"Error checking admin status: {e}")
            # If we can't check admin status, continue processing
        
        # Skip legitimate commands but catch fake commands like "/." or "/text"
        if message.text and message.text.startswith('/'):
            # Allow legitimate bot commands (have space or are single command)
            text_parts = message.text.split()
            command_part = text_parts[0]
            
            # List of allowed commands
            allowed_commands = ['/resale_topic', '/notification', '/report', '/resetcd', '/changecd', '/set_report_chat', '/myprofile', '/perks', '/top', '/addxp', '/removexp', '/setrank', '/resetxp']
            
            # If it's a legitimate command, skip processing
            if command_part in allowed_commands:
                return
            
            # If it's a fake command (like "/." or "/random text"), treat as rule violation
            if len(command_part) == 1 or command_part not in allowed_commands:
                # This is a fake command used to bypass rules
                deleted = await delete_message_safe(message)
                if deleted:
                    await send_warning_and_delete(
                        message.chat.id,
                        f"❌ @{message.from_user.username}, ваше повідомлення було видалено, оскільки воно не містить хештегів '#куплю' або '#продам'.",
                        message.message_thread_id,
                        3,
                        message.from_user.id
                    )
                logger.info(f"Fake command deleted for @{message.from_user.username}: {message.text}")
                return
        
        # Handle media groups - collect all messages before processing
        if message.media_group_id:
            media_group_id = message.media_group_id
            
            # Add to collection
            if media_group_id not in processed_media_groups:
                processed_media_groups[media_group_id] = []
            processed_media_groups[media_group_id].append(message)
            
            # Wait for more messages (up to 1 second)
            await asyncio.sleep(0.1)
            
            # Only process when we have collected all messages or timeout
            if len(processed_media_groups[media_group_id]) == 1:
                # Wait a bit more for other messages in the group
                await asyncio.sleep(0.5)
            
            # Process only from the first message, but collect text from all
            if processed_media_groups[media_group_id][0].message_id != message.message_id:
                return  # Skip subsequent messages in the group
        
        # Get text from message (text, caption, or empty for media)
        if message.media_group_id and media_group_id in processed_media_groups:
            # Collect text from all messages in the media group
            all_texts = []
            for msg in processed_media_groups[media_group_id]:
                text = msg.text or msg.caption or ""
                if text.strip():
                    all_texts.append(text.strip())
            message_text = " ".join(all_texts)
        else:
            message_text = message.text or message.caption or ""
        
        # Check for prohibited content types
        if message.sticker:
            deleted = await delete_message_safe(message)
            if deleted:
                await send_warning_and_delete(
                    message.chat.id,
                    f"❌ Стікери заборонені у цій гілці.",
                    message.message_thread_id,
                    3,
                    message.from_user.id
                )
            logger.info(f"Sticker deleted for @{message.from_user.username}")
            return
        
        # Check for media without text - this violates rules and should be deleted
        if not message_text.strip():
            # Media without text/caption violates rules
            if message.photo or message.video or message.document or message.animation:
                deleted = await delete_message_safe(message)
                if deleted:
                    await send_warning_and_delete(
                        message.chat.id,
                        f"❌ Ваше повідомлення було видалено, оскільки воно не містить опису.",
                        message.message_thread_id,
                        3,
                        message.from_user.id
                    )
                logger.info(f"Media without text deleted for @{message.from_user.username}")
                return
            else:
                # Empty text message, ignore
                return
        
        # Check if this is a buy/sell post
        category = get_post_category(message_text)
        if not category:
            # If message contains text but no buy/sell hashtags, delete it as off-topic
            deleted = await delete_message_safe(message)
            if deleted:
                await send_warning_and_delete(
                    message.chat.id,
                    f"❌ Ваше повідомлення було видалено, оскільки воно не містить хештегів '#куплю' або '#продам'.",
                    message.message_thread_id,
                    3,
                    message.from_user.id
                )
            logger.info(f"Off-topic message deleted for @{message.from_user.username}")
            return
        
        logger.info(f"Processing {category} message from @{message.from_user.username}: {message_text[:100]}")
        
        user_id = message.from_user.id
        
        # NEW: Get user rank for cooldown bonus
        user_rank = await get_user_rank(user_id)
        
        # MODIFIED: Check cooldown with Reseller bonus (2 posts CONSECUTIVELY)
        if cooldown_manager.is_on_cooldown(user_id, category, user_rank):
            remaining_time = cooldown_manager.get_remaining_time(user_id, category)
            hours = remaining_time // 3600 if remaining_time else 0
            minutes = (remaining_time % 3600) // 60 if remaining_time else 0
            
            # Show different messages for Resellers
            if user_rank == "Ресейлер":
                posts_made = cooldown_manager.get_reseller_posts_count(user_id, category)
                warning_text = f"<b>⏰ Ви можете опублікувати наступне оголошення через {hours}г {minutes}хв</b>\n💎 Як Ресейлер ви вже використали {posts_made}/2 пости в цій категорії"
            else:
                warning_text = f"<b>⏰ Ви можете опублікувати наступне оголошення через {hours}г {minutes}хв</b>"
            
            # Delete the message
            deleted = await delete_message_safe(message)
            if deleted:
                # Send warning about cooldown with auto-delete
                await send_warning_and_delete(
                    message.chat.id,
                    warning_text,
                    message.message_thread_id,
                    3,
                    message.from_user.id
                )
            logger.info(f"Message deleted due to cooldown for @{message.from_user.username}")
            return
        
        # Extract and validate price (only for #продам posts)
        if category == 'sell':
            extracted_price = await extract_price(message_text)
            minimum_price = await get_minimum_price(message_text)  # Get minimum price based on content
            
            if extracted_price is None:
                # Delete message without price
                deleted = await delete_message_safe(message)
                if deleted:
                    await send_warning_and_delete(
                        message.chat.id,
                        f"❌ Ваше повідомлення було видалено, оскільки мінімальна ціна оголошення — 3000 грн.",
                        message.message_thread_id,
                        3,
                        message.from_user.id
                    )
                logger.info(f"Sell message deleted - no price found for @{message.from_user.username}")
                return
            
            if extracted_price < minimum_price:
                # Delete message with insufficient price
                deleted = await delete_message_safe(message)
                if deleted:
                    await send_warning_and_delete(
                        message.chat.id,
                        f"❌ Ваше повідомлення було видалено. Мінімальна ціна: {minimum_price} грн, вказана ціна: {extracted_price} грн.",
                        message.message_thread_id,
                        3,
                        message.from_user.id
                    )
                logger.info(f"Sell message deleted - price {extracted_price} below minimum {minimum_price} for @{message.from_user.username}")
                return
            
            logger.info(f"Valid sell post approved for @{message.from_user.username}, price: {extracted_price} грн")
        else:
            # For #куплю posts, price is optional
            logger.info(f"Valid buy post approved for @{message.from_user.username} (price not required)")
        
        # Message passed all checks - record successful post
        cooldown_manager.record_successful_post(user_id, category, user_rank)
        
    except Exception as e:
        logger.error(f"Error handling message: {e}", exc_info=True)

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/health':
            self.send_response(200)
            self.send_header('Content-type', 'text/plain')
            self.end_headers()
            self.wfile.write(b'OK')
        else:
            self.send_response(404)
            self.end_headers()
    
    def log_message(self, format, *args):
        pass

def run_health_server():
    """Run health check server in a separate thread"""
    server = HTTPServer(('0.0.0.0', 8000), HealthHandler)
    logger.info("Health check server starting on port 8000")
    server.serve_forever()

async def main():
    """Main function to start the bot"""
    try:
        # Start health check server in background
        health_thread = threading.Thread(target=run_health_server, daemon=True)
        health_thread.start()
        
        logger.info("Starting enhanced Resale Community Bot with XP system...")
        await dp.start_polling(bot)
    except Exception as e:
        logger.error(f"Error starting bot: {e}")
    finally:
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())
