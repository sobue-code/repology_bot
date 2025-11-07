"""Main entry point for Repology Bot."""
import asyncio
import logging
import signal
import sys
from typing import Optional

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
from bot import maintainer_handlers
from bot import search_handlers


class RepologyBot:
    """Main bot application."""

    def __init__(self):
        """Initialize bot application."""
        self.config = load_config()

        # Setup logging first, before any other loggers are created
        setup_logging(self.config.logging)
        self.logger = logging.getLogger(__name__)

        self.bot: Optional[Bot] = None
        self.dp: Optional[Dispatcher] = None
        self.db = None
        self.repology_client: Optional[RepologyClient] = None
        self.rdb_client: Optional[RDBClient] = None
        self.package_checker: Optional[PackageChecker] = None
        self.notification_service: Optional[NotificationService] = None
        self.scheduler: Optional[NotificationScheduler] = None

        self.shutdown_event = asyncio.Event()
        self.polling_task: Optional[asyncio.Task] = None
    
    async def setup(self):
        """Setup bot components."""
        self.logger.info("Starting Repology Bot...")

        # Initialize database
        self.logger.info("Initializing database...")
        self.db = await init_database(self.config.database.path)

        # Note: User registration is now fully dynamic
        # Users are automatically created when they first interact with the bot
        self.logger.info("Using dynamic user registration mode")

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
        self.dp.include_router(maintainer_handlers.router)
        self.dp.include_router(search_handlers.router)

        # Setup dependency injection
        self.dp['db'] = self.db
        self.dp['package_checker'] = self.package_checker
        self.dp['rdb_client'] = self.rdb_client
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

            # Setup signal handlers that actually work
            loop = asyncio.get_event_loop()

            def signal_handler(signum, frame):
                self.logger.warning(f"Received signal {signum}")
                if not self.shutdown_event.is_set():
                    self.shutdown_event.set()
                    # Cancel polling task
                    if self.polling_task and not self.polling_task.done():
                        self.polling_task.cancel()

            for sig in (signal.SIGINT, signal.SIGTERM):
                signal.signal(sig, signal_handler)

            self.logger.info("Bot is running. Press Ctrl+C to stop.")

            # Start polling as a task so we can cancel it
            self.polling_task = asyncio.create_task(
                self.dp.start_polling(self.bot, handle_signals=False)
            )

            try:
                await self.polling_task
            except asyncio.CancelledError:
                self.logger.info("Polling cancelled by signal")

        except Exception as e:
            self.logger.error(f"Error starting bot: {e}", exc_info=True)
        finally:
            await self.shutdown()

    async def shutdown(self):
        """Graceful shutdown."""
        # Use a local flag to prevent re-entry
        if hasattr(self, '_shutting_down'):
            return
        self._shutting_down = True

        self.logger.info("Shutting down bot...")

        try:
            # Cancel polling task first
            if self.polling_task and not self.polling_task.done():
                self.logger.info("Cancelling polling task...")
                self.polling_task.cancel()
                try:
                    await asyncio.wait_for(self.polling_task, timeout=1.0)
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    pass

            # Stop scheduler
            if self.scheduler:
                self.logger.info("Stopping scheduler...")
                try:
                    await asyncio.wait_for(self.scheduler.stop(), timeout=1.0)
                except asyncio.TimeoutError:
                    self.logger.warning("Scheduler stop timeout")

            # Close all sessions concurrently with short timeout
            close_tasks = []

            if self.bot and hasattr(self.bot, 'session') and self.bot.session:
                close_tasks.append(self.bot.session.close())

            if self.repology_client:
                close_tasks.append(self.repology_client.close())

            if self.rdb_client:
                close_tasks.append(self.rdb_client.close())

            if self.db:
                close_tasks.append(self.db.disconnect())

            if close_tasks:
                self.logger.info(f"Closing {len(close_tasks)} connections...")
                try:
                    await asyncio.wait_for(
                        asyncio.gather(*close_tasks, return_exceptions=True),
                        timeout=2.0
                    )
                except asyncio.TimeoutError:
                    self.logger.warning("Connection close timeout")

            self.logger.info("Shutdown complete")

        except Exception as e:
            self.logger.error(f"Error during shutdown: {e}", exc_info=True)


async def main():
    """Main entry point."""
    bot = RepologyBot()
    try:
        await bot.start()
    except KeyboardInterrupt:
        logging.info("Received keyboard interrupt")
    except Exception as e:
        logging.error(f"Fatal error in main: {e}", exc_info=True)
        raise
    finally:
        # Ensure we exit cleanly
        logging.info("Main coroutine completed")


if __name__ == '__main__':
    exit_code = 0
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nBot stopped by user")
    except Exception as e:
        print(f"Fatal error: {e}")
        exit_code = 1
    finally:
        # Force exit to prevent hanging
        sys.exit(exit_code)
