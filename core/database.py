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
        
        -- User emails table
        CREATE TABLE IF NOT EXISTS user_emails (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            email TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
            UNIQUE(user_id, email)
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
        
        -- Indexes for optimization
        CREATE INDEX IF NOT EXISTS idx_user_emails_email ON user_emails(email);
        CREATE INDEX IF NOT EXISTS idx_user_emails_user_id ON user_emails(user_id);
        CREATE INDEX IF NOT EXISTS idx_subscriptions_user_enabled ON subscriptions(user_id, enabled);
        CREATE INDEX IF NOT EXISTS idx_package_cache_email ON package_cache(email);
        CREATE INDEX IF NOT EXISTS idx_package_cache_status ON package_cache(status);
        CREATE INDEX IF NOT EXISTS idx_package_cache_cached_at ON package_cache(cached_at);
        CREATE INDEX IF NOT EXISTS idx_notification_history_user ON notification_history(user_id);
        """
        
        await self.connection.executescript(schema)
        await self.connection.commit()
        logger.info("Database schema initialized")
    
    async def sync_users_from_config(self, users_config):
        """
        Synchronize users from configuration to database.
        
        Args:
            users_config: List of UserConfig objects
        """
        for user_cfg in users_config:
            # Insert or update user
            await self.connection.execute("""
                INSERT INTO users (name, telegram_id, enabled)
                VALUES (?, ?, ?)
                ON CONFLICT(telegram_id) DO UPDATE SET
                    name = excluded.name,
                    enabled = excluded.enabled,
                    updated_at = CURRENT_TIMESTAMP
            """, (user_cfg.name, user_cfg.telegram_id, user_cfg.enabled))
            
            # Get user id
            cursor = await self.connection.execute(
                "SELECT id FROM users WHERE telegram_id = ?",
                (user_cfg.telegram_id,)
            )
            row = await cursor.fetchone()
            user_id = row['id']
            
            # Remove old emails that are not in config
            await self.connection.execute("""
                DELETE FROM user_emails
                WHERE user_id = ? AND email NOT IN ({})
            """.format(','.join('?' * len(user_cfg.emails))),
                (user_id, *user_cfg.emails)
            )
            
            # Insert new emails
            for email in user_cfg.emails:
                await self.connection.execute("""
                    INSERT OR IGNORE INTO user_emails (user_id, email)
                    VALUES (?, ?)
                """, (user_id, email))
        
        await self.connection.commit()
        logger.info(f"Synchronized {len(users_config)} users from config")
    
    async def get_user_by_telegram_id(self, telegram_id: int):
        """Get user by Telegram ID."""
        cursor = await self.connection.execute(
            "SELECT * FROM users WHERE telegram_id = ?",
            (telegram_id,)
        )
        return await cursor.fetchone()
    
    async def get_user_emails(self, user_id: int):
        """Get all emails for a user."""
        cursor = await self.connection.execute(
            "SELECT email FROM user_emails WHERE user_id = ? ORDER BY email",
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
