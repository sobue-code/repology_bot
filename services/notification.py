"""Notification service."""
import logging
from datetime import datetime
from typing import List

from aiogram import Bot

from core.database import Database
from services.package_checker import PackageChecker
from utils.formatting import format_package_list
from bot import keyboards

logger = logging.getLogger(__name__)


class NotificationService:
    """Service for sending notifications."""
    
    def __init__(
        self,
        bot: Bot,
        db: Database,
        package_checker: PackageChecker,
        max_packages: int = 20
    ):
        """
        Initialize notification service.
        
        Args:
            bot: Bot instance
            db: Database instance
            package_checker: PackageChecker instance
            max_packages: Maximum packages per message
        """
        self.bot = bot
        self.db = db
        self.package_checker = package_checker
        self.max_packages = max_packages
    
    async def send_scheduled_notifications(self):
        """Send notifications to all users with active subscriptions."""
        # Get all active subscriptions
        subs = await self.db.fetchall("""
            SELECT s.*, u.telegram_id, u.name
            FROM subscriptions s
            JOIN users u ON s.user_id = u.id
            WHERE s.enabled = 1 AND u.enabled = 1
        """)
        
        logger.info(f"Processing {len(subs)} active subscriptions")
        
        for sub in subs:
            try:
                await self.send_notification_to_user(
                    user_id=sub['user_id'],
                    telegram_id=sub['telegram_id'],
                    subscription_id=sub['id']
                )
            except Exception as e:
                logger.error(
                    f"Failed to send notification to user {sub['user_id']}: {e}",
                    exc_info=True
                )
    
    async def send_notification_to_user(
        self,
        user_id: int,
        telegram_id: int,
        subscription_id: int
    ):
        """
        Send notification to a specific user.
        
        Args:
            user_id: User ID in database
            telegram_id: Telegram user ID
            subscription_id: Subscription ID
        """
        logger.info(f"Sending notification to user {user_id} (telegram: {telegram_id})")
        
        # Get user emails
        emails = await self.db.get_user_emails(user_id)
        
        if not emails:
            logger.warning(f"User {user_id} has no emails configured")
            return
        
        total_outdated = 0
        
        # Check packages for each email
        for email in emails:
            try:
                packages = await self.package_checker.get_outdated_packages(
                    email,
                    force_refresh=True
                )
                
                if packages:
                    total_outdated += len(packages)

                    # Format with pagination (first page only for notifications)
                    text, total_pages = format_package_list(
                        packages,
                        email,
                        page=0,
                        per_page=self.max_packages,
                        show_all_statuses=False
                    )

                    # Send first page
                    await self.bot.send_message(telegram_id, text)

                    # If there are more pages, send a note
                    if total_pages > 1:
                        await self.bot.send_message(
                            telegram_id,
                            f"ℹ️ Показаны первые {self.max_packages} пакетов. "
                            f"Всего страниц: {total_pages}. "
                            f"Используйте /check для полного просмотра."
                        )
            
            except Exception as e:
                logger.error(f"Error checking packages for {email}: {e}", exc_info=True)
                await self.bot.send_message(
                    telegram_id,
                    f"❌ Ошибка при проверке {email}: {str(e)}"
                )
        
        # Send summary
        if total_outdated == 0:
            await self.bot.send_message(
                telegram_id,
                "✅ Все пакеты актуальны!",
                reply_markup=keyboards.back_to_menu_keyboard()
            )
        else:
            await self.bot.send_message(
                telegram_id,
                f"📊 Всего найдено {total_outdated} outdated пакетов",
                reply_markup=keyboards.back_to_menu_keyboard()
            )
        
        # Update subscription last_notification time
        await self.db.execute("""
            UPDATE subscriptions
            SET last_notification = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (subscription_id,))
        
        # Log to notification history
        await self.db.execute("""
            INSERT INTO notification_history
            (user_id, packages_count, notification_type)
            VALUES (?, ?, 'scheduled')
        """, (user_id, total_outdated))
        
        logger.info(f"Notification sent to user {user_id}: {total_outdated} outdated packages")
