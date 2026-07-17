"""Persistent, resumable, crash-safe stage runtime.

Safety properties:

* **Atomic manifests** — every save goes through the atomic JSON writer; a
  killed process can never leave a truncated manifest.
* **Schema version** — the manifest carries an explicit schema version.
* **Interruption recovery** — a stage found in ``running`` on startup was
  interrupted; it is marked ``interrupted`` and re-executed.
* **Config-change invalidation** — when the effective configuration hash
  changes, previously completed stages are marked ``stale`` and re-run.
* **Output checksums** — completed stages record the SHA-256 of their output
  file; resume only trusts a completed stage whose artifact still exists and
  matches its checksum (no false "completed" state).
* **Retry budget** — each stage has a bounded attempt count.
* **Concurrency** — a pid-stamped lock file rejects a second live worker on
  the same job directory; locks from dead processes are reclaimed.
* **No tracebacks in manifests** — errors are stored as redacted
  type + message; full tracebacks go only to the debug log when enabled.
"""

from __future__ import annotations

import os
import traceback
from datetime import datetime, UTC
from pathlib import Path
from typing import Any, Callable

from .errors import JobConcurrencyError, JobStateError, StageRetryExhaustedError
from .schemas import JobManifest, StageRecord
from .security import redacted_exception_text
from .utils import ensure_dir, hash_value, load_json, save_json, sha256_file

MANIFEST_SCHEMA_VERSION = "10.1"

