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
            user = await self.db.get_user(user_id)
            if not user:
                return None
            
            # Check if rank should be updated
            current_rank = user['rank']
            new_rank = self.calculate_rank_from_xp(user['xp'])
            
            # Don't override special ranks with auto-calculated ranks
            if current_rank not in self.special_ranks and new_rank != current_rank:
                await self.db.set_rank(user_id, new_rank)
                user['rank'] = new_rank
                logger.info(f"User {user_id} promoted to {new_rank}")
                return {
                    'xp': user['xp'],
                    'old_rank': current_rank,
                    'new_rank': new_rank,
                    'promoted': True
                }
            
            return {
                'xp': user['xp'],
                'rank': user['rank'],
                'promoted': False
            }
            
        except Exception as e:
            logger.error(f"Error processing message XP for user {user_id}: {e}")
            return None
    
    async def get_user_profile(self, user_id: int, is_admin: bool = False) -> Optional[Dict]:
        """Get user profile information"""
        try:
            user = await self.db.get_user(user_id)
            if not user:
                return None
            
            # Override rank for administrators
            display_rank = "Адміністратор" if is_admin else user['rank']
            
            # Calculate next rank info
            next_rank_info = None
            if not is_admin and user['rank'] not in self.special_ranks:
                current_xp = user['xp']
                for threshold, rank in self.rank_thresholds:
                    if current_xp < threshold:
                        next_rank_info = {
                            'rank': rank,
                            'xp_needed': threshold - current_xp,
                            'threshold': threshold
                        }
                        break
            
            return {
                'user_id': user['user_id'],
                'username': user['username'],
                'first_name': user['first_name'],
                'xp': user['xp'],
                'rank': display_rank,
                'daily_xp': user['daily_xp'],
                'next_rank': next_rank_info
            }
            
        except Exception as e:
            logger.error(f"Error getting user profile {user_id}: {e}")
            return None
    
    async def add_xp_admin(self, user_id: int, xp_amount: int, admin_id: int) -> bool:
        """Add XP to user (admin command)"""
        try:
            # Ensure user exists
            user = await self.db.get_user(user_id)
            if not user:
                return False
            
            success = await self.db.add_xp(user_id, xp_amount, f"Административное начисление", admin_id)
            if not success:
                return False
            
            # Update rank if necessary
            updated_user = await self.db.get_user(user_id)
            if updated_user and updated_user['rank'] not in self.special_ranks:
                new_rank = self.calculate_rank_from_xp(updated_user['xp'])
                if new_rank != updated_user['rank']:
                    await self.db.set_rank(user_id, new_rank, admin_id)
            
            return True
            
        except Exception as e:
            logger.error(f"Error adding XP admin for user {user_id}: {e}")
            return False
    
    async def remove_xp_admin(self, user_id: int, xp_amount: int, admin_id: int) -> bool:
        """Remove XP from user (admin command)"""
        try:
            success = await self.db.remove_xp(user_id, xp_amount, "Административное снятие", admin_id)
            if not success:
                return False
            
            # Update rank if necessary
            user = await self.db.get_user(user_id)
            if user and user['rank'] not in self.special_ranks:
                new_rank = self.calculate_rank_from_xp(user['xp'])
                if new_rank != user['rank']:
                    await self.db.set_rank(user_id, new_rank, admin_id)
            
            return True
            
        except Exception as e:
            logger.error(f"Error removing XP admin for user {user_id}: {e}")
            return False
    
    async def set_rank_admin(self, user_id: int, rank: str, admin_id: int) -> bool:
        """Set user rank (admin command)"""
        try:
            return await self.db.set_rank(user_id, rank, admin_id)
        except Exception as e:
            logger.error(f"Error setting rank admin for user {user_id}: {e}")
            return False
    
    async def reset_xp_admin(self, user_id: int, admin_id: int) -> bool:
        """Reset user XP (admin command)"""
        try:
            return await self.db.reset_xp(user_id, admin_id)
        except Exception as e:
            logger.error(f"Error resetting XP admin for user {user_id}: {e}")
            return False
    
    async def get_top_users(self, limit: int = 10) -> List[Dict]:
        """Get top users for leaderboard"""
        try:
            return await self.db.get_top_users(limit)
        except Exception as e:
            logger.error(f"Error getting top users: {e}")
            return []
    
    def get_rank_list(self) -> str:
        """Get formatted list of available ranks and their requirements"""
        rank_text = "<b>Доступні ранги:</b>\n\n"
        
        for threshold, rank in self.rank_thresholds:
            rank_text += f"<b>{rank}</b> - {threshold} XP\n"
        
        rank_text += "\n<b>Спеціальні ранги:</b>\n"
        rank_text += "<b>Ресейлер</b> - присвоюється адміністрацією\n"
        rank_text += "• Додатково +1 оголошення на годину\n"
        
        return rank_text
