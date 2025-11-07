"""Telegram bot command handlers."""
import logging
from typing import Optional

from aiogram import Router, F
from aiogram.filters import Command, CommandStart
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.exceptions import TelegramBadRequest

from bot import keyboards
from core.database import Database
from services.package_checker import PackageChecker
from utils.formatting import (
    format_package_list,
    format_package_stats,
    format_user_info
)

logger = logging.getLogger(__name__)

# Create router
router = Router()


# Helper function
async def safe_answer_callback(callback: CallbackQuery, text: str = "", show_alert: bool = False):
    """Safely answer callback query, ignoring timeout errors."""
    try:
        await callback.answer(text, show_alert=show_alert)
    except TelegramBadRequest as e:
        if "query is too old" in str(e):
            logger.debug(f"Callback query too old, ignoring: {e}")
        else:
            raise


# ===== Basic Commands =====

@router.message(CommandStart())
async def cmd_start(message: Message, user: dict, user_id: int, db: Database):
    """Handle /start command."""
    name = user['name']

    # Check if user has any maintainer subscriptions
    maintainers = await db.get_user_maintainer_subscriptions(user_id)

    if not maintainers:
        # New user - show welcome message with instructions
        await message.answer(
            f"👋 Здравствуйте, {name}!\n\n"
            f"Я помогу отслеживать outdated пакеты в ALT Linux через Repology.\n\n"
            f"🚀 Для начала работы:\n"
            f"1. Нажмите '👤 Мои мантейнеры'\n"
            f"2. Добавьте nickname мантейнера из RDB\n"
            f"3. Проверяйте пакеты через '🔍 Проверить пакеты'\n\n"
            f"Используйте кнопки ниже для управления:",
            reply_markup=keyboards.main_menu_keyboard()
        )
    else:
        # Existing user
        await message.answer(
            f"👋 Здравствуйте, {name}!\n\n"
            f"Используйте кнопки ниже для управления:",
            reply_markup=keyboards.main_menu_keyboard()
        )


@router.message(Command("help"))
async def cmd_help(message: Message):
    """Handle /help command."""
    help_text = """
📖 Справка по командам:

/start - Главное меню
/help - Эта справка
/check - Проверить пакеты
/status - Мои настройки
/stats - Статистика пакетов
/subscribe - Настроить уведомления
/unsubscribe - Отключить уведомления

👤 Управление мантейнерами:
• Добавляйте мантейнеров по nickname из RDB
• Email формируется автоматически: nickname@altlinux.org
• Используйте кнопку "Мои мантейнеры" в меню

🔘 Используйте кнопки меню для удобной навигации!
"""
    await message.answer(help_text)


@router.message(Command("status"))
@router.callback_query(F.data == "status")
async def cmd_status(event: Message | CallbackQuery, user: dict, user_id: int, db: Database):
    """Handle /status command or callback."""
    # Get user emails (combined from old and new systems)
    emails = await db.get_user_emails(user_id)

    # Get maintainer subscriptions
    maintainers = await db.get_user_maintainer_subscriptions(user_id)

    # Get subscription info
    sub_row = await db.fetchone(
        "SELECT * FROM subscriptions WHERE user_id = ? AND enabled = 1",
        (user_id,)
    )

    # Format user info
    info = format_user_info(user['name'], user['telegram_id'], emails)

    # Add maintainer subscriptions info
    if maintainers:
        info += f"\n\n👤 Подписки на мантейнеров ({len(maintainers)}):"
        for maint in maintainers[:5]:  # Show first 5
            info += f"\n  • {maint['nickname']}"
        if len(maintainers) > 5:
            info += f"\n  ... и еще {len(maintainers) - 5}"

    # Add subscription info
    if sub_row:
        from models.user import Subscription
        sub = Subscription(
            id=sub_row['id'],
            user_id=sub_row['user_id'],
            frequency=sub_row['frequency'],
            time=sub_row['time'],
            day_of_week=sub_row['day_of_week'],
            enabled=sub_row['enabled'],
            last_notification=sub_row['last_notification'],
            created_at=sub_row['created_at']
        )
        info += f"\n\n🔔 Подписка: {sub.description}"

        if sub.last_notification:
            from utils.formatting import format_datetime
            info += f"\nПоследнее уведомление: {format_datetime(sub.last_notification)}"
    else:
        info += "\n\n🔕 Автоматические уведомления отключены"

    if isinstance(event, Message):
        await event.answer(info, reply_markup=keyboards.back_to_menu_keyboard())
    else:
        await event.message.edit_text(info, reply_markup=keyboards.back_to_menu_keyboard())
        await safe_answer_callback(event)


# ===== Check Commands =====

