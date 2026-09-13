import time
from collections.abc import Callable

from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import SessionLocal
from ..models import AnalysisRun, utc_now

SessionFactory = Callable[[], Session]


def run_fake_analysis(run_id: str, session_factory: SessionFactory = SessionLocal) -> None:
    """Exercise the durable job lifecycle without claiming repository analysis occurred."""
    with session_factory() as db:
        run = db.get(AnalysisRun, run_id)
        if run is None or run.state in {"SUCCEEDED", "FAILED"}:
            return
        run.state = "RUNNING"
        run.progress = 0.25
        run.started_at = utc_now()
        db.commit()

    delay = get_settings().job_delay_seconds
    if delay:
        time.sleep(delay)

    with session_factory() as db:
        run = db.get(AnalysisRun, run_id)
        if run is None:
            return
        run.completed_at = utc_now()
        run.progress = 1.0
        if run.simulate_failure:
            run.state = "FAILED"
            run.error_code = "SIMULATED_FAILURE"
            run.error_detail = "The foundation test job was asked to simulate a failure."
            run.diagnostics = ["No repository contents were read or analyzed."]
        else:
            run.state = "SUCCEEDED"
            run.diagnostics = [
                "Foundation lifecycle completed. No repository evidence was analyzed in this run."
            ]
        db.commit()
