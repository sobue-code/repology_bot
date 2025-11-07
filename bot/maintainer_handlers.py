"""Telegram bot maintainer management handlers."""
import logging
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.exceptions import TelegramBadRequest

from bot import keyboards
from core.database import Database
from services.rdb import RDBClient
from utils.formatting import format_datetime

logger = logging.getLogger(__name__)

# Create router
router = Router()


# FSM states for adding maintainer
class AddMaintainerStates(StatesGroup):
    """States for adding maintainer."""
    waiting_for_nickname = State()


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


# ===== Maintainers Menu =====

@router.callback_query(F.data == "maintainers")
async def callback_maintainers_menu(callback: CallbackQuery):
    """Show maintainers management menu."""
    await safe_edit_message(callback.message,
        "👤 Управление подписками на мантейнеров\n\n"
        "Здесь вы можете добавлять и удалять мантейнеров, "
        "за пакетами которых хотите следить.",
        reply_markup=keyboards.maintainers_menu_keyboard()
    )
    await safe_answer_callback(callback)


# ===== List Maintainers =====

@router.callback_query(F.data == "list_maintainers")
async def callback_list_maintainers(callback: CallbackQuery, user_id: int, db: Database):
    """Show list of subscribed maintainers."""
    maintainers = await db.get_user_maintainer_subscriptions(user_id)

    if not maintainers:
        await safe_edit_message(
            callback.message,
            "📋 У вас пока нет подписок на мантейнеров.\n\n"
            "Используйте кнопку 'Добавить мантейнера' для добавления.",
            reply_markup=keyboards.maintainers_menu_keyboard()
        )
    else:
        text = f"📋 Ваши подписки ({len(maintainers)}):\n\n"
        await safe_edit_message(
            callback.message,
            text,
            reply_markup=keyboards.maintainers_list_keyboard(maintainers)
        )

    await safe_answer_callback(callback)


# ===== Maintainer Info =====

@router.callback_query(F.data.startswith("maintainer_info:"))
async def callback_maintainer_info(callback: CallbackQuery, user_id: int, db: Database):
    """Show information about a specific maintainer."""
    nickname = callback.data.split(":", 1)[1]

    # Check if subscription exists
    exists = await db.check_maintainer_subscription_exists(user_id, nickname)

    if not exists:
        await safe_edit_message(callback.message,
            "❌ Подписка не найдена",
            reply_markup=keyboards.maintainers_menu_keyboard()
        )
        await safe_answer_callback(callback)
        return

    # Get subscription details
    subs = await db.get_user_maintainer_subscriptions(user_id)
    sub = next((s for s in subs if s['nickname'] == nickname), None)

    if sub:
        created_at = format_datetime(sub['created_at'])
        text = (
            f"👤 Мантейнер: {nickname}\n"
            f"📧 Email: {sub['email']}\n"
            f"📅 Подписка с: {created_at}\n"
        )
    else:
        text = f"👤 Мантейнер: {nickname}\n📧 Email: {nickname}@altlinux.org\n"

    await safe_edit_message(callback.message,
        text,
        reply_markup=keyboards.maintainer_actions_keyboard(nickname)
    )
    await safe_answer_callback(callback)


# ===== Add Maintainer =====

@router.callback_query(F.data == "add_maintainer")
async def callback_add_maintainer(callback: CallbackQuery, state: FSMContext):
    """Start adding a maintainer."""
    await safe_edit_message(callback.message,
        "➕ Добавление мантейнера\n\n"
        "Введите nickname мантейнера в RDB (ALT Linux).\n"
        "Например: sobue, amakeenk\n\n"
        "Email будет автоматически сформирован как nickname@altlinux.org",
        reply_markup=keyboards.cancel_keyboard()
    )
    await state.set_state(AddMaintainerStates.waiting_for_nickname)
    await safe_answer_callback(callback)


