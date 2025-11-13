"""Handlers for subscription management."""
import logging
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery
from aiogram.exceptions import TelegramBadRequest

from bot import keyboards
from core.database import Database
from core.scheduler import NotificationScheduler

logger = logging.getLogger(__name__)

# Create router
router = Router()


# Helper functions
async def safe_answer_callback(callback: CallbackQuery, text: str = "", show_alert: bool = False):
    """Safely answer callback query, ignoring timeout errors."""
    try:
        await callback.answer(text, show_alert=show_alert)
    except TelegramBadRequest as e:
        if "query is too old" in str(e):
            logger.debug(f"Callback query too old, ignoring: {e}")
        else:
            raise


async def safe_edit_message(message: Message, text: str, **kwargs):
    """Safely edit message, ignoring 'message is not modified' errors."""
    try:
        await message.edit_text(text, **kwargs)
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            # Message is already in the correct state, ignore
            logger.debug("Message not modified, content is the same")
        else:
            raise


@router.message(Command("subscribe"))
@router.callback_query(F.data == "subscribe")
async def cmd_subscribe(event: Message | CallbackQuery):
    """Handle /subscribe command or callback."""
    text = "🔔 Настройка уведомлений\n\nВыберите частоту проверки:"
    keyboard = keyboards.subscription_menu_keyboard()
    
    if isinstance(event, Message):
        await event.answer(text, reply_markup=keyboard)
    else:
        await event.message.edit_text(text, reply_markup=keyboard)
        await safe_answer_callback(event)


@router.callback_query(F.data == "sub_daily")
async def callback_subscribe_daily(callback: CallbackQuery):
    """Handle daily subscription."""
    text = "⏰ Ежедневные уведомления\n\nВыберите время проверки:"
    keyboard = keyboards.time_selection_keyboard("daily")
    
    await safe_edit_message(callback.message,text, reply_markup=keyboard)
    await safe_answer_callback(callback)


@router.callback_query(F.data == "sub_weekly")
async def callback_subscribe_weekly(callback: CallbackQuery):
    """Handle weekly subscription."""
    text = "📅 Еженедельные уведомления\n\nВыберите время проверки:"
    keyboard = keyboards.time_selection_keyboard("weekly")
    
    await safe_edit_message(callback.message,text, reply_markup=keyboard)
    await safe_answer_callback(callback)


@router.callback_query(F.data.startswith("time_daily:"))
async def callback_time_daily(callback: CallbackQuery, user_id: int, db: Database, scheduler: NotificationScheduler):
    """Handle daily time selection."""
    time = callback.data.split(":", 1)[1]

    # Delete existing subscription if any
    await db.execute("DELETE FROM subscriptions WHERE user_id = ?", (user_id,))

    # Create new subscription
    await db.execute("""
        INSERT INTO subscriptions (user_id, frequency, time, day_of_week, enabled)
        VALUES (?, 'daily', ?, NULL, 1)
    """, (user_id, time))
    
    # Reload scheduler to pick up the new subscription
    await scheduler.reload_subscriptions()
    
    text = f"✅ Подписка настроена!\n\nВы будете получать уведомления ежедневно в {time}"
    
    await safe_edit_message(callback.message,
        text,
        reply_markup=keyboards.back_to_menu_keyboard()
    )
    await safe_answer_callback(callback, "✅ Подписка создана!")
    
    logger.info(f"User {user_id} subscribed to daily notifications at {time}")


@router.callback_query(F.data.startswith("time_weekly:"))
async def callback_time_weekly(callback: CallbackQuery):
    """Handle weekly time selection."""
    time = callback.data.split(":", 1)[1]
    
    text = f"📅 Выбрано время: {time}\n\nТеперь выберите день недели:"
    keyboard = keyboards.day_selection_keyboard(time)
    
    await safe_edit_message(callback.message,text, reply_markup=keyboard)
    await safe_answer_callback(callback)


@router.callback_query(F.data.startswith("day:"))
async def callback_day_selection(callback: CallbackQuery, user_id: int, db: Database, scheduler: NotificationScheduler):
    """Handle day of week selection."""
    parts = callback.data.split(":")
    time = parts[1]
    day_of_week = int(parts[2])

    # Delete existing subscription if any
    await db.execute("DELETE FROM subscriptions WHERE user_id = ?", (user_id,))

    # Create new subscription
    await db.execute("""
        INSERT INTO subscriptions (user_id, frequency, time, day_of_week, enabled)
        VALUES (?, 'weekly', ?, ?, 1)
    """, (user_id, time, day_of_week))
    
    # Reload scheduler to pick up the new subscription
    await scheduler.reload_subscriptions()
    
    days = ['Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница', 'Суббота', 'Воскресенье']
    day_name = days[day_of_week]
    
    text = f"✅ Подписка настроена!\n\nВы будете получать уведомления еженедельно\nпо {day_name} в {time}"
    
    await safe_edit_message(callback.message,
        text,
        reply_markup=keyboards.back_to_menu_keyboard()
    )
    await safe_answer_callback(callback, "✅ Подписка создана!")
    
    logger.info(f"User {user_id} subscribed to weekly notifications on day {day_of_week} at {time}")


