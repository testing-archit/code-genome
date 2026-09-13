from arq import create_pool
from arq.connections import RedisSettings
from fastapi import BackgroundTasks

from .config import get_settings
from .services.analysis import run_fake_analysis


async def enqueue_analysis(run_id: str, background_tasks: BackgroundTasks) -> None:
    settings = get_settings()
    if settings.job_backend == "manual":
        return
    if settings.job_backend == "inline":
        background_tasks.add_task(run_fake_analysis, run_id)
        return
    pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    try:
        await pool.enqueue_job("analyze_repository", run_id, _job_id=run_id)
    finally:
        await pool.aclose()
