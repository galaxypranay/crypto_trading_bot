"""
Railway PostgreSQL — seen news IDs track karne ke liye.
Agar DATABASE_URL nahi hai toh fallback in-memory set use hota hai.
"""
import os
import logging
from typing import Optional

logger = logging.getLogger(__name__)

_pool = None
_memory_fallback: set[str] = set()
DB_AVAILABLE = False


async def init_db():
    """Connect to Postgres and create table if not exists."""
    global _pool, DB_AVAILABLE
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        logger.warning("DATABASE_URL not set — using in-memory fallback for seen news.")
        return

    try:
        import asyncpg
        _pool = await asyncpg.create_pool(db_url, min_size=1, max_size=3)
        async with _pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS seen_news (
                    id TEXT PRIMARY KEY,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                );
            """)
        DB_AVAILABLE = True
        logger.info("PostgreSQL connected — seen_news table ready.")
    except Exception as e:
        logger.error(f"DB init failed — using in-memory fallback: {e}")


async def is_seen(news_id: str) -> bool:
    """Check if news ID already processed."""
    if not DB_AVAILABLE:
        return news_id in _memory_fallback

    try:
        async with _pool.acquire() as conn:
            row = await conn.fetchrow("SELECT id FROM seen_news WHERE id=$1", news_id)
            return row is not None
    except Exception as e:
        logger.error(f"DB is_seen error: {e}")
        return news_id in _memory_fallback


async def mark_seen(news_id: str):
    """Mark news ID as processed."""
    if not DB_AVAILABLE:
        _memory_fallback.add(news_id)
        # Memory cleanup
        if len(_memory_fallback) > 1000:
            keep = list(_memory_fallback)[-500:]
            _memory_fallback.clear()
            _memory_fallback.update(keep)
        return

    try:
        async with _pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO seen_news (id) VALUES ($1) ON CONFLICT DO NOTHING",
                news_id
            )
    except Exception as e:
        logger.error(f"DB mark_seen error: {e}")
        _memory_fallback.add(news_id)


async def close_db():
    """Close DB pool on shutdown."""
    global _pool
    if _pool:
        await _pool.close()
        logger.info("DB pool closed.")