@router.message(AddMaintainerStates.waiting_for_nickname)
async def process_maintainer_nickname(
    message: Message,
    state: FSMContext,
    user_id: int,
    db: Database,
    rdb_client: RDBClient
):
    """Process entered maintainer nickname."""
    nickname = message.text.strip()

    # Validate nickname format (alphanumeric and underscore)
    if not nickname or not nickname.replace('_', '').replace('-', '').isalnum():
        await message.answer(
            "❌ Некорректный nickname. Используйте только латинские буквы, цифры, дефис и подчеркивание.\n\n"
            "Попробуйте еще раз или нажмите 'Отмена':",
            reply_markup=keyboards.cancel_keyboard()
        )
        return

    # Check if already subscribed
    exists = await db.check_maintainer_subscription_exists(user_id, nickname)
    if exists:
        await message.answer(
            f"ℹ️ Вы уже подписаны на мантейнера '{nickname}'",
            reply_markup=keyboards.maintainers_menu_keyboard()
        )
        await state.clear()
        return

    # Validate maintainer exists in RDB (optional, non-blocking)
    await message.answer(f"🔍 Проверяю мантейнера '{nickname}' в RDB...")

    is_valid = await rdb_client.validate_maintainer(nickname)

    if not is_valid:
        await message.answer(
            f"⚠️ Мантейнер '{nickname}' не найден в RDB.\n\n"
            f"Возможно, nickname указан неверно. "
            f"Вы все равно можете добавить подписку, но проверьте правильность написания.",
            reply_markup=keyboards.confirm_keyboard("add_maint", nickname)
        )
        await state.clear()
        return

    # Add subscription
    success = await db.add_maintainer_subscription(user_id, nickname)

    if success:
        await message.answer(
            f"✅ Подписка на мантейнера '{nickname}' добавлена!\n"
            f"📧 Email: {nickname}@altlinux.org",
            reply_markup=keyboards.maintainers_menu_keyboard()
        )
    else:
        await message.answer(
            f"❌ Не удалось добавить подписку. Возможно, она уже существует.",
            reply_markup=keyboards.maintainers_menu_keyboard()
        )

    await state.clear()


@router.callback_query(F.data == "cancel_add_maintainer")
async def callback_cancel_add_maintainer(callback: CallbackQuery, state: FSMContext):
    """Cancel adding maintainer."""
    await state.clear()
    await safe_edit_message(callback.message,
        "❌ Добавление отменено",
        reply_markup=keyboards.maintainers_menu_keyboard()
    )
    await safe_answer_callback(callback)


@router.callback_query(F.data.startswith("confirm_add_maint:"))
async def callback_confirm_add_maintainer(
    callback: CallbackQuery,
    user_id: int,
    db: Database
):
    """Confirm adding maintainer that wasn't found in RDB."""
    nickname = callback.data.split(":", 1)[1]

    success = await db.add_maintainer_subscription(user_id, nickname)

    if success:
        await safe_edit_message(callback.message,
            f"✅ Подписка на мантейнера '{nickname}' добавлена!\n"
            f"📧 Email: {nickname}@altlinux.org\n\n"
            f"⚠️ Обратите внимание: мантейнер не был найден в RDB при проверке.",
            reply_markup=keyboards.maintainers_menu_keyboard()
        )
    else:
        await safe_edit_message(callback.message,
            f"❌ Не удалось добавить подписку.",
            reply_markup=keyboards.maintainers_menu_keyboard()
        )

    await safe_answer_callback(callback)


# ===== Remove Maintainer =====

@router.callback_query(F.data.startswith("remove_maintainer:"))
async def callback_remove_maintainer(
    callback: CallbackQuery,
    user_id: int,
    db: Database
):
    """Remove maintainer subscription."""
    nickname = callback.data.split(":", 1)[1]

    success = await db.remove_maintainer_subscription(user_id, nickname)

    if success:
        await safe_edit_message(callback.message,
            f"✅ Подписка на мантейнера '{nickname}' удалена",
            reply_markup=keyboards.maintainers_menu_keyboard()
        )
    else:
        await safe_edit_message(callback.message,
            f"❌ Не удалось удалить подписку (возможно, она уже была удалена)",
            reply_markup=keyboards.maintainers_menu_keyboard()
        )

    await safe_answer_callback(callback)
