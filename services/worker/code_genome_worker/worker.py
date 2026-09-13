import asyncio

from arq.connections import RedisSettings
from code_genome_api.config import get_settings
from code_genome_api.services.analysis import run_fake_analysis


async def analyze_repository(ctx: dict[str, object], run_id: str) -> None:
    del ctx
    await asyncio.to_thread(run_fake_analysis, run_id)


class WorkerSettings:
    functions = [analyze_repository]
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    max_jobs = 4
    job_timeout = 300
