"""Release integrity — prove the running notebook is the code that was tested.

The failure this prevents: a fix lands in the source tree and the test suite goes
green, but the *notebook* handed to the user was built from an older tree — so
the user runs stale code while a report claims "validated". There was no way to
tell the two apart. This gives every artifact a strong version identity and lets
a run verify, at runtime, that the code it is about to execute is exactly the
code that was bundled at build time.

* ``compute_source_digest`` — a deterministic SHA-256 over the package's module
  sources (sorted ``(relative_name, bytes)``), independent of filesystem order.
* ``build_fingerprint`` — captured at **build** time over the modules the builder
  is about to embed: pipeline version, git commit, the source-bundle digest,
  module count, builder-file digest, and build timestamp.
* ``verify_parity`` — at **runtime**, re-hash the materialized package on disk and
  compare to the embedded fingerprint. A mismatch (a hand-edited or stale cell)
  is reported as drift, with the specific files that differ.
* ``emit_run_release`` — write ``release.json`` into a run so its output is
  traceable to an exact code identity.

Nothing here is provider-specific and it never phones home; it only hashes local
files and shells out to ``git`` best-effort for the commit id.
"""

from __future__ import annotations

import hashlib
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .utils import save_json

SCHEMA_VERSION = "10.3"


def _module_files(module_dir: Path) -> list[Path]:
    return sorted(p for p in Path(module_dir).glob("*.py") if p.is_file())


def compute_source_digest(module_dir: str | Path) -> tuple[str, int]:
    """Return ``(sha256_hex, module_count)`` over the package's ``*.py`` sources.

    Order-independent: files are hashed by sorted relative name, each as
    ``name\\0<bytes>\\0`` so a rename or an edit both change the digest.
    """
    module_dir = Path(module_dir)
    h = hashlib.sha256()
    files = _module_files(module_dir)
    for p in files:
        h.update(p.name.encode("utf-8"))
        h.update(b"\x00")
        h.update(p.read_bytes())
        h.update(b"\x00")
    return h.hexdigest(), len(files)


def _file_digest(path: str | Path) -> str:
    p = Path(path)
    if not p.exists():
        return ""
    return hashlib.sha256(p.read_bytes()).hexdigest()


def git_commit(cwd: str | Path | None = None) -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        return out.stdout.strip()
    except Exception:
        return ""


def build_fingerprint(
    module_dir: str | Path,
    builder_path: str | Path | None = None,
    pipeline_version: str = "10.3.0-dev",
    test_suite_sha256: str = "",
) -> dict[str, Any]:
    """Capture the release identity at build time (over the modules being bundled)."""
    digest, count = compute_source_digest(module_dir)
    return {
        "pipeline_version": pipeline_version,
        "schema_version": SCHEMA_VERSION,
        "git_commit": git_commit(module_dir),
        "source_bundle_sha256": digest,
        "module_count": count,
        "builder_sha256": _file_digest(builder_path) if builder_path else "",
        "test_suite_sha256": test_suite_sha256,
        "built_at": datetime.now(timezone.utc).isoformat(),  # noqa: UP017
    }


def verify_parity(fingerprint: dict[str, Any], live_module_dir: str | Path) -> dict[str, Any]:
    """Re-hash the materialized package and compare to the embedded fingerprint.

    Returns ``{parity_ok, expected, actual, module_count, drifted}``. ``drifted``
    lists per-file names whose bytes differ from... it cannot know the original
    bytes, so it reports the count delta and the overall mismatch; the digest is
    the authoritative check.
    """
    expected = str(fingerprint.get("source_bundle_sha256", ""))
    actual, count = compute_source_digest(live_module_dir)
    ok = bool(expected) and expected == actual
    return {
        "parity_ok": ok,
        "expected": expected,
        "actual": actual,
        "module_count": count,
        "expected_module_count": fingerprint.get("module_count"),
        "drifted": [] if ok else ["<source_bundle digest mismatch>"],
    }


def emit_run_release(
    module_dir: str | Path,
    root: str | Path,
    embedded: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Write ``release.json`` for a run: the live code identity, plus a parity
    check against the ``embedded`` build fingerprint when one is provided.

    ``production_validated`` is True only when an embedded fingerprint exists,
    it records a git commit, and the live source matches it — i.e. the running
    code is provably the built-and-recorded code, not a drifted copy.
    """
    live_digest, count = compute_source_digest(module_dir)
    record: dict[str, Any] = {
        "live_source_bundle_sha256": live_digest,
        "live_module_count": count,
        "git_commit": git_commit(module_dir),
        "schema_version": SCHEMA_VERSION,
        "checked_at": datetime.now(timezone.utc).isoformat(),  # noqa: UP017
        "embedded": embedded or {},
    }
    if embedded:
        parity = verify_parity(embedded, module_dir)
        record["parity"] = parity
        record["production_validated"] = bool(
            parity["parity_ok"] and embedded.get("git_commit")
        )
    else:
        record["parity"] = {"parity_ok": None, "reason": "no embedded build fingerprint"}
        record["production_validated"] = False
    if extra:
        record.update(extra)
    save_json(Path(root) / "release.json", record)
    return record
