"""Telegram inline keyboards."""
from typing import List
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton


def main_menu_keyboard() -> InlineKeyboardMarkup:
    """Create main menu keyboard."""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔍 Проверить пакеты", callback_data="check")],
        [InlineKeyboardButton(text="🔔 Настроить уведомления", callback_data="subscribe")],
        [InlineKeyboardButton(text="ℹ️ Мои настройки", callback_data="status")],
        [InlineKeyboardButton(text="📊 Статистика", callback_data="stats")],
    ])
    return keyboard


def email_selection_keyboard(emails: List[str], prefix: str = "check") -> InlineKeyboardMarkup:
    """
    Create keyboard for email selection.
    
    Args:
        emails: List of email addresses
        prefix: Callback data prefix
        
    Returns:
        InlineKeyboardMarkup
    """
    buttons = []
    
    # Add button for each email
    for email in emails:
        buttons.append([
            InlineKeyboardButton(
                text=f"📧 {email}",
                callback_data=f"{prefix}:{email}"
            )
        ])
    
    # Add "All emails" button
    if len(emails) > 1:
        buttons.append([
            InlineKeyboardButton(
                text="📨 Все email",
                callback_data=f"{prefix}:all"
            )
        ])
    
    # Add back button
    buttons.append([
        InlineKeyboardButton(text="◀️ Назад", callback_data="menu")
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def check_options_keyboard(email: str) -> InlineKeyboardMarkup:
    """
    Create keyboard for check options.
    
    Args:
        email: Email address
        
    Returns:
        InlineKeyboardMarkup
    """
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="⚠️ Только outdated",
            callback_data=f"check_outdated:{email}"
        )],
        [InlineKeyboardButton(
            text="📦 Все пакеты",
            callback_data=f"check_all:{email}"
        )],
        [InlineKeyboardButton(
            text="🔄 Обновить кэш",
            callback_data=f"check_refresh:{email}"
        )],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="check")],
    ])
    return keyboard


def subscription_menu_keyboard() -> InlineKeyboardMarkup:
    """Create subscription menu keyboard."""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="⏰ Ежедневно",
            callback_data="sub_daily"
        )],
        [InlineKeyboardButton(
            text="📅 Еженедельно",
            callback_data="sub_weekly"
        )],
        [InlineKeyboardButton(
            text="❌ Отключить уведомления",
            callback_data="unsub"
        )],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="menu")],
    ])
    return keyboard


def time_selection_keyboard(frequency: str) -> InlineKeyboardMarkup:
    """
    Create keyboard for time selection.
    
    Args:
        frequency: 'daily' or 'weekly'
        
    Returns:
        InlineKeyboardMarkup
    """
    times = ["06:00", "09:00", "12:00", "15:00", "18:00", "21:00"]
    
    buttons = []
    # Add times in pairs
    for i in range(0, len(times), 2):
        row = []
        for j in range(2):
            if i + j < len(times):
                time = times[i + j]
                row.append(InlineKeyboardButton(
                    text=time,
                    callback_data=f"time_{frequency}:{time}"
                ))
        buttons.append(row)
    
    buttons.append([
        InlineKeyboardButton(text="◀️ Назад", callback_data="subscribe")
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def day_selection_keyboard(time: str) -> InlineKeyboardMarkup:
    """
    Create keyboard for day of week selection.
    
    Args:
        time: Selected time (HH:MM)
        
    Returns:
        InlineKeyboardMarkup
    """
    days = [
        ("Понедельник", 0),
        ("Вторник", 1),
        ("Среда", 2),
        ("Четверг", 3),
        ("Пятница", 4),
        ("Суббота", 5),
        ("Воскресенье", 6),
    ]
    
    buttons = []
    for day_name, day_num in days:
        buttons.append([
            InlineKeyboardButton(
                text=day_name,
                callback_data=f"day:{time}:{day_num}"
            )
        ])
    
    buttons.append([
        InlineKeyboardButton(text="◀️ Назад", callback_data="subscribe")
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def confirm_keyboard(action: str, data: str = "") -> InlineKeyboardMarkup:
    """
    Create confirmation keyboard.
    
    Args:
        action: Action to confirm
        data: Additional data
        
    Returns:
        InlineKeyboardMarkup
    """
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="✅ Да",
                callback_data=f"confirm_{action}:{data}"
            ),
            InlineKeyboardButton(
                text="❌ Нет",
                callback_data="cancel"
            )
        ]
    ])
    return keyboard


def back_to_menu_keyboard() -> InlineKeyboardMarkup:
    """Create simple back to menu keyboard."""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Главное меню", callback_data="menu")]
    ])
    return keyboard


def pagination_keyboard(
    email: str,
    current_page: int,
    total_pages: int,
    prefix: str = "check"
) -> InlineKeyboardMarkup:
    """
    Create pagination keyboard.

    Args:
        email: Email address
        current_page: Current page number (0-indexed)
        total_pages: Total number of pages
        prefix: Callback prefix (check or stats)

    Returns:
        InlineKeyboardMarkup
    """
    buttons = []

    # Navigation row
    nav_row = []
    if current_page > 0:
        nav_row.append(InlineKeyboardButton(
            text="⬅️",
            callback_data=f"page_{prefix}:{email}:{current_page - 1}"
        ))

    # Page indicator
    nav_row.append(InlineKeyboardButton(
        text=f"📄 {current_page + 1}/{total_pages}",
        callback_data="noop"
    ))

    if current_page < total_pages - 1:
        nav_row.append(InlineKeyboardButton(
            text="➡️",
            callback_data=f"page_{prefix}:{email}:{current_page + 1}"
        ))

    if nav_row:
        buttons.append(nav_row)

    # Back to menu
    buttons.append([
        InlineKeyboardButton(text="◀️ Главное меню", callback_data="menu")
    ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)
