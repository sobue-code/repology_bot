"""User data models."""
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, List


@dataclass
class User:
    """User model."""
    
    id: int
    name: str
    telegram_id: int
    enabled: bool
    created_at: datetime
    updated_at: datetime
    emails: Optional[List[str]] = None


@dataclass
class Subscription:
    """Subscription model."""
    
    id: int
    user_id: int
    frequency: str  # 'daily', 'weekly', 'manual'
    time: str  # HH:MM format
    day_of_week: Optional[int]  # 0-6 for weekly, None for daily
    enabled: bool
    last_notification: Optional[datetime]
    created_at: datetime
    
    @property
    def description(self) -> str:
        """Human-readable description of subscription."""
        if self.frequency == 'daily':
            return f"Ежедневно в {self.time}"
        elif self.frequency == 'weekly':
            days = ['Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница', 'Суббота', 'Воскресенье']
            day_name = days[self.day_of_week] if self.day_of_week is not None else "?"
            return f"Еженедельно по {day_name} в {self.time}"
        else:
            return "Только вручную"
