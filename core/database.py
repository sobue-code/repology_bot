"""Database management module."""
import aiosqlite
from pathlib import Path
from typing import Optional
import logging

logger = logging.getLogger(__name__)


class Database:
    """SQLite database manager."""
    
    def __init__(self, db_path: str):
        """
        Initialize database manager.
        
        Args:
            db_path: Path to SQLite database file
        """
        self.db_path = db_path
        self.connection: Optional[aiosqlite.Connection] = None
    
    async def connect(self):
        """Establish database connection."""
        # Create data directory if it doesn't exist
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        
        self.connection = await aiosqlite.connect(self.db_path)
        self.connection.row_factory = aiosqlite.Row
        
        logger.info(f"Connected to database: {self.db_path}")
        await self.init_schema()
    
    async def disconnect(self):
        """Close database connection."""
        if self.connection:
            await self.connection.close()
            logger.info("Database connection closed")
    
    async def init_schema(self):
        """Initialize database schema."""
        schema = """
        -- Users table
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            telegram_id INTEGER UNIQUE NOT NULL,
            enabled BOOLEAN DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        -- Subscriptions table
        CREATE TABLE IF NOT EXISTS subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            frequency TEXT NOT NULL CHECK(frequency IN ('daily', 'weekly', 'manual')),
            time TEXT NOT NULL,
            day_of_week INTEGER CHECK(day_of_week IS NULL OR (day_of_week >= 0 AND day_of_week <= 6)),
            enabled BOOLEAN DEFAULT 1,
            last_notification TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        
        -- Package cache table
        CREATE TABLE IF NOT EXISTS package_cache (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL,
            package_name TEXT NOT NULL,
            repo TEXT NOT NULL,
            current_version TEXT,
            latest_version TEXT,
            status TEXT CHECK(status IN ('outdated', 'newest', 'devel', 'unique', 'legacy', 'incorrect', 'untrusted', 'noscheme', 'rolling')),
            data_json TEXT,
            cached_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            -- RDB fields
            has_rdb_data INTEGER DEFAULT 0,
            rdb_pkg_name TEXT,
            rdb_new_version TEXT,
            rdb_url TEXT,
            rdb_date TEXT,
            UNIQUE(email, package_name, repo)
        );
        
        -- Notification history table
        CREATE TABLE IF NOT EXISTS notification_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            packages_count INTEGER,
            notification_type TEXT CHECK(notification_type IN ('manual', 'scheduled')),
            sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );

        -- Maintainer subscriptions table (new dynamic subscription system)
        CREATE TABLE IF NOT EXISTS maintainer_subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            nickname TEXT NOT NULL,
            email TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
            UNIQUE(user_id, nickname)
        );

        -- Indexes for optimization
        CREATE INDEX IF NOT EXISTS idx_subscriptions_user_enabled ON subscriptions(user_id, enabled);
        CREATE INDEX IF NOT EXISTS idx_package_cache_email ON package_cache(email);
        CREATE INDEX IF NOT EXISTS idx_package_cache_status ON package_cache(status);
        CREATE INDEX IF NOT EXISTS idx_package_cache_cached_at ON package_cache(cached_at);
        CREATE INDEX IF NOT EXISTS idx_notification_history_user ON notification_history(user_id);
        CREATE INDEX IF NOT EXISTS idx_maintainer_subscriptions_user ON maintainer_subscriptions(user_id);
        CREATE INDEX IF NOT EXISTS idx_maintainer_subscriptions_nickname ON maintainer_subscriptions(nickname);
        """
        
        await self.connection.executescript(schema)
        await self.connection.commit()
        logger.info("Database schema initialized")
    
    async def sync_users_from_config(self, users_config):
        """
        Deprecated: Users are now registered automatically.
        This method is kept for backward compatibility but does nothing.

        Args:
            users_config: List of UserConfig objects (ignored)
        """
        if users_config:
            logger.warning(
                "Users configuration in config.toml is deprecated. "
                "Users are now registered automatically when they interact with the bot."
            )
        logger.info("Using dynamic user registration mode")
    
    async def get_user_by_telegram_id(self, telegram_id: int):
        """Get user by Telegram ID."""
        cursor = await self.connection.execute(
            "SELECT * FROM users WHERE telegram_id = ?",
            (telegram_id,)
        )
        return await cursor.fetchone()
    
    async def get_user_emails(self, user_id: int):
        """
        Get all emails for a user from maintainer subscriptions.

        Args:
            user_id: User ID

        Returns:
            List of email addresses
        """
        cursor = await self.connection.execute(
            "SELECT email FROM maintainer_subscriptions WHERE user_id = ? ORDER BY email",
            (user_id,)
        )
        rows = await cursor.fetchall()
        return [row['email'] for row in rows]
    
    async def execute(self, query: str, params: tuple = ()):
        """Execute a query."""
        cursor = await self.connection.execute(query, params)
        await self.connection.commit()
        return cursor
    
    async def fetchone(self, query: str, params: tuple = ()):
        """Fetch one row."""
        cursor = await self.connection.execute(query, params)
        return await cursor.fetchone()
    
    async def fetchall(self, query: str, params: tuple = ()):
        """Fetch all rows."""
        cursor = await self.connection.execute(query, params)
        return await cursor.fetchall()

    # ===== Maintainer Subscription Methods =====

    async def add_maintainer_subscription(self, user_id: int, nickname: str) -> bool:
        """
        Add a maintainer subscription for a user.

        Args:
            user_id: User ID
            nickname: Maintainer nickname in RDB

        Returns:
            True if added successfully, False if already exists
        """
        email = f"{nickname}@altlinux.org"
        try:
            await self.connection.execute("""
                INSERT INTO maintainer_subscriptions (user_id, nickname, email)
                VALUES (?, ?, ?)
            """, (user_id, nickname, email))
            await self.connection.commit()
            logger.info(f"Added maintainer subscription: user_id={user_id}, nickname={nickname}")
            return True
        except Exception as e:
            if "UNIQUE constraint failed" in str(e):
                logger.debug(f"Subscription already exists: user_id={user_id}, nickname={nickname}")
                return False
            else:
                logger.error(f"Failed to add subscription: {e}")
                raise

    async def remove_maintainer_subscription(self, user_id: int, nickname: str) -> bool:
        """
        Remove a maintainer subscription for a user.

        Args:
            user_id: User ID
            nickname: Maintainer nickname in RDB

        Returns:
            True if removed successfully, False if not found
        """
        cursor = await self.connection.execute("""
            DELETE FROM maintainer_subscriptions
            WHERE user_id = ? AND nickname = ?
        """, (user_id, nickname))
        await self.connection.commit()

        removed = cursor.rowcount > 0
        if removed:
            logger.info(f"Removed maintainer subscription: user_id={user_id}, nickname={nickname}")
        else:
            logger.debug(f"Subscription not found: user_id={user_id}, nickname={nickname}")

        return removed

    async def get_user_maintainer_subscriptions(self, user_id: int):
        """
        Get all maintainer subscriptions for a user.

        Args:
            user_id: User ID

        Returns:
            List of dicts with 'nickname', 'email', 'created_at'
        """
        cursor = await self.connection.execute("""
            SELECT nickname, email, created_at
            FROM maintainer_subscriptions
            WHERE user_id = ?
            ORDER BY nickname
        """, (user_id,))
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def check_maintainer_subscription_exists(self, user_id: int, nickname: str) -> bool:
        """
        Check if a maintainer subscription exists.

        Args:
            user_id: User ID
            nickname: Maintainer nickname

        Returns:
            True if exists, False otherwise
        """
        cursor = await self.connection.execute("""
            SELECT 1 FROM maintainer_subscriptions
            WHERE user_id = ? AND nickname = ?
        """, (user_id, nickname))
        row = await cursor.fetchone()
        return row is not None

    async def create_user_if_not_exists(self, telegram_id: int, name: str = None) -> int:
        """
        Create a user if they don't exist, or return existing user ID.

        Args:
            telegram_id: Telegram user ID
            name: User name (defaults to "User {telegram_id}")

        Returns:
            User ID (from database)
        """
        if name is None:
            name = f"User {telegram_id}"

        # Try to insert or get existing
        await self.connection.execute("""
            INSERT OR IGNORE INTO users (name, telegram_id, enabled)
            VALUES (?, ?, 1)
        """, (name, telegram_id))
        await self.connection.commit()

        # Get user ID
        cursor = await self.connection.execute(
            "SELECT id FROM users WHERE telegram_id = ?",
            (telegram_id,)
        )
        row = await cursor.fetchone()

        if row:
            logger.info(f"User auto-registered or found: telegram_id={telegram_id}, user_id={row['id']}")
            return row['id']
        else:
            raise RuntimeError(f"Failed to create/find user with telegram_id={telegram_id}")


# Global database instance
db: Optional[Database] = None


async def init_database(db_path: str) -> Database:
    """
    Initialize global database instance.
    
    Args:
        db_path: Path to database file
        
    Returns:
        Database instance
    """
    global db
    db = Database(db_path)
    await db.connect()
    return db


async def get_db() -> Database:
    """Get global database instance."""
    if db is None:
        raise RuntimeError("Database not initialized. Call init_database() first.")
    return db