_VALID_TRANSITIONS: dict[str, set[str]] = {
    "pending": {"running", "skipped"},
    "running": {"completed", "failed", "interrupted"},
    "completed": {"stale", "running"},
    "failed": {"running"},
    "interrupted": {"running"},
    "stale": {"running"},
    "skipped": {"running"},
}


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class ResumableJobRuntime:
    """Idempotent stage runtime with crash recovery and integrity checks."""

    def __init__(
        self,
        run_dir: str | Path,
        *,
        job_id: str,
        topic: str,
        config: dict[str, Any],
        max_stage_attempts: int = 5,
        debug_tracebacks: bool = False,
        on_stage_event: Any | None = None,
        cancellation_token: Any | None = None,
    ):
        # on_stage_event(stage_id, event, record_dict) is invoked on
        # "cached", "started", "completed" and "failed"; it must never raise
        # into the pipeline. cancellation_token exposes a truthy `.cancelled`
        # checked BEFORE each stage — a set token stops after the current
        # safe stage (never mid-provider-request).
        self.on_stage_event = on_stage_event
        self.cancellation_token = cancellation_token
        self.run_dir = ensure_dir(run_dir)
        self.manifest_path = self.run_dir / "job_manifest.json"
        self.lock_path = self.run_dir / "job.lock"
        self.max_stage_attempts = int(max_stage_attempts)
        self.debug_tracebacks = bool(debug_tracebacks)
        self._acquire_lock()

        config_hash = hash_value(config, 24)
        existing = load_json(self.manifest_path)
        if isinstance(existing, dict):
            self.manifest = JobManifest.model_validate(existing)
            self.manifest.schema_version = MANIFEST_SCHEMA_VERSION
            self._recover_interrupted_stages()
            if self.manifest.config_hash and self.manifest.config_hash != config_hash:
                self._invalidate_for_config_change(config_hash)
            self.manifest.config_hash = config_hash
            self._save()
        else:
            self.manifest = JobManifest(
                schema_version=MANIFEST_SCHEMA_VERSION,
                job_id=job_id,
                topic=topic,
                run_dir=str(self.run_dir),
                config_hash=config_hash,
            )
            self._save()

    # -- locking -----------------------------------------------------------
    def _acquire_lock(self) -> None:
        my_pid = os.getpid()
        for _ in range(2):
            try:
                fd = os.open(self.lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    handle.write(f"{my_pid}\n{_now()}\n")
                return
            except FileExistsError:
                try:
                    holder = int(self.lock_path.read_text(encoding="utf-8").splitlines()[0])
                except (OSError, ValueError, IndexError):
                    holder = -1
                if holder == my_pid:
                    return  # re-entrant within the same process
                if not _pid_alive(holder):
                    # Stale lock from a dead worker: reclaim it.
                    try:
                        self.lock_path.unlink()
                    except OSError:
                        pass
                    continue
                raise JobConcurrencyError(
                    f"Job directory {self.run_dir} is locked by live pid {holder}; "
                    "refusing concurrent execution of the same job"
                ) from None
        raise JobConcurrencyError(f"Could not acquire job lock at {self.lock_path}")

    def release_lock(self) -> None:
        try:
            holder = int(self.lock_path.read_text(encoding="utf-8").splitlines()[0])
            if holder == os.getpid():
                self.lock_path.unlink()
        except (OSError, ValueError, IndexError):
            pass

    # -- recovery / invalidation -------------------------------------------
    def _recover_interrupted_stages(self) -> None:
        for record in self.manifest.stages.values():
            if record.status == "running":
                record.status = "interrupted"
                record.error_type = "Interrupted"
                record.error_message = "Stage was left running by a previous worker; it will re-run."

    def _invalidate_for_config_change(self, new_hash: str) -> None:
        for record in self.manifest.stages.values():
            if record.status == "completed":
                record.status = "stale"
        self.manifest.warnings.append(
            f"Configuration changed (hash {self.manifest.config_hash} -> {new_hash}); "
            "completed stages were invalidated."
        )

    # -- persistence ---------------------------------------------------------
    def _save(self) -> None:
        self.manifest.updated_at = _now()
        save_json(self.manifest_path, self.manifest)

    def _transition(self, record: StageRecord, new_status: str) -> None:
        allowed = _VALID_TRANSITIONS.get(record.status, set())
        if new_status not in allowed:
            raise JobStateError(
                f"Invalid stage transition {record.status!r} -> {new_status!r} for stage {record.stage_id}"
            )
        record.status = new_status  # type: ignore[assignment]

    # -- execution -------------------------------------------------------------
    def execute(
        self,
        stage_id: str,
        input_payload: Any,
        fn: Callable[[], Any],
        *,
        force: bool = False,
    ) -> Any:
        if self.cancellation_token is not None and getattr(self.cancellation_token, "cancelled", False):
            from .errors import JobCancelledError

            self._emit_stage_event(stage_id, "cancelled", None)
            raise JobCancelledError(
                f"Run cancelled before stage {stage_id}; the job is resumable with the same job id."
            )
        input_hash = hash_value(input_payload, 32)
        current = self.manifest.stages.get(stage_id)
        if current and current.status == "completed" and current.input_hash == input_hash and not force:
            output_path = Path(current.output_path) if current.output_path else None
            if output_path and output_path.exists():
                if not current.output_sha256 or sha256_file(output_path) == current.output_sha256:
                    self._emit_stage_event(stage_id, "cached", current)
                    return load_json(output_path)
                # Artifact was modified/corrupted after completion: re-run.
                self._transition(current, "stale")
                self._save()

        record = current or StageRecord(stage_id=stage_id)
        if record.attempt >= self.max_stage_attempts and not force:
            raise StageRetryExhaustedError(
                f"Stage {stage_id} exceeded {self.max_stage_attempts} attempts; "
                "pass force=True after fixing the underlying failure"
            )
        self._transition(record, "running")
        record.attempt += 1
        record.input_hash = input_hash
        record.started_at = _now()
        record.error_type = ""
        record.error_message = ""
        self.manifest.stages[stage_id] = record
        self.manifest.status = "running"
        self._save()
        self._emit_stage_event(stage_id, "started", record)
        try:
            result = fn()
            serializable = result.model_dump(mode="json") if hasattr(result, "model_dump") else result
            output = self.run_dir / "stage_outputs" / f"{stage_id}.json"
            ensure_dir(output.parent)
            save_json(output, serializable)
            self._transition(record, "completed")
            record.completed_at = _now()
            record.output_path = str(output)
            record.output_sha256 = sha256_file(output)
            self._save()
            self._emit_stage_event(stage_id, "completed", record)
            return serializable
        except Exception as exc:
            self._transition(record, "failed")
            record.completed_at = _now()
            record.error_type = type(exc).__name__
            record.error_message = redacted_exception_text(exc, 1000)
            if self.debug_tracebacks:
                debug_path = self.run_dir / "debug" / f"{stage_id}.traceback.txt"
                ensure_dir(debug_path.parent)
                debug_path.write_text(traceback.format_exc(limit=20), encoding="utf-8")
                record.metadata["traceback_file"] = str(debug_path)
            self.manifest.status = "failed"
            self._save()
            self._emit_stage_event(stage_id, "failed", record)
            self.release_lock()
            raise

    def _emit_stage_event(self, stage_id: str, event: str, record: StageRecord | None) -> None:
        """Deliver a stage event to the optional observer; observer failures
        are swallowed so UI callbacks can never break the pipeline."""
        if self.on_stage_event is None:
            return
        try:
            payload = record.model_dump(mode="json") if record is not None else {}
            self.on_stage_event(stage_id, event, payload)
        except Exception:
            pass

    # -- terminal states ---------------------------------------------------------
    def mark_completed(self) -> None:
        self.manifest.status = "completed"
        self._save()
        self.release_lock()

    def stage_completed(self, stage_id: str) -> bool:
        record = self.manifest.stages.get(stage_id)
        if not (record and record.status == "completed"):
            return False
        if record.output_path and not Path(record.output_path).exists():
            return False
        return True
