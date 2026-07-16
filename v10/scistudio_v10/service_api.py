"""FastAPI service shell.

Endpoints:

* ``POST /jobs`` — validate the request, start generation in a background
  thread, return ``202`` with a job id. (Long-running generation belongs in a
  real worker/queue for multi-instance deployments; this in-process thread
  model is single-worker only and documented as such — distributed job
  execution is **not** implemented.)
* ``GET /jobs/{job_id}`` — job status from the persisted job manifest.

Errors are structured and redacted; raw exception strings are never exposed.
"""

# NOTE: no `from __future__ import annotations` here — FastAPI must resolve
# the locally-defined request/response models from real (non-string) annotations.
import threading
import uuid
from pathlib import Path
from typing import Any

from .security import redacted_exception_text


def create_app(studio_factory):
    """Build the FastAPI app. FastAPI is imported lazily."""
    try:
        from fastapi import FastAPI, HTTPException
        from fastapi.responses import JSONResponse
        from pydantic import BaseModel, Field
    except ImportError as exc:
        raise RuntimeError("Install fastapi and uvicorn to enable the REST service") from exc

    class GenerateRequest(BaseModel):
        topic: str = Field(min_length=3, max_length=500)
        reference_video: str = Field(min_length=1, max_length=4096)
        plan_only: bool = True
        render_video: bool = False
        job_id: str | None = Field(default=None, max_length=120)

    class JobAccepted(BaseModel):
        job_id: str
        status: str = "accepted"
        detail: str = (
            "Generation started in a background thread. Poll "
            "GET /jobs/{job_id} for status. Deploy a dedicated "
            "worker/queue for multi-instance production use."
        )

    class JobStatus(BaseModel):
        job_id: str
        status: str
        mode: str = ""
        video: str = ""
        error_type: str = ""
        error_message: str = ""

    app = FastAPI(
        title="Scientific Motion Studio V10",
        description="Single-worker generation API. Long-running generation "
        "requires a worker/queue in multi-instance deployments; "
        "distributed job execution is not implemented here.",
    )
    jobs: dict[str, dict[str, Any]] = {}
    jobs_lock = threading.Lock()

    def _run_job(job_id: str, payload: GenerateRequest) -> None:
        try:
            studio = studio_factory()
            result = studio.run(
                payload.topic,
                payload.reference_video,
                plan_only=payload.plan_only,
                render_video=payload.render_video,
                job_id=job_id,
            )
            with jobs_lock:
                jobs[job_id] = {"status": "completed", "result": result}
        except Exception as exc:
            with jobs_lock:
                jobs[job_id] = {
                    "status": "failed",
                    "error_type": type(exc).__name__,
                    "error_message": redacted_exception_text(exc, 500),
                }

    @app.post("/jobs", status_code=202, response_model=JobAccepted)
    def create_job(payload: GenerateRequest) -> JobAccepted:
        reference = Path(payload.reference_video)
        if not reference.exists() or not reference.is_file():
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "invalid_reference_video",
                    "message": "reference_video must be an existing file path on the server",
                },
            )
        job_id = payload.job_id or f"api-{uuid.uuid4().hex[:12]}"
        with jobs_lock:
            if job_id in jobs and jobs[job_id].get("status") in {"accepted", "running"}:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "error": "job_exists",
                        "message": f"job {job_id} is already running",
                    },
                )
            jobs[job_id] = {"status": "running"}
        worker = threading.Thread(target=_run_job, args=(job_id, payload), daemon=True)
        worker.start()
        return JobAccepted(job_id=job_id)

    @app.get("/jobs/{job_id}", response_model=JobStatus)
    def get_job(job_id: str) -> JobStatus:
        with jobs_lock:
            state = jobs.get(job_id)
        if state is None:
            raise HTTPException(
                status_code=404,
                detail={
                    "error": "unknown_job",
                    "message": f"no job with id {job_id}",
                },
            )
        result = state.get("result") or {}
        return JobStatus(
            job_id=job_id,
            status=str(state.get("status", "unknown")),
            mode=str(result.get("mode", "")),
            video=str(result.get("video", "")),
            error_type=str(state.get("error_type", "")),
            error_message=str(state.get("error_message", "")),
        )

    @app.exception_handler(Exception)
    def _unhandled(request, exc):  # noqa: ANN001 - FastAPI signature
        return JSONResponse(
            status_code=500,
            content={
                "error": "internal_error",
                "error_type": type(exc).__name__,
                "message": redacted_exception_text(exc, 300),
            },
        )

    return app
