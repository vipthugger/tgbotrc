import time
import json
import os
import logging
import re
from typing import Dict, Tuple, Optional, Union

class CooldownManager:
    """
    Manages the cooldown system for the Telegram bot.
    Tracks user posting in buy/sell categories and enforces cooldown periods.
    """
    def __init__(self, cooldown_seconds: int = 3600, storage_file: str = "cooldown_data.json"):
        """
        Initialize the cooldown manager.
        
        Args:
            cooldown_seconds: Cooldown time in seconds (default: 3600 = 1 hour)
            storage_file: File to persist cooldown data across bot restarts
        """
        self.cooldown_seconds = cooldown_seconds
        self.storage_file = storage_file
        self.user_cooldowns = {}  # {user_id: {'buy': timestamp, 'sell': timestamp}}
        self.temporary_attempts = {}  # {(user_id, category): attempts_count}
        self.user_custom_cooldowns = {}  # {user_id: {'buy': custom_seconds, 'sell': custom_seconds}}
        
        # Load existing cooldown data if available
        self._load_data()
        
        logging.debug(f"CooldownManager initialized with {cooldown_seconds}s cooldown")
    
    def _load_data(self) -> None:
        """Load cooldown data from storage file if it exists."""
        try:
            if os.path.exists(self.storage_file):
                with open(self.storage_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    if isinstance(data, dict) and "cooldowns" in data:
                        self.user_cooldowns = data["cooldowns"]
                        if "custom_cooldowns" in data:
                            self.user_custom_cooldowns = data["custom_cooldowns"]
                    else:
                        # Backwards compatibility with old format
                        self.user_cooldowns = data
                    logging.debug(f"Loaded cooldown data for {len(self.user_cooldowns)} users")
        except Exception as e:
            logging.error(f"Error loading cooldown data: {e}")
            self.user_cooldowns = {}
            self.user_custom_cooldowns = {}
    
    def _save_data(self) -> None:
        """Save cooldown data to storage file."""
        try:
            data = {
                "cooldowns": self.user_cooldowns,
                "custom_cooldowns": self.user_custom_cooldowns
            }
            with open(self.storage_file, 'w', encoding='utf-8') as f:
                json.dump(data, f)
        except Exception as e:
            logging.error(f"Error saving cooldown data: {e}")
    
    def check_cooldown(self, user_id: int, category: str) -> Tuple[bool, Optional[int]]:
        """
        Check if a user is under cooldown for a specific category.
        
        Args:
            user_id: The Telegram user ID
            category: The category ('buy' or 'sell')
            
        Returns:
            Tuple[bool, Optional[int]]: (is_in_cooldown, remaining_seconds)
        """
        if str(user_id) not in self.user_cooldowns:
            return False, None
        
        if category not in self.user_cooldowns[str(user_id)]:
            return False, None
        
        last_post_time = self.user_cooldowns[str(user_id)][category]
        current_time = time.time()
        elapsed_time = current_time - last_post_time
        
        # Check if user has custom cooldown
        cooldown_value = self.get_user_cooldown_seconds(user_id, category)
        
        if elapsed_time < cooldown_value:
            remaining_time = int(cooldown_value - elapsed_time)
            return True, remaining_time
        
        return False, None
        
    def get_user_cooldown_seconds(self, user_id: int, category: str) -> int:
        """
        Get the cooldown seconds for a specific user and category.
        If user has custom cooldown, return that, otherwise return default.
        
        Args:
            user_id: The Telegram user ID
            category: The category ('buy' or 'sell')
            
        Returns:
            int: Cooldown time in seconds
        """
        user_id_str = str(user_id)
        if user_id_str in self.user_custom_cooldowns and category in self.user_custom_cooldowns[user_id_str]:
            return self.user_custom_cooldowns[user_id_str][category]
        return self.cooldown_seconds
    
    def record_successful_post(self, user_id: int, category: str) -> None:
        """
        Record a successful post, activating the cooldown for a user in a category.
        
        Args:
            user_id: The Telegram user ID
            category: The category ('buy' or 'sell')
        """
        # Initialize user entry if not exists
        if str(user_id) not in self.user_cooldowns:
            self.user_cooldowns[str(user_id)] = {}
        
        # Record current timestamp
        self.user_cooldowns[str(user_id)][category] = time.time()
        
        # Remove any temporary attempts
        key = (user_id, category)
        if key in self.temporary_attempts:
            del self.temporary_attempts[key]
        
        # Save data to file
        self._save_data()
        
        logging.debug(f"Recorded successful post for user {user_id} in category {category}")
    
    def record_attempt(self, user_id: int, category: str) -> int:
        """
        Record a post attempt (used for tracking retries).
        
        Args:
            user_id: The Telegram user ID
            category: The category ('buy' or 'sell')
            
        Returns:
            int: The number of attempts made
        """
        key = (user_id, category)
        if key not in self.temporary_attempts:
            self.temporary_attempts[key] = 0
        
        self.temporary_attempts[key] += 1
        return self.temporary_attempts[key]
    
    def get_attempts(self, user_id: int, category: str) -> int:
        """
        Get the number of attempts a user has made.
        
        Args:
            user_id: The Telegram user ID
            category: The category ('buy' or 'sell')
            
        Returns:
            int: The number of attempts made
        """
        key = (user_id, category)
        return self.temporary_attempts.get(key, 0)
    
    def reset_attempts(self, user_id: int, category: str) -> None:
        """
        Reset the attempt counter for a user in a category.
        
        Args:
            user_id: The Telegram user ID
            category: The category ('buy' or 'sell')
        """
        key = (user_id, category)
        if key in self.temporary_attempts:
            del self.temporary_attempts[key]
    
    def format_remaining_time(self, seconds: int) -> str:
        """
        Format remaining cooldown time into a human-readable string.
        
        Args:
            seconds: Remaining cooldown time in seconds
            
        Returns:
            str: Formatted time string (e.g., "45 minutes and 30 seconds")
        """
        minutes, seconds = divmod(seconds, 60)
        hours, minutes = divmod(minutes, 60)
        
        if hours > 0:
            return f"{hours} год{'ин' if hours > 1 else 'ина'} та {minutes} хвилин"
        elif minutes > 0:
            return f"{minutes} хвилин{'а' if minutes == 1 else ''} та {seconds} секунд"
        else:
            return f"{seconds} секунд"
            
    def reset_cooldown(self, user_id: int, category: str = 'all') -> bool:
        """
        Reset the cooldown for a user in a specific category or all categories.
        
        Args:
            user_id: The Telegram user ID
            category: The category ('buy', 'sell', or 'all')
            
        Returns:
            bool: True if cooldown was reset, False otherwise
        """
        user_id_str = str(user_id)
        if user_id_str not in self.user_cooldowns:
            return False
            
        if category == 'all':
            # Reset all categories
            self.user_cooldowns.pop(user_id_str, None)
            self._save_data()
            return True
        elif category in ['buy', 'sell']:
            # Reset specific category
            if category in self.user_cooldowns[user_id_str]:
                self.user_cooldowns[user_id_str].pop(category, None)
                self._save_data()
                return True
                
        return False
        
    def set_custom_cooldown(self, user_id: int, category: str, seconds: int) -> bool:
        """
        Set a custom cooldown time for a user in a specific category.
        
        Args:
            user_id: The Telegram user ID
            category: The category ('buy', 'sell', or 'all')
            seconds: Cooldown time in seconds
            
        Returns:
            bool: True if custom cooldown was set, False otherwise
        """
        user_id_str = str(user_id)
        
        if seconds < 0:
            return False
            
        # Initialize user entry if not exists
        if user_id_str not in self.user_custom_cooldowns:
            self.user_custom_cooldowns[user_id_str] = {}
            
        if category == 'all':
            # Set for both categories
            self.user_custom_cooldowns[user_id_str]['buy'] = seconds
            self.user_custom_cooldowns[user_id_str]['sell'] = seconds
        elif category in ['buy', 'sell']:
            # Set for specific category
            self.user_custom_cooldowns[user_id_str][category] = seconds
        else:
            return False
            
        self._save_data()
        return True
        
    def parse_time_string(self, time_str: str) -> Optional[int]:
        """
        Parse a time string (e.g., '30m', '2h') into seconds.
        
        Args:
            time_str: A string with number and unit (s/m/h/d)
            
        Returns:
            Optional[int]: Time in seconds, or None if invalid format
        """
        if not time_str:
            return None
            
        match = re.match(r'^(\d+)([smhd])$', time_str.lower())
        if not match:
            return None
            
        value, unit = match.groups()
        value = int(value)
        
        if unit == 's':
            return value
        elif unit == 'm':
            return value * 60
        elif unit == 'h':
            return value * 3600
        elif unit == 'd':
            return value * 86400
            
        return None
