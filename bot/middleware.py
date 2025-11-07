"""Bot middleware for authorization and logging."""
import logging
from typing import Callable, Dict, Any, Awaitable

from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery, TelegramObject
from aiogram.exceptions import TelegramBadRequest

from core.database import Database

logger = logging.getLogger(__name__)


class AuthMiddleware(BaseMiddleware):
    """Middleware for checking user authorization."""
    
    def __init__(self, db: Database):
        """
        Initialize auth middleware.
        
        Args:
            db: Database instance
        """
        super().__init__()
        self.db = db
    
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        """
        Check if user is authorized.
        
        Args:
            handler: Next handler
            event: Update event
            data: Handler data
            
        Returns:
            Handler result or None if not authorized
        """
        # Get user from message or callback
        if isinstance(event, Message):
            telegram_id = event.from_user.id
            chat = event.chat
        elif isinstance(event, CallbackQuery):
            telegram_id = event.from_user.id
            chat = event.message.chat if event.message else None
        else:
            # For other event types, allow by default
            return await handler(event, data)
        
        # Check if user exists in database, if not - auto-register them
        user = await self.db.get_user_by_telegram_id(telegram_id)

        if user is None:
            # Auto-register new user
            logger.info(f"New user detected, auto-registering: telegram_id={telegram_id}")

            # Get user's name from Telegram
            user_name = None
            if isinstance(event, Message) and event.from_user:
                first_name = event.from_user.first_name or ""
                last_name = event.from_user.last_name or ""
                user_name = f"{first_name} {last_name}".strip() or f"User {telegram_id}"
            elif isinstance(event, CallbackQuery) and event.from_user:
                first_name = event.from_user.first_name or ""
                last_name = event.from_user.last_name or ""
                user_name = f"{first_name} {last_name}".strip() or f"User {telegram_id}"

            # Create user
            user_id = await self.db.create_user_if_not_exists(telegram_id, user_name)

            # Get user from database again
            user = await self.db.get_user_by_telegram_id(telegram_id)

            if user is None:
                logger.error(f"Failed to create user: telegram_id={telegram_id}")
                return None
        
        if not user['enabled']:
            logger.warning(f"Access attempt from disabled user {telegram_id}")
            
            # Send disabled message
            if isinstance(event, Message):
                await event.answer(
                    "❌ Ваш аккаунт отключен.\n"
                    "Обратитесь к администратору."
                )
            elif isinstance(event, CallbackQuery):
                try:
                    await event.answer(
                        "❌ Ваш аккаунт отключен.",
                        show_alert=True
                    )
                except TelegramBadRequest:
                    pass  # Ignore timeout errors
            
            return None
        
        # Add user data to handler data
        data['user'] = user
        data['user_id'] = user['id']
        data['telegram_id'] = telegram_id
        
        # Log access
        logger.debug(f"User {user['name']} ({telegram_id}) accessed {event.__class__.__name__}")
        
        return await handler(event, data)


class LoggingMiddleware(BaseMiddleware):
    """Middleware for logging all updates."""
    
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        """
        Log update information.
        
        Args:
            handler: Next handler
            event: Update event
            data: Handler data
            
        Returns:
            Handler result
        """
        if isinstance(event, Message):
            logger.info(
                f"Message from {event.from_user.id}: {event.text[:50] if event.text else '[no text]'}"
            )
        elif isinstance(event, CallbackQuery):
            logger.info(
                f"Callback from {event.from_user.id}: {event.data}"
            )
        
        return await handler(event, data)
