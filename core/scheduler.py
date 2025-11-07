"""Task scheduler for periodic notifications."""
import logging
from datetime import datetime, time as dt_time
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from core.database import Database
from services.notification import NotificationService

logger = logging.getLogger(__name__)


class NotificationScheduler:
    """Scheduler for managing notification tasks."""
    
    def __init__(self, db: Database, notification_service: NotificationService):
        """
        Initialize scheduler.
        
        Args:
            db: Database instance
            notification_service: NotificationService instance
        """
        self.db = db
        self.notification_service = notification_service
        self.scheduler: Optional[AsyncIOScheduler] = None
    
    async def start(self):
        """Start the scheduler."""
        if self.scheduler is not None:
            logger.warning("Scheduler already started")
            return
        
        self.scheduler = AsyncIOScheduler()
        
        # Add cache cleanup job (daily at 3 AM)
        self.scheduler.add_job(
            self._cleanup_cache,
            CronTrigger(hour=3, minute=0),
            id='cache_cleanup',
            name='Clean up old cache'
        )
        
        # Load and schedule user subscriptions
        await self.reload_subscriptions()
        
        self.scheduler.start()
        logger.info("Scheduler started")
    
    async def stop(self):
        """Stop the scheduler."""
        if self.scheduler is not None:
            self.scheduler.shutdown()
            self.scheduler = None
            logger.info("Scheduler stopped")
    
    async def reload_subscriptions(self):
        """Reload all active subscriptions and create jobs."""
        if self.scheduler is None:
            return
        
        # Remove all existing notification jobs
        for job in self.scheduler.get_jobs():
            if job.id.startswith('notify_user_'):
                job.remove()
        
        # Get all active subscriptions
        subs = await self.db.fetchall("""
            SELECT s.*, u.telegram_id, u.name
            FROM subscriptions s
            JOIN users u ON s.user_id = u.id
            WHERE s.enabled = 1 AND u.enabled = 1
        """)
        
        logger.info(f"Loading {len(subs)} active subscriptions")
        
        # Create jobs for each subscription
        for sub in subs:
            await self._schedule_subscription(sub)
        
        logger.info(f"Scheduled {len(subs)} notification jobs")
    
    async def _schedule_subscription(self, sub: dict):
        """
        Schedule a notification job for a subscription.
        
        Args:
            sub: Subscription row from database
        """
        user_id = sub['user_id']
        telegram_id = sub['telegram_id']
        frequency = sub['frequency']
        time_str = sub['time']
        
        # Parse time
        hour, minute = map(int, time_str.split(':'))
        
        # Create trigger based on frequency
        if frequency == 'daily':
            trigger = CronTrigger(hour=hour, minute=minute)
            schedule_desc = f"daily at {time_str}"
        
        elif frequency == 'weekly':
            day_of_week = sub['day_of_week']
            if day_of_week is None:
                logger.error(f"Weekly subscription {sub['id']} has no day_of_week")
                return
            
            trigger = CronTrigger(
                day_of_week=day_of_week,
                hour=hour,
                minute=minute
            )
            days = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
            schedule_desc = f"weekly on {days[day_of_week]} at {time_str}"
        
        else:
            logger.error(f"Unknown frequency: {frequency}")
            return
        
        # Add job
        job_id = f"notify_user_{user_id}"
        
        self.scheduler.add_job(
            self._send_user_notification,
            trigger,
            args=[user_id, telegram_id, sub['id']],
            id=job_id,
            name=f"Notify user {sub['name']} ({schedule_desc})",
            replace_existing=True
        )
        
        logger.info(f"Scheduled job {job_id}: {schedule_desc}")
    
    async def _send_user_notification(
        self,
        user_id: int,
        telegram_id: int,
        subscription_id: int
    ):
        """
        Send notification to a user (called by scheduler).
        
        Args:
            user_id: User ID in database
            telegram_id: Telegram user ID
            subscription_id: Subscription ID
        """
        logger.info(f"Scheduler triggered notification for user {user_id}")
        
        try:
            await self.notification_service.send_notification_to_user(
                user_id=user_id,
                telegram_id=telegram_id,
                subscription_id=subscription_id
            )
        except Exception as e:
            logger.error(
                f"Failed to send scheduled notification to user {user_id}: {e}",
                exc_info=True
            )
    
    async def _cleanup_cache(self):
        """Clean up old cache entries."""
        logger.info("Running cache cleanup")
        
        try:
            await self.notification_service.package_checker.cleanup_old_cache(days=7)
        except Exception as e:
            logger.error(f"Cache cleanup failed: {e}", exc_info=True)
