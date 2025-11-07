"""Configuration management module."""
import sys
from pathlib import Path
from typing import List, Dict

try:
    import tomllib
except ImportError:
    import tomli as tomllib

from pydantic import BaseModel, Field, field_validator


class BotConfig(BaseModel):
    """Bot configuration."""
    token: str
    admin_ids: List[int] = Field(default_factory=list)


class DatabaseConfig(BaseModel):
    """Database configuration."""
    path: str = "data/bot.db"


class LoggingConfig(BaseModel):
    """Logging configuration."""
    level: str = "INFO"
    file: str = "logs/bot.log"
    max_bytes: int = 10485760
    backup_count: int = 5

    @field_validator('level')
    @classmethod
    def validate_level(cls, v: str) -> str:
        """Validate logging level."""
        allowed = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']
        v_upper = v.upper()
        if v_upper not in allowed:
            raise ValueError(f"Logging level must be one of {allowed}")
        return v_upper


class RepologyConfig(BaseModel):
    """Repology API configuration."""
    api_base_url: str = "https://repology.org/api/v1"
    request_timeout: int = 30
    rate_limit_delay: float = 1.0
    cache_duration: int = 6


class RDBConfig(BaseModel):
    """ALT Linux RDB API configuration."""
    api_base_url: str = "https://rdb.altlinux.org/api/site"
    request_timeout: int = 30
    maintainer_mapping: Dict[str, str] = Field(default_factory=dict)  # email -> nickname


class NotificationsConfig(BaseModel):
    """Notifications configuration."""
    default_check_time: str = "09:00"
    max_packages_per_message: int = 20
    min_notification_interval: int = 1

    @field_validator('default_check_time')
    @classmethod
    def validate_time(cls, v: str) -> str:
        """Validate time format HH:MM."""
        parts = v.split(':')
        if len(parts) != 2:
            raise ValueError("Time must be in HH:MM format")
        try:
            hour, minute = int(parts[0]), int(parts[1])
            if not (0 <= hour <= 23 and 0 <= minute <= 59):
                raise ValueError("Invalid time values")
        except ValueError as e:
            raise ValueError(f"Invalid time format: {e}")
        return v


class UserConfig(BaseModel):
    """User configuration."""
    name: str
    telegram_id: int
    emails: List[str]
    enabled: bool = True

    @field_validator('emails')
    @classmethod
    def validate_emails(cls, v: List[str]) -> List[str]:
        """Validate that emails list is not empty."""
        if not v:
            raise ValueError("At least one email must be specified")
        return v


class Config(BaseModel):
    """Main configuration."""
    bot: BotConfig
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    repology: RepologyConfig = Field(default_factory=RepologyConfig)
    rdb: RDBConfig = Field(default_factory=RDBConfig)
    notifications: NotificationsConfig = Field(default_factory=NotificationsConfig)
    users: List[UserConfig]

    @field_validator('users')
    @classmethod
    def validate_users(cls, v: List[UserConfig]) -> List[UserConfig]:
        """Validate users list."""
        if not v:
            raise ValueError("At least one user must be configured")
        
        # Check for duplicate telegram IDs
        telegram_ids = [u.telegram_id for u in v]
        if len(telegram_ids) != len(set(telegram_ids)):
            raise ValueError("Duplicate telegram_id found in users")
        
        return v


def load_config(config_path: str = "config.toml") -> Config:
    """
    Load configuration from TOML file.
    
    Args:
        config_path: Path to configuration file
        
    Returns:
        Config object
        
    Raises:
        FileNotFoundError: If config file doesn't exist
        ValueError: If config is invalid
    """
    path = Path(config_path)
    
    if not path.exists():
        print(f"Error: Configuration file '{config_path}' not found!")
        print(f"Please copy 'config.toml.example' to '{config_path}' and configure it.")
        sys.exit(1)
    
    try:
        with open(path, 'rb') as f:
            data = tomllib.load(f)
        
        config = Config(**data)
        return config
    except Exception as e:
        print(f"Error loading configuration: {e}")
        sys.exit(1)
