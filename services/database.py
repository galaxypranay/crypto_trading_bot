"""
Railway PostgreSQL database service.

Tables:
  seen_news    — duplicate/old news rok ta hai
  news_log     — channel mein post hui har news ka record
  trade_log    — har approve/reject trade ka record
"""
import os
import logging
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

_pool = None
_memory_fallback: set[str] = set()
DB_AVAILABLE = False

# Kitne ghante purani news ignore karein (old news filter)
NEWS_MAX_AGE_HOURS = 6


async def init_db():
    """Connect to Postgres and create all tables."""
    global _pool, DB_AVAILABLE
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        logger.warning("DATABASE_URL not set — using in-memory fallback.")
        return

    try:
        import asyncpg
        _pool = await asyncpg.create_pool(db_url, min_size=1, max_size=5)
        async with _pool.acquire() as conn:
            await conn.execute("""
                -- Seen news IDs — duplicate prevention
                CREATE TABLE IF NOT EXISTS seen_news (
                    id         TEXT PRIMARY KEY,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                );

                -- News posted to channel
                CREATE TABLE IF NOT EXISTS news_log (
                    id           TEXT PRIMARY KEY,
                    title        TEXT,
                    coin         TEXT,
                    source       TEXT,
                    url          TEXT,
                    published_at TIMESTAMPTZ,
                    posted_at    TIMESTAMPTZ DEFAULT NOW()
                );

                -- Trade signals approved/rejected
                CREATE TABLE IF NOT EXISTS trade_log (
                    id         SERIAL PRIMARY KEY,
                    coin       TEXT,
                    direction  TEXT,
                    confidence INTEGER,
                    leverage   INTEGER,
                    entry      NUMERIC,
                    tp         NUMERIC,
                    sl         NUMERIC,
                    news_title TEXT,
                    status     TEXT,        -- 'approved' / 'rejected' / 'failed'
                    created_at TIMESTAMPTZ DEFAULT NOW()
                );
            """)
        DB_AVAILABLE = True
        logger.info("PostgreSQL connected — all tables ready.")
    except Exception as e:
        logger.error(f"DB init failed — using in-memory fallback: {e}")


# ── Seen news ─────────────────────────────────────────────────

async def is_seen(news_id: str) -> bool:
    if not DB_AVAILABLE:
        return news_id in _memory_fallback
    try:
        async with _pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id FROM seen_news WHERE id=$1", news_id
            )
            return row is not None
    except Exception as e:
        logger.error(f"DB is_seen error: {e}")
        return news_id in _memory_fallback


async def mark_seen(news_id: str):
    if not DB_AVAILABLE:
        _memory_fallback.add(news_id)
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


# ── Old news filter ───────────────────────────────────────────

def is_too_old(published_at: datetime) -> bool:
    """True agar news NEWS_MAX_AGE_HOURS se zyada purani ho."""
    if published_at.tzinfo is None:
        published_at = published_at.replace(tzinfo=timezone.utc)
    age = datetime.now(timezone.utc) - published_at
    return age > timedelta(hours=NEWS_MAX_AGE_HOURS)


# ── News log ──────────────────────────────────────────────────

async def log_news(article: dict):
    """Channel mein post hui news ko DB mein save karo."""
    if not DB_AVAILABLE:
        return
    try:
        async with _pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO news_log (id, title, coin, source, url, published_at)
                VALUES ($1, $2, $3, $4, $5, $6)
                ON CONFLICT DO NOTHING
            """,
                article["id"],
                article["title"],
                article["coin"],
                article["source"],
                article["url"],
                article["published_at"],
            )
    except Exception as e:
        logger.error(f"DB log_news error: {e}")


# ── Trade log ─────────────────────────────────────────────────

async def log_trade(signal: dict, status: str):
    """
    Trade signal DB mein save karo.
    status = 'approved' / 'rejected' / 'failed'
    """
    if not DB_AVAILABLE:
        return
    try:
        async with _pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO trade_log
                  (coin, direction, confidence, leverage, entry, tp, sl, news_title, status)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
            """,
                signal.get("coin"),
                signal.get("direction"),
                signal.get("confidence"),
                signal.get("leverage"),
                signal.get("entry"),
                signal.get("tp"),
                signal.get("sl"),
                signal.get("news_title"),
                status,
            )
    except Exception as e:
        logger.error(f"DB log_trade error: {e}")


# ── Cleanup ───────────────────────────────────────────────────

async def cleanup_old_seen_news():
    """7 din se purane seen_news records delete karo."""
    if not DB_AVAILABLE:
        return
    try:
        async with _pool.acquire() as conn:
            deleted = await conn.execute(
                "DELETE FROM seen_news WHERE created_at < NOW() - INTERVAL '7 days'"
            )
        logger.info(f"DB cleanup: {deleted}")
    except Exception as e:
        logger.error(f"DB cleanup error: {e}")


async def close_db():
    global _pool
    if _pool:
        await _pool.close()
        logger.info("DB pool closed.")
