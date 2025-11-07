"""Main entry point for Repology Bot."""
import asyncio
import logging
import signal
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from core.config import load_config
from core.logger import setup_logging
from core.database import init_database, get_db
from core.scheduler import NotificationScheduler
from services.repology import RepologyClient
from services.rdb import RDBClient
from services.package_checker import PackageChecker
from services.notification import NotificationService
from bot.middleware import AuthMiddleware, LoggingMiddleware
from bot import handlers
from bot import subscription_handlers


class RepologyBot:
    """Main bot application."""

    def __init__(self):
        """Initialize bot application."""
        self.config = load_config()

        # Setup logging first, before any other loggers are created
        setup_logging(self.config.logging)
        self.logger = logging.getLogger(__name__)
        
        self.bot: Bot = None
        self.dp: Dispatcher = None
        self.db = None
        self.repology_client: RepologyClient = None
        self.rdb_client: RDBClient = None
        self.package_checker: PackageChecker = None
        self.notification_service: NotificationService = None
        self.scheduler: NotificationScheduler = None

        self.shutdown_event = asyncio.Event()
    
    async def setup(self):
        """Setup bot components."""
        self.logger.info("Starting Repology Bot...")

        # Initialize database
        self.logger.info("Initializing database...")
        self.db = await init_database(self.config.database.path)

        # Sync users from config
        await self.db.sync_users_from_config(self.config.users)

        # Initialize Repology client
        self.logger.info("Initializing Repology client...")
        self.repology_client = RepologyClient(self.config.repology)
        await self.repology_client.start()

        # Initialize RDB client
        self.logger.info("Initializing RDB client...")
        self.rdb_client = RDBClient(
            base_url=self.config.rdb.api_base_url,
            timeout=self.config.rdb.request_timeout
        )

        # Initialize package checker
        self.logger.info("Initializing package checker...")
        self.package_checker = PackageChecker(
            self.db,
            self.repology_client,
            rdb=self.rdb_client,
            rdb_mapping=self.config.rdb.maintainer_mapping,
            cache_hours=self.config.repology.cache_duration
        )

        # Initialize bot
        self.logger.info("Initializing bot...")
        self.bot = Bot(
            token=self.config.bot.token,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML)
        )

        # Initialize dispatcher
        self.dp = Dispatcher()

        # Setup middleware
        self.dp.message.middleware(LoggingMiddleware())
        self.dp.callback_query.middleware(LoggingMiddleware())
        self.dp.message.middleware(AuthMiddleware(self.db))
        self.dp.callback_query.middleware(AuthMiddleware(self.db))

        # Register routers
        self.dp.include_router(handlers.router)
        self.dp.include_router(subscription_handlers.router)

        # Setup dependency injection
        self.dp['db'] = self.db
        self.dp['package_checker'] = self.package_checker
        self.dp['config'] = self.config

        # Initialize notification service
        self.logger.info("Initializing notification service...")
        self.notification_service = NotificationService(
            self.bot,
            self.db,
            self.package_checker,
            max_packages=self.config.notifications.max_packages_per_message
        )

        # Initialize scheduler
        self.logger.info("Initializing scheduler...")
        self.scheduler = NotificationScheduler(
            self.db,
            self.notification_service
        )
        await self.scheduler.start()

        self.logger.info("Bot setup completed successfully!")
    
    async def start(self):
        """Start the bot."""
        try:
            await self.setup()
            
            # Setup signal handlers
            loop = asyncio.get_event_loop()
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(
                    sig,
                    lambda: asyncio.create_task(self.shutdown())
                )
            
            self.logger.info("Bot is running. Press Ctrl+C to stop.")

            # Start polling
            await self.dp.start_polling(self.bot)

        except Exception as e:
            self.logger.error(f"Error starting bot: {e}", exc_info=True)
            await self.shutdown()
            sys.exit(1)

    async def shutdown(self):
        """Graceful shutdown."""
        if self.shutdown_event.is_set():
            return

        self.shutdown_event.set()
        self.logger.info("Shutting down bot...")

        # Stop scheduler
        if self.scheduler:
            await self.scheduler.stop()

        # Stop polling
        if self.dp:
            await self.dp.stop_polling()

        # Close bot session
        if self.bot:
            await self.bot.session.close()

        # Close Repology client
        if self.repology_client:
            await self.repology_client.close()

        # Close RDB client
        if self.rdb_client:
            await self.rdb_client.close()

        # Close database
        if self.db:
            await self.db.disconnect()

        self.logger.info("Bot stopped")


async def main():
    """Main entry point."""
    bot = RepologyBot()
    await bot.start()


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nBot stopped by user")
    except Exception as e:
        print(f"Fatal error: {e}")
        sys.exit(1)