@router.message(Command("check"))
@router.callback_query(F.data == "check")
async def cmd_check(event: Message | CallbackQuery, user_id: int, db: Database):
    """Handle /check command or callback."""
    # Get user emails
    emails = await db.get_user_emails(user_id)

    if not emails:
        # User has no subscriptions yet
        text = (
            "📧 У вас пока нет подписок на мантейнеров.\n\n"
            "Чтобы начать отслеживать пакеты:\n"
            "1. Нажмите '👤 Мои мантейнеры'\n"
            "2. Добавьте nickname мантейнера"
        )
        keyboard = keyboards.back_to_menu_keyboard()
    else:
        text = "📧 Выберите email для проверки:"
        keyboard = keyboards.email_selection_keyboard(emails, prefix="check")

    if isinstance(event, Message):
        await event.answer(text, reply_markup=keyboard)
    else:
        await event.message.edit_text(text, reply_markup=keyboard)
        await safe_answer_callback(event)


@router.callback_query(F.data.startswith("check:"))
async def callback_check_email(
    callback: CallbackQuery,
    user_id: int,
    db: Database,
    package_checker: PackageChecker
):
    """Handle email selection for checking."""
    email = callback.data.split(":", 1)[1]
    
    if email == "all":
        # Check all emails
        emails = await db.get_user_emails(user_id)
        await callback.message.edit_text("🔄 Проверяю все email...")

        for i, email_addr in enumerate(emails):
            # Добавляем кнопку только к последнему email
            is_last = (i == len(emails) - 1)
            await send_package_check(
                callback.message,
                email_addr,
                package_checker,
                only_outdated=True,
                add_keyboard=is_last
            )

        await safe_answer_callback(callback, "✅ Проверка завершена!")
    else:
        # Show options for single email
        keyboard = keyboards.check_options_keyboard(email)
        await callback.message.edit_text(
            f"📧 Проверка: {email}\n\nВыберите действие:",
            reply_markup=keyboard
        )
        await safe_answer_callback(callback)


@router.callback_query(F.data.startswith("check_outdated:"))
async def callback_check_outdated(
    callback: CallbackQuery,
    package_checker: PackageChecker
):
    """Check only outdated packages."""
    email = callback.data.split(":", 1)[1]
    
    await callback.message.edit_text(f"🔄 Проверяю outdated пакеты для {email}...")
    await safe_answer_callback(callback)
    
    await send_package_check(callback.message, email, package_checker, only_outdated=True)


@router.callback_query(F.data.startswith("check_all:"))
async def callback_check_all(
    callback: CallbackQuery,
    package_checker: PackageChecker
):
    """Check all packages."""
    email = callback.data.split(":", 1)[1]
    
    await callback.message.edit_text(f"🔄 Проверяю все пакеты для {email}...")
    await safe_answer_callback(callback)
    
    await send_package_check(callback.message, email, package_checker, only_outdated=False)


@router.callback_query(F.data.startswith("check_refresh:"))
async def callback_check_refresh(
    callback: CallbackQuery,
    package_checker: PackageChecker
):
    """Check packages with cache refresh."""
    email = callback.data.split(":", 1)[1]

    await callback.message.edit_text(f"🔄 Обновляю кэш и проверяю пакеты для {email}...")
    await safe_answer_callback(callback)

    await send_package_check(callback.message, email, package_checker, only_outdated=True, force_refresh=True)


@router.callback_query(F.data.startswith("page_check:"))
async def callback_page_check(
    callback: CallbackQuery,
    package_checker: PackageChecker
):
    """Handle pagination for package check."""
    parts = callback.data.split(":", 2)
    email = parts[1]
    page = int(parts[2])

    await safe_answer_callback(callback)

    # Get packages
    packages = await package_checker.get_outdated_packages(email)

    # Format with pagination
    text, total_pages = format_package_list(packages, email, page=page, show_all_statuses=False)

    # Create keyboard
    if total_pages > 1:
        keyboard = keyboards.pagination_keyboard(email, page, total_pages, prefix="check")
    else:
        keyboard = keyboards.back_to_menu_keyboard()

    try:
        await callback.message.edit_text(text, reply_markup=keyboard)
    except Exception as e:
        logger.error(f"Failed to edit message: {e}")
        await callback.message.answer(text, reply_markup=keyboard)


# ===== Stats Command =====

@router.message(Command("stats"))
@router.callback_query(F.data == "stats")
async def cmd_stats(event: Message | CallbackQuery, user_id: int, db: Database):
    """Handle /stats command or callback."""
    # Get user emails
    emails = await db.get_user_emails(user_id)

    if not emails:
        # User has no subscriptions yet
        text = (
            "📊 У вас пока нет подписок на мантейнеров.\n\n"
            "Чтобы начать отслеживать пакеты:\n"
            "1. Нажмите '👤 Мои мантейнеры'\n"
            "2. Добавьте nickname мантейнера"
        )
        keyboard = keyboards.back_to_menu_keyboard()
    else:
        text = "📊 Выберите email для статистики:"
        keyboard = keyboards.email_selection_keyboard(emails, prefix="stats")

    if isinstance(event, Message):
        await event.answer(text, reply_markup=keyboard)
    else:
        await event.message.edit_text(text, reply_markup=keyboard)
        await safe_answer_callback(event)


