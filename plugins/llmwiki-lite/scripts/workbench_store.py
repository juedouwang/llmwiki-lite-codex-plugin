"""
Workbench mechanical storage layer.

Provides SQLite-based storage for:
- Repository and worktree identity
- Session tracking
- Activity ledger
- Job queue and leasing
- Operation state
- Maintenance proposals
- Report metadata
- Audit receipts

All operations use transactions, CAS for optimistic locking,
and provide structured success/failure responses.
"""

import sqlite3
import json
import os
import hashlib
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple
from contextlib import contextmanager


# Schema version
SCHEMA_VERSION = 1


class WorkbenchStoreError(Exception):
    """Base exception for storage errors."""
    pass


class RevisionConflictError(WorkbenchStoreError):
    """Raised when expected_revision does not match current revision."""
    pass


class StateChangedError(WorkbenchStoreError):
    """Raised when entity state has changed unexpectedly."""
    pass


class SchemaVersionError(WorkbenchStoreError):
    """Raised when schema version is incompatible."""
    pass


def canonical_json(obj: Any) -> bytes:
    """
    Produce canonical JSON for hashing/comparison.

    Fixed format: UTF-8, sorted keys, compact separators, no NaN/Infinity.
    """
    return json.dumps(
        obj,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False
    ).encode("utf-8")


def compute_sha256(data: bytes) -> str:
    """Compute SHA256 hex digest."""
    return hashlib.sha256(data).hexdigest()


def new_id() -> str:
    """Generate new lowercase UUID4 hex (32 chars)."""
    return uuid.uuid4().hex


def utc_now() -> str:
    """Return current UTC time in RFC3339 format (second precision)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ===== Response Format =====

def success_response(data: Any = None, revision: Optional[str] = None, request_id: Optional[str] = None) -> Dict[str, Any]:
    """Standard success response."""
    resp = {"ok": True, "data": data if data is not None else {}}
    if revision is not None:
        resp["revision"] = revision
    if request_id is not None:
        resp["request_id"] = request_id
    return resp


def error_response(code: str, message: str, details: Optional[Dict] = None, request_id: Optional[str] = None) -> Dict[str, Any]:
    """Standard error response."""
    resp = {
        "ok": False,
        "error": {
            "code": code,
            "message": message,
            "details": details or {}
        }
    }
    if request_id is not None:
        resp["request_id"] = request_id
    return resp


# ===== Schema Definition =====

SCHEMA_SQL = """
-- Core mechanical data tables for workbench execution
-- Schema version: 1

CREATE TABLE IF NOT EXISTS repositories (
    id TEXT PRIMARY KEY NOT NULL,
    project_id TEXT NOT NULL,
    common_dir TEXT NOT NULL,
    object_format TEXT NOT NULL,
    access_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(common_dir)
);

CREATE TABLE IF NOT EXISTS worktrees (
    id TEXT PRIMARY KEY NOT NULL,
    repo_id TEXT NOT NULL REFERENCES repositories(id),
    root_path TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(root_path)
);

CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY NOT NULL,
    project_id TEXT NOT NULL,
    host TEXT NOT NULL,
    host_session_id TEXT NOT NULL,
    worktree_id TEXT,
    started_at TEXT,
    last_seen_at TEXT NOT NULL,
    consent_revision TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('active', 'closed', 'excluded')),
    UNIQUE(host, host_session_id)
);

CREATE TABLE IF NOT EXISTS cursors (
    id TEXT PRIMARY KEY NOT NULL,
    session_id TEXT NOT NULL REFERENCES sessions(id),
    source_key TEXT NOT NULL,
    generation INTEGER NOT NULL,
    position_json TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(session_id, source_key)
);

CREATE TABLE IF NOT EXISTS activities (
    id TEXT PRIMARY KEY NOT NULL,
    project_id TEXT NOT NULL,
    session_id TEXT,
    kind TEXT NOT NULL,
    source_key TEXT NOT NULL,
    source_revision TEXT NOT NULL,
    occurred_at TEXT,
    observed_at TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('active', 'redacted')),
    UNIQUE(project_id, source_key, source_revision)
);

CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY NOT NULL,
    project_id TEXT,
    kind TEXT NOT NULL,
    job_key TEXT NOT NULL UNIQUE,
    state TEXT NOT NULL,
    input_json TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    due_at TEXT NOT NULL,
    lease_owner TEXT,
    lease_until TEXT,
    lease_token_hash TEXT,
    attempt INTEGER NOT NULL DEFAULT 0,
    result_json TEXT,
    error_code TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS operations (
    id TEXT PRIMARY KEY NOT NULL,
    repo_id TEXT,
    worktree_id TEXT,
    action TEXT NOT NULL,
    state TEXT NOT NULL,
    request_key TEXT NOT NULL UNIQUE,
    request_hash TEXT NOT NULL,
    plan_json TEXT NOT NULL,
    result_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS maintenance (
    id TEXT PRIMARY KEY NOT NULL,
    project_id TEXT NOT NULL,
    page_path TEXT NOT NULL,
    base_revision TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    proposal_json TEXT NOT NULL,
    state TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(project_id, page_path, base_revision, input_hash)
);

CREATE TABLE IF NOT EXISTS reports (
    id TEXT PRIMARY KEY NOT NULL,
    scope TEXT NOT NULL CHECK(scope IN ('project', 'personal')),
    project_id TEXT,
    project_ids_json TEXT NOT NULL,
    kind TEXT NOT NULL,
    period_start TEXT NOT NULL,
    period_end TEXT NOT NULL,
    timezone TEXT NOT NULL,
    state TEXT NOT NULL,
    latest_revision TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS receipts (
    id TEXT PRIMARY KEY NOT NULL,
    project_id TEXT,
    kind TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    detail_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


# ===== WorkbenchStore Class =====

class WorkbenchStore:
    """
    SQLite-based mechanical storage for research workbench.

    Provides ACID transactions, CAS for conflict detection,
    schema migration, and structured responses.
    """

    def __init__(self, db_path: str, clock=None):
        """
        Initialize workbench store.

        Args:
            db_path: Path to SQLite database file
            clock: Optional callable returning UTC timestamp (for testing)
        """
        self.db_path = db_path
        self._clock = clock or utc_now
        self._conn: Optional[sqlite3.Connection] = None

    def _get_now(self) -> str:
        """Get current timestamp from injected clock."""
        return self._clock()

    def open(self):
        """Open database connection and verify/initialize schema."""
        os.makedirs(os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True)

        self._conn = sqlite3.connect(
            self.db_path,
            timeout=5.0,  # busy_timeout in seconds
            isolation_level=None  # autocommit mode, we manage transactions explicitly
        )
        self._conn.row_factory = sqlite3.Row

        # Set pragmas
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.execute("PRAGMA synchronous = FULL")

        # Check schema version
        current_version = self._conn.execute("PRAGMA user_version").fetchone()[0]

        if current_version == 0:
            # New database, initialize
            self._initialize_schema()
        elif current_version > SCHEMA_VERSION:
            # Future version, open as readonly
            raise SchemaVersionError(
                f"Database schema version {current_version} is higher than supported version {SCHEMA_VERSION}. "
                f"Please upgrade the application."
            )
        elif current_version < SCHEMA_VERSION:
            # Migration needed
            self._migrate_schema(current_version, SCHEMA_VERSION)

    def close(self):
        """Close database connection."""
        if self._conn:
            self._conn.close()
            self._conn = None

    def _initialize_schema(self):
        """Initialize database schema for new database."""
        with self.transaction():
            self._conn.executescript(SCHEMA_SQL)
            self._conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def _migrate_schema(self, from_version: int, to_version: int):
        """
        Migrate schema from one version to another.

        Args:
            from_version: Current schema version
            to_version: Target schema version

        Raises:
            SchemaVersionError: If migration fails
        """
        # For v1, no migrations yet
        # Future migrations would be implemented here
        if from_version == to_version:
            return

        raise SchemaVersionError(
            f"No migration path from schema version {from_version} to {to_version}"
        )

    @contextmanager
    def transaction(self):
        """Context manager for explicit transactions."""
        if not self._conn:
            raise WorkbenchStoreError("Database not open")

        self._conn.execute("BEGIN")
        try:
            yield self._conn
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    def compare_and_swap(
        self,
        table: str,
        id_field: str,
        id_value: str,
        updates: Dict[str, Any],
        expected_revision: str,
        revision_field: str = "revision"
    ) -> Tuple[bool, Optional[str]]:
        """
        Perform compare-and-swap update with optimistic locking.

        Args:
            table: Table name
            id_field: Primary key field name
            id_value: Primary key value
            updates: Fields to update
            expected_revision: Expected current revision
            revision_field: Name of revision field (if applicable)

        Returns:
            (success, current_revision)
            If success is False, current_revision contains the actual revision
        """
        with self.transaction():
            # Check if using content-based revision or separate field
            if revision_field in updates:
                # Content-based revision (like catalog)
                # Fetch current content
                cursor = self._conn.execute(
                    f"SELECT * FROM {table} WHERE {id_field} = ?",
                    (id_value,)
                )
                row = cursor.fetchone()

                if not row:
                    return (False, None)

                # Compute current revision
                current_content = canonical_json(dict(row))
                current_revision = compute_sha256(current_content)

                if current_revision != expected_revision:
                    return (False, current_revision)
            else:
                # Check revision field
                cursor = self._conn.execute(
                    f"SELECT {revision_field} FROM {table} WHERE {id_field} = ?",
                    (id_value,)
                )
                row = cursor.fetchone()

                if not row:
                    return (False, None)

                current_revision = row[revision_field]
                if current_revision != expected_revision:
                    return (False, current_revision)

            # Revision matches, perform update
            set_clause = ", ".join(f"{k} = ?" for k in updates.keys())
            self._conn.execute(
                f"UPDATE {table} SET {set_clause} WHERE {id_field} = ?",
                list(updates.values()) + [id_value]
            )

            # Return new revision if applicable
            if revision_field in updates:
                new_revision = updates[revision_field]
            else:
                cursor = self._conn.execute(
                    f"SELECT {revision_field} FROM {table} WHERE {id_field} = ?",
                    (id_value,)
                )
                row = cursor.fetchone()
                new_revision = row[revision_field] if row else None

            return (True, new_revision)

    # ===== Repository Operations =====

    def create_repository(
        self,
        project_id: str,
        common_dir: str,
        object_format: str,
        access_json: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Create a new repository entry."""
        repo_id = new_id()
        now = self._get_now()

        try:
            with self.transaction():
                self._conn.execute(
                    """
                    INSERT INTO repositories (id, project_id, common_dir, object_format, access_json, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (repo_id, project_id, common_dir, object_format, json.dumps(access_json, ensure_ascii=False), now)
                )

            return success_response({"repo_id": repo_id})

        except sqlite3.IntegrityError as e:
            if "UNIQUE constraint failed" in str(e) and "common_dir" in str(e):
                return error_response("ALREADY_EXISTS", f"Repository already registered: {common_dir}")
            raise

    def get_repository(self, repo_id: str) -> Optional[Dict[str, Any]]:
        """Get repository by ID."""
        cursor = self._conn.execute("SELECT * FROM repositories WHERE id = ?", (repo_id,))
        row = cursor.fetchone()
        if row:
            data = dict(row)
            data["access_json"] = json.loads(data["access_json"])
            return data
        return None

    # ===== Session Operations =====

    def upsert_session(
        self,
        project_id: str,
        host: str,
        host_session_id: str,
        consent_revision: str,
        worktree_id: Optional[str] = None,
        started_at: Optional[str] = None,
        state: str = "active"
    ) -> Dict[str, Any]:
        """Create or update session."""
        now = self._get_now()

        with self.transaction():
            cursor = self._conn.execute(
                "SELECT id FROM sessions WHERE host = ? AND host_session_id = ?",
                (host, host_session_id)
            )
            row = cursor.fetchone()

            if row:
                session_id = row["id"]
                self._conn.execute(
                    """
                    UPDATE sessions
                    SET project_id = ?, worktree_id = ?, last_seen_at = ?, consent_revision = ?, state = ?
                    WHERE id = ?
                    """,
                    (project_id, worktree_id, now, consent_revision, state, session_id)
                )
            else:
                session_id = new_id()
                self._conn.execute(
                    """
                    INSERT INTO sessions (id, project_id, host, host_session_id, worktree_id, started_at, last_seen_at, consent_revision, state)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (session_id, project_id, host, host_session_id, worktree_id, started_at, now, consent_revision, state)
                )

            return success_response({"session_id": session_id})

    # ===== Activity Operations =====

    def create_activity(
        self,
        project_id: str,
        kind: str,
        source_key: str,
        source_revision: str,
        evidence_json: Dict[str, Any],
        occurred_at: Optional[str] = None,
        session_id: Optional[str] = None,
        status: str = "active"
    ) -> Dict[str, Any]:
        """Create activity entry (idempotent by source_key + source_revision)."""
        now = self._get_now()
        activity_id = new_id()

        try:
            with self.transaction():
                self._conn.execute(
                    """
                    INSERT INTO activities (id, project_id, session_id, kind, source_key, source_revision, occurred_at, observed_at, evidence_json, status)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (activity_id, project_id, session_id, kind, source_key, source_revision, occurred_at, now, json.dumps(evidence_json, ensure_ascii=False), status)
                )

            return success_response({"activity_id": activity_id})

        except sqlite3.IntegrityError as e:
            if "UNIQUE constraint failed" in str(e):
                # Already exists, return existing ID
                cursor = self._conn.execute(
                    "SELECT id FROM activities WHERE project_id = ? AND source_key = ? AND source_revision = ?",
                    (project_id, source_key, source_revision)
                )
                row = cursor.fetchone()
                return success_response({"activity_id": row["id"], "already_exists": True})
            raise

    # ===== Job Operations =====

    def enqueue_job(
        self,
        kind: str,
        job_key: str,
        input_json: Dict[str, Any],
        due_at: str,
        project_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Enqueue a job (idempotent by job_key + input_hash)."""
        now = self._get_now()
        input_hash = compute_sha256(canonical_json(input_json))

        with self.transaction():
            # Check for existing job with same key
            cursor = self._conn.execute(
                "SELECT id, input_hash, state FROM jobs WHERE job_key = ?",
                (job_key,)
            )
            row = cursor.fetchone()

            if row:
                if row["input_hash"] == input_hash:
                    # Same input, return existing job
                    return success_response({"job_id": row["id"], "state": row["state"], "already_exists": True})
                else:
                    # Same key, different input
                    return error_response("IDEMPOTENCY_CONFLICT", "Job key exists with different input")

            # Create new job
            job_id = new_id()
            self._conn.execute(
                """
                INSERT INTO jobs (id, project_id, kind, job_key, state, input_json, input_hash, due_at, attempt, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (job_id, project_id, kind, job_key, "queued", json.dumps(input_json, ensure_ascii=False), input_hash, due_at, 0, now, now)
            )

            return success_response({"job_id": job_id, "state": "queued"})

    def claim_job(
        self,
        lease_owner: str,
        lease_duration_seconds: int = 300
    ) -> Optional[Dict[str, Any]]:
        """
        Claim next available job with atomic lease.

        Returns job data with lease_token, or None if no job available.
        """
        now_dt = datetime.now(timezone.utc)
        now = now_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        lease_until_dt = now_dt + timedelta(seconds=lease_duration_seconds)
        lease_until = lease_until_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

        lease_token = new_id()
        lease_token_hash = compute_sha256(lease_token.encode("utf-8"))

        with self.transaction():
            # Find next available job
            cursor = self._conn.execute(
                """
                SELECT id, project_id, kind, job_key, input_json, input_hash, due_at, attempt
                FROM jobs
                WHERE (state = 'queued' OR state = 'retry_wait')
                  AND due_at <= ?
                  AND (lease_until IS NULL OR lease_until < ?)
                ORDER BY due_at, created_at, id
                LIMIT 1
                """,
                (now, now)
            )
            row = cursor.fetchone()

            if not row:
                return None

            job_id = row["id"]

            # Claim it
            self._conn.execute(
                """
                UPDATE jobs
                SET state = 'leased', lease_owner = ?, lease_until = ?, lease_token_hash = ?, updated_at = ?
                WHERE id = ?
                """,
                (lease_owner, lease_until, lease_token_hash, now, job_id)
            )

            return {
                "job_id": job_id,
                "project_id": row["project_id"],
                "kind": row["kind"],
                "job_key": row["job_key"],
                "input_json": json.loads(row["input_json"]),
                "input_hash": row["input_hash"],
                "due_at": row["due_at"],
                "attempt": row["attempt"],
                "lease_token": lease_token,
                "lease_until": lease_until
            }

    def complete_job(
        self,
        job_id: str,
        lease_token: str,
        result_json: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Complete a job (requires valid lease)."""
        now = self._get_now()
        lease_token_hash = compute_sha256(lease_token.encode("utf-8"))

        with self.transaction():
            cursor = self._conn.execute(
                "SELECT state, lease_token_hash, lease_until FROM jobs WHERE id = ?",
                (job_id,)
            )
            row = cursor.fetchone()

            if not row:
                return error_response("NOT_FOUND", "Job not found")

            if row["state"] != "leased":
                return error_response("INVALID_STATE", f"Job is in state '{row['state']}', not leased")

            if row["lease_token_hash"] != lease_token_hash:
                return error_response("INVALID_LEASE", "Lease token does not match")

            # Check lease not expired
            if row["lease_until"] < now:
                return error_response("LEASE_EXPIRED", "Lease has expired")

            # Mark completed
            self._conn.execute(
                """
                UPDATE jobs
                SET state = 'succeeded', result_json = ?, lease_owner = NULL, lease_until = NULL, lease_token_hash = NULL, updated_at = ?
                WHERE id = ?
                """,
                (json.dumps(result_json, ensure_ascii=False), now, job_id)
            )

            return success_response({"job_id": job_id, "state": "succeeded"})

    # ===== Operation Operations =====

    def create_operation(
        self,
        action: str,
        request_key: str,
        plan_json: Dict[str, Any],
        repo_id: Optional[str] = None,
        worktree_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Create operation (idempotent by request_key + request_hash)."""
        now = self._get_now()
        request_hash = compute_sha256(canonical_json({
            "action": action,
            "plan": plan_json
        }))

        with self.transaction():
            # Check existing
            cursor = self._conn.execute(
                "SELECT id, request_hash, state FROM operations WHERE request_key = ?",
                (request_key,)
            )
            row = cursor.fetchone()

            if row:
                if row["request_hash"] == request_hash:
                    return success_response({"operation_id": row["id"], "state": row["state"], "already_exists": True})
                else:
                    return error_response("IDEMPOTENCY_CONFLICT", "Operation key exists with different request")

            # Create new
            operation_id = new_id()
            self._conn.execute(
                """
                INSERT INTO operations (id, repo_id, worktree_id, action, state, request_key, request_hash, plan_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (operation_id, repo_id, worktree_id, action, "queued", request_key, request_hash, json.dumps(plan_json, ensure_ascii=False), now, now)
            )

            return success_response({"operation_id": operation_id, "state": "queued"})

    def update_operation_state(
        self,
        operation_id: str,
        new_state: str,
        result_json: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Update operation state."""
        now = self._get_now()

        with self.transaction():
            updates = {"state": new_state, "updated_at": now}
            if result_json is not None:
                updates["result_json"] = json.dumps(result_json, ensure_ascii=False)

            set_clause = ", ".join(f"{k} = ?" for k in updates.keys())
            self._conn.execute(
                f"UPDATE operations SET {set_clause} WHERE id = ?",
                list(updates.values()) + [operation_id]
            )

            return success_response({"operation_id": operation_id, "state": new_state})

    def get_operation(self, operation_id: str) -> Optional[Dict[str, Any]]:
        """Get operation by ID."""
        cursor = self._conn.execute("SELECT * FROM operations WHERE id = ?", (operation_id,))
        row = cursor.fetchone()
        if row:
            data = dict(row)
            data["plan_json"] = json.loads(data["plan_json"])
            if data["result_json"]:
                data["result_json"] = json.loads(data["result_json"])
            return data
        return None
