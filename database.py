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
        """Check if user can gain XP (cooldown + daily limit)"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                cursor = await db.execute(
                    'SELECT last_xp_time, daily_xp, daily_xp_date FROM xp_users WHERE user_id = ?',
                    (user_id,)
                )
                row = await cursor.fetchone()
                
                if not row:
                    return True  # New user can gain XP
                
                last_xp_time, daily_xp, daily_xp_date = row
                
                # Check daily XP reset
                today = datetime.now().date().isoformat()
                if daily_xp_date != today:
                    # Reset daily XP
                    await db.execute(
                        'UPDATE xp_users SET daily_xp = 0, daily_xp_date = ? WHERE user_id = ?',
                        (today, user_id)
                    )
                    await db.commit()
                    daily_xp = 0
                
                # Check daily limit (100 XP per day)
                if daily_xp >= 100:
                    logger.debug(f"User {user_id} has reached daily XP limit")
                    return False
                
                # Check cooldown (60 seconds between XP gains)
                if last_xp_time:
                    last_time = datetime.fromisoformat(last_xp_time)
                    if (datetime.now() - last_time).seconds < 60:
                        logger.debug(f"User {user_id} is on XP cooldown")
                        return False
                
                return True
                
        except Exception as e:
            logger.error(f"Error checking XP eligibility for user {user_id}: {e}")
            return False
    
    async def add_xp(self, user_id: int, xp_amount: int, reason: str = "Активность в чате", admin_id: int = None) -> bool:
        """Add XP to user"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                # Update user XP
                now = datetime.now().isoformat()
                
                await db.execute('''
                    UPDATE xp_users 
                    SET xp = xp + ?, daily_xp = daily_xp + ?, last_xp_time = ?, updated_at = ?
                    WHERE user_id = ?
                ''', (xp_amount, xp_amount, now, now, user_id))
                
                # Add to history
                await db.execute('''
                    INSERT INTO xp_history (user_id, xp_change, reason, admin_id)
                    VALUES (?, ?, ?, ?)
                ''', (user_id, xp_amount, reason, admin_id))
                
                await db.commit()
                
                # Get updated total XP
                cursor = await db.execute('SELECT xp FROM xp_users WHERE user_id = ?', (user_id,))
                row = await cursor.fetchone()
                total_xp = row[0] if row else 0
                
                logger.info(f"Added {xp_amount} XP to user {user_id} (total: {total_xp})")
                return True
                
        except Exception as e:
            logger.error(f"Error adding XP to user {user_id}: {e}")
            return False
    
    async def set_xp(self, user_id: int, xp_amount: int, reason: str = "Адмін встановив XP", admin_id: int = None) -> bool:
        """Set user XP to specific amount"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                # Get current XP
                cursor = await db.execute('SELECT xp FROM xp_users WHERE user_id = ?', (user_id,))
                row = await cursor.fetchone()
                current_xp = row[0] if row else 0
                
                # Calculate change
                xp_change = xp_amount - current_xp
                
                # Update user XP
                now = datetime.now().isoformat()
                await db.execute('''
                    UPDATE xp_users 
                    SET xp = ?, updated_at = ?
                    WHERE user_id = ?
                ''', (xp_amount, now, user_id))
                
                # Add to history
                await db.execute('''
                    INSERT INTO xp_history (user_id, xp_change, reason, admin_id)
                    VALUES (?, ?, ?, ?)
                ''', (user_id, xp_change, reason, admin_id))
                
                await db.commit()
                logger.info(f"Set XP for user {user_id} to {xp_amount}")
                return True
                
        except Exception as e:
            logger.error(f"Error setting XP for user {user_id}: {e}")
            return False
    
    async def reset_xp(self, user_id: int, admin_id: int = None) -> bool:
        """Reset user XP to 0"""
        return await self.set_xp(user_id, 0, "Адмін скинув XP", admin_id)
    
    async def set_rank(self, user_id: int, rank: str, admin_id: int = None) -> bool:
        """Set custom rank for user"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                now = datetime.now().isoformat()
                await db.execute('''
                    UPDATE xp_users 
                    SET rank = ?, updated_at = ?
                    WHERE user_id = ?
                ''', (rank, now, user_id))
                
                # Add to history
                await db.execute('''
                    INSERT INTO xp_history (user_id, xp_change, reason, admin_id)
                    VALUES (?, 0, ?, ?)
                ''', (user_id, f"Встановлено ранг: {rank}", admin_id))
                
                await db.commit()
                logger.info(f"Set rank {rank} for user {user_id}")
                return True
                
        except Exception as e:
            logger.error(f"Error setting rank for user {user_id}: {e}")
            return False
    
    async def get_leaderboard(self, limit: int = 10) -> List[dict]:
        """Get top users by XP"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                cursor = await db.execute('''
                    SELECT user_id, username, first_name, xp, rank
                    FROM xp_users 
                    WHERE xp > 0
                    ORDER BY xp DESC 
                    LIMIT ?
                ''', (limit,))
                
                rows = await cursor.fetchall()
                columns = [description[0] for description in cursor.description]
                
                return [dict(zip(columns, row)) for row in rows]
                
        except Exception as e:
            logger.error(f"Error getting leaderboard: {e}")
            return []
    
    async def get_user_rank_position(self, user_id: int) -> Optional[int]:
        """Get user's position in leaderboard"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                cursor = await db.execute('''
                    SELECT COUNT(*) + 1 as position
                    FROM xp_users u1, xp_users u2
                    WHERE u1.user_id = ? AND u2.xp > u1.xp
                ''', (user_id,))
                
                row = await cursor.fetchone()
                return row[0] if row else None
                
        except Exception as e:
            logger.error(f"Error getting rank position for user {user_id}: {e}")
            return None