@router.message(Command("unsubscribe"))
@router.callback_query(F.data == "unsub")
async def cmd_unsubscribe(event: Message | CallbackQuery, user_id: int, db: Database):
    """Handle /unsubscribe command or callback."""
    # Check if user has active subscription
    sub_row = await db.fetchone(
        "SELECT * FROM subscriptions WHERE user_id = ? AND enabled = 1",
        (user_id,)
    )
    
    if sub_row is None:
        text = "ℹ️ У вас нет активных подписок"
        
        if isinstance(event, Message):
            await event.answer(text, reply_markup=keyboards.back_to_menu_keyboard())
        else:
            await event.message.edit_text(text, reply_markup=keyboards.back_to_menu_keyboard())
            await safe_answer_callback(event)
        return
    
    # Show confirmation
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
    
    text = f"🔔 Текущая подписка: {sub.description}\n\nОтключить уведомления?"
    keyboard = keyboards.confirm_keyboard("unsub")
    
    if isinstance(event, Message):
        await event.answer(text, reply_markup=keyboard)
    else:
        await event.message.edit_text(text, reply_markup=keyboard)
        await safe_answer_callback(event)


@router.callback_query(F.data.startswith("confirm_unsub"))
async def callback_confirm_unsubscribe(callback: CallbackQuery, user_id: int, db: Database, scheduler: NotificationScheduler):
    """Confirm unsubscription."""
    # Disable subscription
    await db.execute(
        "UPDATE subscriptions SET enabled = 0 WHERE user_id = ?",
        (user_id,)
    )
    
    # Reload scheduler to remove the disabled subscription
    await scheduler.reload_subscriptions()
    
    text = "✅ Автоматические уведомления отключены\n\nВы всё ещё можете проверять пакеты вручную командой /check"
    
    await safe_edit_message(callback.message,
        text,
        reply_markup=keyboards.back_to_menu_keyboard()
    )
    await safe_answer_callback(callback, "✅ Подписка отключена")
    
    logger.info(f"User {user_id} unsubscribed from notifications")


@router.message(Command("settings"))
async def cmd_settings(message: Message, user_id: int, db: Database):
    """Handle /settings command."""
    # Get current subscription
    sub_row = await db.fetchone(
        "SELECT * FROM subscriptions WHERE user_id = ?",
        (user_id,)
    )
    
    if sub_row is None or not sub_row['enabled']:
        text = "⚙️ Настройки\n\nУ вас нет активной подписки.\nИспользуйте /subscribe для настройки уведомлений."
    else:
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
        
        text = f"⚙️ Настройки\n\n🔔 Текущая подписка: {sub.description}"
        
        if sub.last_notification:
            from utils.formatting import format_datetime
            text += f"\n\n🕐 Последнее уведомление:\n{format_datetime(sub.last_notification)}"
        
        text += "\n\nИспользуйте /subscribe для изменения или /unsubscribe для отключения."
    
    await message.answer(text, reply_markup=keyboards.back_to_menu_keyboard())


@router.message(Command("test_notify"))
async def cmd_test_notify(message: Message, user_id: int, db: Database, scheduler: NotificationScheduler):
    """Test command to manually trigger notification (for testing purposes)."""
    # Get user subscription
    sub_row = await db.fetchone(
        "SELECT * FROM subscriptions WHERE user_id = ? AND enabled = 1",
        (user_id,)
    )
    
    if sub_row is None:
        await message.answer(
            "❌ У вас нет активной подписки.\nИспользуйте /subscribe для настройки уведомлений.",
            reply_markup=keyboards.back_to_menu_keyboard()
        )
        return
    
    telegram_id = message.from_user.id
    
    await message.answer("🔄 Отправляю тестовое уведомление...")
    
    try:
        from services.notification import NotificationService
        # Get notification service from scheduler
        notification_service = scheduler.notification_service
        
        await notification_service.send_notification_to_user(
            user_id=user_id,
            telegram_id=telegram_id,
            subscription_id=sub_row['id']
        )
        
        await message.answer("✅ Тестовое уведомление отправлено!")
    except Exception as e:
        logger.error(f"Failed to send test notification: {e}", exc_info=True)
        await message.answer(f"❌ Ошибка при отправке уведомления: {str(e)}")
