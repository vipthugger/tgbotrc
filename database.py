import sqlite3
import logging
from datetime import datetime, timedelta
from typing import Optional, List, Tuple
import asyncio
import aiosqlite

logger = logging.getLogger(__name__)

class XPDatabase:
    def __init__(self, db_path: str = "resale_bot.db"):
        self.db_path = db_path
        self.init_db()
    
    def init_db(self):
        """Initialize the database with XP tables"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                # Create XP users table
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS xp_users (
                        user_id INTEGER PRIMARY KEY,
                        username TEXT,
                        first_name TEXT,
                        xp INTEGER DEFAULT 0,
                        rank TEXT DEFAULT 'Новачок',
                        last_xp_time TEXT,
                        daily_xp INTEGER DEFAULT 0,
                        daily_xp_date TEXT,
                        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                        updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
                
                # Create XP history table for tracking XP changes
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS xp_history (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER,
                        xp_change INTEGER,
                        reason TEXT,
                        admin_id INTEGER,
                        timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (user_id) REFERENCES xp_users (user_id)
                    )
                ''')
                
                # Create index for faster queries
                cursor.execute('CREATE INDEX IF NOT EXISTS idx_xp_users_xp ON xp_users (xp DESC)')
                cursor.execute('CREATE INDEX IF NOT EXISTS idx_xp_history_user_id ON xp_history (user_id)')
                
                conn.commit()
                logger.info("XP database initialized successfully")
                
        except Exception as e:
            logger.error(f"Error initializing XP database: {e}")
    
    async def get_user(self, user_id: int) -> Optional[dict]:
        """Get user XP data"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                cursor = await db.execute(
                    'SELECT * FROM xp_users WHERE user_id = ?',
                    (user_id,)
                )
                row = await cursor.fetchone()
                
                if row:
                    columns = [description[0] for description in cursor.description]
                    return dict(zip(columns, row))
                return None
                
        except Exception as e:
            logger.error(f"Error getting user {user_id}: {e}")
            return None
    
    async def create_or_update_user(self, user_id: int, username: str | None = None, first_name: str | None = None) -> bool:
        """Create or update user in XP system"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                # First, try to insert new user (ignore if already exists)
                await db.execute('''
                    INSERT OR IGNORE INTO xp_users (user_id, username, first_name, xp, rank, daily_xp_date, created_at)
                    VALUES (?, ?, ?, 0, 'Новачок', ?, ?)
                ''', (user_id, username, first_name, datetime.now().date().isoformat(), datetime.now().isoformat()))
                
                # Then, always update the existing user info (in case username/first_name changed)
                await db.execute('''
                    UPDATE xp_users 
                    SET username = ?, first_name = ?, updated_at = ?
                    WHERE user_id = ?
                ''', (username, first_name, datetime.now().isoformat(), user_id))
                
                await db.commit()
                return True
                
        except Exception as e:
            logger.error(f"Error creating/updating user {user_id}: {e}")
            return False
    
    async def can_gain_xp(self, user_id: int) -> bool:
        """Check if user can gain XP (cooldown and daily limit check)"""
        try:
            user = await self.get_user(user_id)
            if not user:
                return True  # New user can gain XP
            
            # Check cooldown (60 seconds)
            if user['last_xp_time']:
                last_xp = datetime.fromisoformat(user['last_xp_time'])
                if datetime.now() - last_xp < timedelta(seconds=60):
                    return False
            
            # Check daily limit (100 XP)
            today = datetime.now().date().isoformat()
            if user['daily_xp_date'] == today and user['daily_xp'] >= 100:
                return False
            
            return True
            
        except Exception as e:
            logger.error(f"Error checking XP cooldown for user {user_id}: {e}")
            return False
    
    async def add_xp(self, user_id: int, xp_amount: int, reason: str = "Сообщение в чате", admin_id: int | None = None) -> bool:
        """Add XP to user"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                user = await self.get_user(user_id)
                if not user:
                    return False
                
                today = datetime.now().date().isoformat()
                current_daily_xp = user['daily_xp'] if user['daily_xp_date'] == today else 0
                
                # Reset daily XP if it's a new day
                if user['daily_xp_date'] != today:
                    current_daily_xp = 0
                
                # Check daily limit
                if current_daily_xp + xp_amount > 100 and admin_id is None:
                    xp_amount = max(0, 100 - current_daily_xp)
                
                if xp_amount <= 0:
                    return False
                
                new_xp = user['xp'] + xp_amount
                new_daily_xp = current_daily_xp + xp_amount
                
                # Update user XP
                await db.execute('''
                    UPDATE xp_users 
                    SET xp = ?, daily_xp = ?, daily_xp_date = ?, last_xp_time = ?, updated_at = ?
                    WHERE user_id = ?
                ''', (new_xp, new_daily_xp, today, datetime.now().isoformat(), datetime.now().isoformat(), user_id))
                
                # Log XP change
                await db.execute('''
                    INSERT INTO xp_history (user_id, xp_change, reason, admin_id)
                    VALUES (?, ?, ?, ?)
                ''', (user_id, xp_amount, reason, admin_id))
                
                await db.commit()
                logger.info(f"Added {xp_amount} XP to user {user_id} (total: {new_xp})")
                return True
                
        except Exception as e:
            logger.error(f"Error adding XP to user {user_id}: {e}")
            return False
    
    async def remove_xp(self, user_id: int, xp_amount: int, reason: str = "Административное снятие", admin_id: int | None = None) -> bool:
        """Remove XP from user"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                user = await self.get_user(user_id)
                if not user:
                    return False
                
                new_xp = max(0, user['xp'] - xp_amount)
                
                # Update user XP
                await db.execute('''
                    UPDATE xp_users 
                    SET xp = ?, updated_at = ?
                    WHERE user_id = ?
                ''', (new_xp, datetime.now().isoformat(), user_id))
                
                # Log XP change
                await db.execute('''
                    INSERT INTO xp_history (user_id, xp_change, reason, admin_id)
                    VALUES (?, ?, ?, ?)
                ''', (user_id, -xp_amount, reason, admin_id))
                
                await db.commit()
                logger.info(f"Removed {xp_amount} XP from user {user_id} (total: {new_xp})")
                return True
                
        except Exception as e:
            logger.error(f"Error removing XP from user {user_id}: {e}")
            return False
    
    async def set_rank(self, user_id: int, rank: str, admin_id: int | None = None) -> bool:
        """Set user rank"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute('''
                    UPDATE xp_users 
                    SET rank = ?, updated_at = ?
                    WHERE user_id = ?
                ''', (rank, datetime.now().isoformat(), user_id))
                
                # Log rank change
                await db.execute('''
                    INSERT INTO xp_history (user_id, xp_change, reason, admin_id)
                    VALUES (?, ?, ?, ?)
                ''', (user_id, 0, f"Ранг изменен на: {rank}", admin_id))
                
                await db.commit()
                logger.info(f"Set rank {rank} for user {user_id}")
                return True
                
        except Exception as e:
            logger.error(f"Error setting rank for user {user_id}: {e}")
            return False
    
    async def reset_xp(self, user_id: int, admin_id: int | None = None) -> bool:
        """Reset user XP to 0"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute('''
                    UPDATE xp_users 
                    SET xp = 0, rank = 'Новачок', daily_xp = 0, updated_at = ?
                    WHERE user_id = ?
                ''', (datetime.now().isoformat(), user_id))
                
                # Log XP reset
                await db.execute('''
                    INSERT INTO xp_history (user_id, xp_change, reason, admin_id)
                    VALUES (?, ?, ?, ?)
                ''', (user_id, 0, "XP сброшен", admin_id))
                
                await db.commit()
                logger.info(f"Reset XP for user {user_id}")
                return True
                
        except Exception as e:
            logger.error(f"Error resetting XP for user {user_id}: {e}")
            return False
    
    async def get_top_users(self, limit: int = 10) -> List[dict]:
        """Get top users by XP"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                cursor = await db.execute('''
                    SELECT user_id, username, first_name, xp, rank 
                    FROM xp_users 
                    ORDER BY xp DESC 
                    LIMIT ?
                ''', (limit,))
                
                rows = await cursor.fetchall()
                columns = [description[0] for description in cursor.description]
                return [dict(zip(columns, row)) for row in rows]
                
        except Exception as e:
            logger.error(f"Error getting top users: {e}")
            return []
