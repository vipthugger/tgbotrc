import time
import logging
from datetime import datetime, timedelta
from typing import Dict, Optional

logger = logging.getLogger(__name__)

class CooldownManager:
    """Manages cooldowns for user posts in different categories"""
    
    def __init__(self, cooldown_seconds: int = 43200):  # 12 hours default
        self.cooldown_seconds = cooldown_seconds
        self.user_cooldowns: Dict[int, Dict[str, float]] = {}  # user_id -> {category: timestamp}
        self.reseller_posts: Dict[int, Dict[str, int]] = {}  # user_id -> {category: post_count}
    
    def is_on_cooldown(self, user_id: int, category: str, user_rank: str = None) -> bool:
        """Check if user is on cooldown for the given category"""
        try:
            if user_id not in self.user_cooldowns:
                return False
            
            if category not in self.user_cooldowns[user_id]:
                return False
            
            last_post_time = self.user_cooldowns[user_id][category]
            current_time = time.time()
            
            time_since_last_post = current_time - last_post_time
            
            # Check if cooldown period has expired
            if time_since_last_post >= self.cooldown_seconds:
                return False  # Cooldown expired
            
            # Check if Reseller has available posts within cooldown period
            if user_rank == "Ресейлер":
                posts_made = self.reseller_posts.get(user_id, {}).get(category, 0)
                if posts_made < 2:
                    return False  # Reseller can still make posts (up to 2 total)
            
            # Regular cooldown check - on cooldown
            remaining_time = self.cooldown_seconds - time_since_last_post
            logger.info(f"User {user_id} on cooldown for {category}, {remaining_time:.0f} seconds remaining")
            return True
            
        except Exception as e:
            logger.error(f"Error checking cooldown for user {user_id}, category {category}: {e}")
            return False
    
    def record_successful_post(self, user_id: int, category: str, user_rank: str = None) -> None:
        """Record a successful post and start cooldown timer"""
        try:
            current_time = time.time()
            
            if user_id not in self.user_cooldowns:
                self.user_cooldowns[user_id] = {}
            
            # For first post or after cooldown expired, reset timestamp
            if category not in self.user_cooldowns[user_id] or \
               (current_time - self.user_cooldowns[user_id][category]) >= self.cooldown_seconds:
                self.user_cooldowns[user_id][category] = current_time
                # Reset reseller post counter
                if user_id in self.reseller_posts and category in self.reseller_posts[user_id]:
                    del self.reseller_posts[user_id][category]
            
            # Track reseller posts
            if user_rank == "Ресейлер":
                if user_id not in self.reseller_posts:
                    self.reseller_posts[user_id] = {}
                if category not in self.reseller_posts[user_id]:
                    self.reseller_posts[user_id][category] = 0
                self.reseller_posts[user_id][category] += 1
                logger.info(f"Reseller post {self.reseller_posts[user_id][category]}/2 recorded for user {user_id}, category {category}")
            
            logger.info(f"Post recorded for user {user_id}, category {category}")
            
        except Exception as e:
            logger.error(f"Error recording post for user {user_id}, category {category}: {e}")
    
    def reset_cooldown(self, user_id: int, category: str = 'all') -> bool:
        """Reset cooldown for user in specific category or all categories"""
        try:
            if user_id not in self.user_cooldowns:
                return False
            
            if category == 'all':
                # Reset all cooldowns for the user
                self.user_cooldowns[user_id] = {}
                # Reset reseller post counts
                if user_id in self.reseller_posts:
                    self.reseller_posts[user_id] = {}
                logger.info(f"All cooldowns reset for user {user_id}")
                return True
            else:
                # Reset specific category
                if category in self.user_cooldowns[user_id]:
                    del self.user_cooldowns[user_id][category]
                    # Reset reseller post count for this category
                    if user_id in self.reseller_posts and category in self.reseller_posts[user_id]:
                        del self.reseller_posts[user_id][category]
                    logger.info(f"Cooldown reset for user {user_id}, category {category}")
                    return True
                else:
                    logger.info(f"No active cooldown found for user {user_id}, category {category}")
                    return False
                    
        except Exception as e:
            logger.error(f"Error resetting cooldown for user {user_id}, category {category}: {e}")
            return False
    
    def get_remaining_time(self, user_id: int, category: str) -> Optional[int]:
        """Get remaining cooldown time in seconds"""
        try:
            if not self.is_on_cooldown(user_id, category):
                return None
            
            last_post_time = self.user_cooldowns[user_id][category]
            current_time = time.time()
            time_since_last_post = current_time - last_post_time
            remaining_time = self.cooldown_seconds - time_since_last_post
            
            return max(0, int(remaining_time))
            
        except Exception as e:
            logger.error(f"Error getting remaining time for user {user_id}, category {category}: {e}")
            return None

    def get_reseller_posts_count(self, user_id: int, category: str) -> int:
        """Get number of posts made by reseller in current cooldown period"""
        try:
            return self.reseller_posts.get(user_id, {}).get(category, 0)
        except Exception as e:
            logger.error(f"Error getting reseller posts count for user {user_id}, category {category}: {e}")
            return 0
    
    def cleanup_expired_cooldowns(self) -> None:
        """Clean up expired cooldowns to free memory"""
        try:
            current_time = time.time()
            users_to_remove = []
            
            for user_id, categories in self.user_cooldowns.items():
                categories_to_remove = []
                
                for category, timestamp in categories.items():
                    if current_time - timestamp >= self.cooldown_seconds:
                        categories_to_remove.append(category)
                
                # Remove expired categories
                for category in categories_to_remove:
                    del categories[category]
                
                # Mark user for removal if no active cooldowns
                if not categories:
                    users_to_remove.append(user_id)
            
            # Remove users with no active cooldowns
            for user_id in users_to_remove:
                del self.user_cooldowns[user_id]
            
            if users_to_remove or any(len(categories) > 0 for categories in self.user_cooldowns.values()):
                logger.info(f"Cleanup completed. Removed {len(users_to_remove)} inactive users")
                
        except Exception as e:
            logger.error(f"Error during cooldown cleanup: {e}")