@router.callback_query(F.data.startswith("stats:"))
async def callback_stats_email(
    callback: CallbackQuery,
    user_id: int,
    db: Database,
    package_checker: PackageChecker
):
    """Show statistics for email."""
    email = callback.data.split(":", 1)[1]

    if email == "all":
        # Show stats for all emails
        emails = await db.get_user_emails(user_id)
        await callback.message.edit_text("🔄 Получаю статистику...")

        for i, email_addr in enumerate(emails):
            stats = await package_checker.get_package_stats(email_addr)
            text = format_package_stats(stats)

            # Добавляем кнопку к последнему сообщению
            if i == len(emails) - 1:
                await callback.message.answer(text, reply_markup=keyboards.back_to_menu_keyboard())
            else:
                await callback.message.answer(text)

        await safe_answer_callback(callback, "✅ Готово!")
    else:
        # Show stats for single email
        await callback.message.edit_text(f"🔄 Получаю статистику для {email}...")

        stats = await package_checker.get_package_stats(email)
        text = format_package_stats(stats)

        await callback.message.edit_text(
            text,
            reply_markup=keyboards.back_to_menu_keyboard()
        )
        await safe_answer_callback(callback)


@router.callback_query(F.data.startswith("page_stats:"))
async def callback_page_stats(
    callback: CallbackQuery,
    package_checker: PackageChecker
):
    """Handle pagination for stats package list."""
    parts = callback.data.split(":", 2)
    email = parts[1]
    page = int(parts[2])

    await safe_answer_callback(callback)

    # Get all packages for stats view
    packages = await package_checker.get_packages_for_email(email)

    # Format with pagination (show all statuses for stats)
    text, total_pages = format_package_list(packages, email, page=page, show_all_statuses=True)

    # Create keyboard
    if total_pages > 1:
        keyboard = keyboards.pagination_keyboard(email, page, total_pages, prefix="stats")
    else:
        keyboard = keyboards.back_to_menu_keyboard()

    try:
        await callback.message.edit_text(text, reply_markup=keyboard)
    except Exception as e:
        logger.error(f"Failed to edit message: {e}")
        await callback.message.answer(text, reply_markup=keyboard)


# ===== Menu Navigation =====

@router.callback_query(F.data == "menu")
async def callback_menu(callback: CallbackQuery, user: dict):
    """Return to main menu."""
    name = user['name']
    await callback.message.edit_text(
        f"👋 {name}, выберите действие:",
        reply_markup=keyboards.main_menu_keyboard()
    )
    await safe_answer_callback(callback)


@router.callback_query(F.data == "cancel")
async def callback_cancel(callback: CallbackQuery):
    """Cancel action."""
    await callback.message.edit_text("❌ Отменено")
    await safe_answer_callback(callback)


@router.callback_query(F.data == "noop")
async def callback_noop(callback: CallbackQuery):
    """Handle no-op callback (e.g., page indicator)."""
    await safe_answer_callback(callback)


# ===== Helper Functions =====

async def send_package_check(
    message: Message,
    email: str,
    package_checker: PackageChecker,
    only_outdated: bool = True,
    force_refresh: bool = False,
    add_keyboard: bool = True
):
    """
    Send package check results with pagination.

    Args:
        message: Message to reply to
        email: Email to check
        package_checker: PackageChecker instance
        only_outdated: Show only outdated packages
        force_refresh: Force cache refresh
        add_keyboard: Add back to menu keyboard to last message
    """
    try:
        if only_outdated:
            packages = await package_checker.get_outdated_packages(email, force_refresh=force_refresh)
        else:
            packages = await package_checker.get_packages_for_email(email, force_refresh=force_refresh)

        # Format with pagination (first page)
        text, total_pages = format_package_list(packages, email, page=0, show_all_statuses=not only_outdated)

        # Choose keyboard based on pagination
        if total_pages > 1:
            prefix = "check" if only_outdated else "stats"
            keyboard = keyboards.pagination_keyboard(email, 0, total_pages, prefix=prefix)
        elif add_keyboard:
            keyboard = keyboards.back_to_menu_keyboard()
        else:
            keyboard = None

        await message.answer(text, reply_markup=keyboard)

    except Exception as e:
        logger.error(f"Error checking packages for {email}: {e}", exc_info=True)
        await message.answer(
            f"❌ Ошибка при проверке пакетов: {str(e)}",
            reply_markup=keyboards.back_to_menu_keyboard()
        )
