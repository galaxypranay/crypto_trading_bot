import asyncio
import logging
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI
from apscheduler.schedulers.asyncio import AsyncIOScheduler

import config
from config import validate_config
from pipeline import run_pipeline
from handlers.trade_bot import get_trade_app

# ── Logging setup ─────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

# ── Scheduler ─────────────────────────────────────────────────
scheduler = AsyncIOScheduler()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup & shutdown logic."""
    logger.info("Starting Crypto Trading Bot...")

    # Validate all env vars are set
    try:
        validate_config()
    except EnvironmentError as e:
        logger.critical(f"Config error: {e}")
        sys.exit(1)

    # Start trade bot polling (for Approve/Reject callbacks)
    trade_app = get_trade_app()
    await trade_app.initialize()
    await trade_app.start()
    await trade_app.updater.start_polling(drop_pending_updates=True)
    logger.info("Trade bot polling started.")

    # Schedule pipeline: run every 2 minutes
    scheduler.add_job(
        run_pipeline,
        trigger="interval",
        minutes=2,
        id="news_pipeline",
        max_instances=1,          # Never run two pipelines at once
        misfire_grace_time=30,
    )
    scheduler.start()
    logger.info("Scheduler started — pipeline runs every 2 minutes.")

    # Run pipeline once immediately on startup
    asyncio.create_task(run_pipeline())

    yield  # App is running

    # ── Shutdown ──
    logger.info("Shutting down...")
    scheduler.shutdown(wait=False)
    await trade_app.updater.stop()
    await trade_app.stop()
    await trade_app.shutdown()
    logger.info("Bot stopped cleanly.")


# ── FastAPI app ───────────────────────────────────────────────
app = FastAPI(
    title="Crypto AI Trading Bot",
    description="AI-powered crypto news trading automation",
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/")
async def root():
    return {
        "status": "running",
        "risk_mode": config.RISK_MODE,
        "min_confidence": config.MIN_CONFIDENCE,
        "ai_model": config.AI_MODEL,
    }


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/trigger")
async def trigger_pipeline():
    """Manually trigger the news pipeline (for testing)."""
    asyncio.create_task(run_pipeline())
    return {"status": "pipeline triggered"}


@app.get("/status")
async def status():
    """Show scheduler and pipeline status."""
    jobs = []
    for job in scheduler.get_jobs():
        jobs.append({
            "id": job.id,
            "next_run": str(job.next_run_time),
        })
    return {
        "scheduler_running": scheduler.running,
        "jobs": jobs,
        "risk_mode": config.RISK_MODE,
        "min_confidence": config.MIN_CONFIDENCE,
        "ai_model": config.AI_MODEL,
    }
