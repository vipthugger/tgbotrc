import re
import logging
from typing import Optional, Dict, List
from datetime import datetime
from database import XPDatabase

logger = logging.getLogger(__name__)

class XPSystem:
    def __init__(self, db_path: str = "resale_bot.db"):
        self.db = XPDatabase(db_path)
        
        # Rank thresholds (XP required for each rank)
        self.rank_thresholds = [
            (0, "Новачок"),
            (50, "Учасник"),
            (150, "Активіст"),
            (300, "Авторитет"),
            (600, "Ветеран"),
            (1000, "Легенда")
        ]
        
        # Special ranks that can only be assigned manually
        self.special_ranks = ["Ресейлер", "Адміністратор"]
        
        # Short messages that should not give XP
        self.spam_patterns = [
            r'^[+\-\.]$',  # Single character +, -, .
            r'^(ок|ok|да|не|нет)$',  # Single word responses
            r'^[+\-]*$',  # Only plus/minus characters
            r'^\s*$',  # Only whitespace
            r'^.{1,2}$'  # Messages with 1-2 characters
        ]
    
    def is_spam_message(self, text: str) -> bool:
        """Check if message should be considered spam and not give XP"""
        if not text:
            return True
        
        text = text.strip().lower()
        
        # Check against spam patterns
        for pattern in self.spam_patterns:
            if re.match(pattern, text, re.IGNORECASE):
                return True
        
        return False
    
    def calculate_rank_from_xp(self, xp: int) -> str:
        """Calculate rank based on XP amount"""
        for threshold, rank in reversed(self.rank_thresholds):
            if xp >= threshold:
                return rank
        return "Новачок"
    
    async def process_message_xp(self, user_id: int, username: str, first_name: str, message_text: str) -> Optional[Dict]:
        """Process XP gain from a message"""
        try:
            # Create or update user
            await self.db.create_or_update_user(user_id, username, first_name)
            
            # Check if message is spam
            if self.is_spam_message(message_text):
                logger.debug(f"Spam message from user {user_id}, no XP given")
                return None
            
            # Check if user can gain XP (cooldown and daily limit)
            if not await self.db.can_gain_xp(user_id):
                logger.debug(f"User {user_id} cannot gain XP due to cooldown or daily limit")
                return None
            
            # Give 1 XP for message
            success = await self.db.add_xp(user_id, 1, "Сообщение в чате")
            if not success:
                return None
            
            # Get updated user data
            user_data = await self.db.get_user(user_id)
            if not user_data:
                return None
            
            # Check if rank should be updated
            current_rank = user_data.get('rank')
            calculated_rank = self.calculate_rank_from_xp(user_data['xp'])
            
            # Only update rank if it's not a special rank
            if current_rank not in self.special_ranks and current_rank != calculated_rank:
                await self.db.set_rank(user_id, calculated_rank)
                user_data['rank'] = calculated_rank
                logger.info(f"Rank updated for user {user_id}: {current_rank} -> {calculated_rank}")
            
            return {
                'user_id': user_id,
                'username': username,
                'xp_gained': 1,
                'total_xp': user_data['xp'],
                'rank': user_data['rank'],
                'rank_changed': current_rank != user_data['rank']
            }
            
        except Exception as e:
            logger.error(f"Error processing message XP for user {user_id}: {e}")
            return None
    
    async def get_user_profile(self, user_id: int) -> Optional[Dict]:
        """Get user's profile information"""
        try:
            user_data = await self.db.get_user(user_id)
            if not user_data:
                return None
            
            # Get user's position in leaderboard
            position = await self.db.get_user_rank_position(user_id)
            
            return {
                'user_id': user_data['user_id'],
                'username': user_data['username'],
                'first_name': user_data['first_name'],
                'xp': user_data['xp'],
                'rank': user_data['rank'],
                'leaderboard_position': position,
                'daily_xp': user_data.get('daily_xp', 0),
                'created_at': user_data.get('created_at')
            }
            
        except Exception as e:
            logger.error(f"Error getting profile for user {user_id}: {e}")
            return None
    
    async def get_leaderboard(self, limit: int = 10) -> List[Dict]:
        """Get XP leaderboard"""
        try:
            return await self.db.get_leaderboard(limit)
        except Exception as e:
            logger.error(f"Error getting leaderboard: {e}")
            return []
    
    # Admin commands
    async def admin_add_xp(self, target_user_id: int, xp_amount: int, admin_id: int, reason: str = "Админ добавил XP") -> bool:
        """Admin command to add XP to user"""
        try:
            success = await self.db.add_xp(target_user_id, xp_amount, reason, admin_id)
            if success:
                # Check if rank should be updated
                user_data = await self.db.get_user(target_user_id)
                if user_data:
                    current_rank = user_data.get('rank')
                    calculated_rank = self.calculate_rank_from_xp(user_data['xp'])
                    
                    if current_rank not in self.special_ranks and current_rank != calculated_rank:
                        await self.db.set_rank(target_user_id, calculated_rank)
            
            return success
        except Exception as e:
            logger.error(f"Error in admin_add_xp: {e}")
            return False
    
    async def admin_set_xp(self, target_user_id: int, xp_amount: int, admin_id: int) -> bool:
        """Admin command to set user XP to specific amount"""
        try:
            success = await self.db.set_xp(target_user_id, xp_amount, "Админ установил XP", admin_id)
            if success:
                # Update rank based on new XP
                user_data = await self.db.get_user(target_user_id)
                if user_data:
                    current_rank = user_data.get('rank')
                    calculated_rank = self.calculate_rank_from_xp(user_data['xp'])
                    
                    if current_rank not in self.special_ranks and current_rank != calculated_rank:
                        await self.db.set_rank(target_user_id, calculated_rank)
            
            return success
        except Exception as e:
            logger.error(f"Error in admin_set_xp: {e}")
            return False
    
    async def admin_reset_xp(self, target_user_id: int, admin_id: int) -> bool:
        """Admin command to reset user XP to 0"""
        try:
            success = await self.db.reset_xp(target_user_id, admin_id)
            if success:
                # Reset rank to default unless it's a special rank
                user_data = await self.db.get_user(target_user_id)
                if user_data and user_data.get('rank') not in self.special_ranks:
                    await self.db.set_rank(target_user_id, "Новачок")
            
            return success
        except Exception as e:
            logger.error(f"Error in admin_reset_xp: {e}")
            return False
    
    async def admin_set_rank(self, target_user_id: int, rank: str, admin_id: int) -> bool:
        """Admin command to set custom rank"""
        try:
            return await self.db.set_rank(target_user_id, rank, admin_id)
        except Exception as e:
            logger.error(f"Error in admin_set_rank: {e}")
            return False
    
    def get_rank_emoji(self, rank: str) -> str:
        """Get emoji for rank"""
        rank_emojis = {
            "Новачок": "🌱",
            "Учасник": "👤", 
            "Активіст": "⭐",
            "Авторитет": "👑",
            "Ветеран": "🏆",
            "Легенда": "💎",
            "Ресейлер": "💰",
            "Адміністратор": "🔥"
        }
        return rank_emojis.get(rank, "❓")
    
    def format_xp_number(self, xp: int) -> str:
        """Format XP number with proper spacing"""
        return f"{xp:,}".replace(",", " ")
