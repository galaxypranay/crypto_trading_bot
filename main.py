import asyncio
import logging
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI
from apscheduler.schedulers.asyncio import AsyncIOScheduler

import config
from config import validate_config
from pipeline import run_pipeline
from services.database import init_db, close_db, cleanup_old_seen_news
from handlers.trade_bot import get_trade_app, restore_pending_signals
from handlers.news_bot import get_news_app

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting Crypto Trading Bot...")

    # ── Config validation ─────────────────────────────────────
    try:
        validate_config()
    except EnvironmentError as e:
        logger.critical(f"Config error: {e}")
        sys.exit(1)

    # ── DB init ───────────────────────────────────────────────
    await init_db()

    # ── Trade Bot start + pending signals restore ─────────────
    trade_app = get_trade_app()
    await trade_app.initialize()
    await trade_app.start()
    await trade_app.updater.start_polling(drop_pending_updates=True)
    logger.info("Trade bot polling started.")

    # DB se pending signals restore karo (restart-safe)
    await restore_pending_signals()

    # ── News Bot start ────────────────────────────────────────
    news_app = get_news_app()
    await news_app.initialize()
    await news_app.start()
    await news_app.updater.start_polling(drop_pending_updates=True)
    logger.info("News bot polling started.")

    # ── Scheduler setup ───────────────────────────────────────
    scheduler.add_job(
        run_pipeline,
        trigger="interval",
        minutes=2,
        id="news_pipeline",
        max_instances=1,          # Ek waqt mein sirf ek pipeline run
        misfire_grace_time=30,
    )

    scheduler.add_job(
        cleanup_old_seen_news,
        trigger="cron",
        hour=2,
        minute=0,
        id="db_cleanup",
    )

    scheduler.start()
    logger.info("Scheduler started (pipeline every 2 min).")

    # Startup pe ek baar turant run karo
    asyncio.create_task(run_pipeline())
    logger.info("Initial pipeline run scheduled.")

    yield

    # ── Graceful shutdown ─────────────────────────────────────
    logger.info("Shutting down...")
    scheduler.shutdown(wait=False)

    await trade_app.updater.stop()
    await trade_app.stop()
    await trade_app.shutdown()

    await news_app.updater.stop()
    await news_app.stop()
    await news_app.shutdown()

    await close_db()
    logger.info("Stopped cleanly.")


app = FastAPI(
    title="Crypto AI Trading Bot",
    version="2.0.0",
    lifespan=lifespan,
)


@app.get("/")
async def root():
    return {
        "status": "running",
        "version": "2.0.0",
        "risk_mode": config.RISK_MODE,
        "min_confidence": config.MIN_CONFIDENCE,
        "trade_size_usdt": config.TRADE_SIZE_USDT,
        "freemodel": config.FREEMODEL_MODEL,
        "openrouter": config.OPENROUTER_MODEL,
    }


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/trigger")
async def trigger_pipeline():
    """Pipeline manually trigger karo (testing ke liye)."""
    asyncio.create_task(run_pipeline())
    return {"status": "pipeline triggered"}


@app.get("/status")
async def status():
    """Scheduler jobs ki status dekho."""
    jobs = [
        {"id": j.id, "next_run": str(j.next_run_time)}
        for j in scheduler.get_jobs()
    ]
    return {"scheduler_running": scheduler.running, "jobs": jobs}
