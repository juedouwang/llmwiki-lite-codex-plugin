"""
Strict literature catalog management.

Implements D-10, F-10~F-14 contracts:
- Pure link-based entries (DOI/arXiv/URL)
- Strong identity deduplication (case-insensitive DOI, arXiv base ID)
- Attachment management (PDF upload/download)
- Old route compatibility (preserve existing literature reading)

Storage: <wiki_root>/.literature/catalog.json
History: <wiki_root>/.literature/history/

Schema version: 1
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

# Maximum limits per D-10
MAX_TITLE_LENGTH = 1000
MAX_AUTHORS = 100
MAX_AUTHOR_LENGTH = 200
MAX_VENUE_LENGTH = 300
MAX_URLS = 20
MAX_ATTACHMENTS = 20
MAX_SOURCE_REFS = 100
MAX_TAGS = 20
MAX_TAG_LENGTH = 40
MAX_READING_NOTE_PATHS = 20
YEAR_MIN = 1500
YEAR_MAX = 2100

# Attachment upload limits
MAX_ATTACHMENT_SIZE = 50 * 1024 * 1024  # 50 MiB


class LiteratureCatalogError(Exception):
    """Base exception for catalog operations."""
    pass


class RevisionConflictError(LiteratureCatalogError):
    """Raised when expected_revision does not match current."""
    pass


def _utc_now() -> str:
    """Return current UTC time in RFC3339 format (second precision)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _new_id() -> str:
    """Generate new lowercase UUID4 hex (32 chars)."""
    import uuid
    return uuid.uuid4().hex


def _canonical_json(obj: Any) -> bytes:
    """Produce canonical JSON for hashing (D-03)."""
    return json.dumps(
        obj,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False
    ).encode("utf-8")


def _compute_sha256(data: bytes) -> str:
    """Compute SHA256 hex digest."""
    return hashlib.sha256(data).hexdigest()


def _normalize_doi(doi: str) -> str:
    """
    Normalize DOI: remove prefix, trim, lowercase.

    Per F-11: case-insensitive comparison.
    """
    normalized = doi.strip()
    # Remove common DOI prefixes
    for prefix in ["doi:", "DOI:", "https://doi.org/", "http://dx.doi.org/"]:
        if normalized.lower().startswith(prefix.lower()):
            normalized = normalized[len(prefix):]
    normalized = normalized.strip().lower()
    return normalized if normalized else ""


def _normalize_arxiv(arxiv_id: str) -> str:
    """
    Normalize arXiv ID: extract base ID without version.

    Per F-11: same base ID with different versions (v1, v2) = same entry.
    Supports old format (e.g., "cs/0703001") and new format (e.g., "1803.12345").
    """
    normalized = arxiv_id.strip()
    # Remove common arXiv prefixes
    for prefix in ["arxiv:", "arXiv:", "https://arxiv.org/abs/", "http://arxiv.org/abs/"]:
        if normalized.lower().startswith(prefix.lower()):
            normalized = normalized[len(prefix):]
    normalized = normalized.strip()

    # Remove version suffix (vN)
    match = re.match(r"^(.+?)(v\d+)?$", normalized, re.IGNORECASE)
    if match:
        base_id = match.group(1)
        return base_id if base_id else ""

    return normalized if normalized else ""


def _normalize_url(url: str) -> str:
    """
    Normalize URL per F-11:
    - Only http/https allowed
    - scheme/host lowercase
    - Remove fragment and utm_* tracking params
    - Preserve other query fields and path case
    - Reject URLs with username/password
    """
    url = url.strip()
    if not url:
        return ""

    try:
        parsed = urlparse(url)
    except Exception:
        return ""

    # Only http/https
    if parsed.scheme.lower() not in ("http", "https"):
        return ""

    # Reject URLs with credentials
    if parsed.username or parsed.password:
        return ""

    # Normalize scheme and host to lowercase
    scheme = parsed.scheme.lower()
    netloc = parsed.hostname.lower() if parsed.hostname else ""
    if parsed.port:
        netloc += f":{parsed.port}"

    # Filter out utm_* tracking params
    query_params = parse_qs(parsed.query, keep_blank_values=True)
    filtered_params = {k: v for k, v in query_params.items() if not k.startswith("utm_")}

    # Rebuild query string
    if filtered_params:
        # Flatten single-value lists for cleaner URLs
        flat_params = {}
        for k, v in filtered_params.items():
            flat_params[k] = v[0] if len(v) == 1 else v
        query = urlencode(flat_params, doseq=True)
    else:
        query = ""

    # Rebuild URL without fragment
    normalized = urlunparse((scheme, netloc, parsed.path, parsed.params, query, ""))
    return normalized


def _validate_title(title: str) -> tuple[bool, str]:
    """Validate title per D-10."""
    title = title.strip()
    if not title:
        return False, "Title cannot be empty"
    if len(title) > MAX_TITLE_LENGTH:
        return False, f"Title exceeds {MAX_TITLE_LENGTH} characters"
    # Reject if title is just a file extension
    if re.fullmatch(r"\.[a-z]{2,5}", title, re.IGNORECASE):
        return False, "Title cannot be just a file extension"
    return True, ""


def _validate_authors(authors: list[str]) -> tuple[bool, str]:
    """Validate authors per D-10."""
    if len(authors) > MAX_AUTHORS:
        return False, f"Authors exceed maximum of {MAX_AUTHORS}"
    for author in authors:
        if len(author) > MAX_AUTHOR_LENGTH:
            return False, f"Author name exceeds {MAX_AUTHOR_LENGTH} characters"
    return True, ""


def _validate_year(year: Optional[int]) -> tuple[bool, str]:
    """Validate year per D-10."""
    if year is None:
        return True, ""
    if not isinstance(year, int) or year < YEAR_MIN or year > YEAR_MAX:
        return False, f"Year must be between {YEAR_MIN} and {YEAR_MAX}"
    return True, ""


def _validate_venue(venue: str) -> tuple[bool, str]:
    """Validate venue per D-10."""
    if len(venue) > MAX_VENUE_LENGTH:
        return False, f"Venue exceeds {MAX_VENUE_LENGTH} characters"
    return True, ""


def _validate_identifiers(identifiers: dict[str, Optional[str]]) -> tuple[bool, str]:
    """Validate identifiers structure."""
    if "doi" not in identifiers or "arxiv" not in identifiers:
        return False, "Identifiers must have 'doi' and 'arxiv' keys"
    return True, ""


def _validate_item(item: dict[str, Any]) -> tuple[bool, str]:
    """
    Validate a catalog item against schema D-10.

    Returns (valid, error_message).
    """
    # Required fields
    required = ["id", "title", "authors", "year", "venue", "publication_type",
                "identifiers", "urls", "attachments", "identity_status",
                "reading_status", "collection_source", "source_refs", "tags",
                "reading_note_paths", "archived", "created_at", "updated_at"]
    for field in required:
        if field not in item:
            return False, f"Missing required field: {field}"

    # Validate title
    valid, msg = _validate_title(item["title"])
    if not valid:
        return False, msg

    # Validate authors
    if not isinstance(item["authors"], list):
        return False, "Authors must be a list"
    valid, msg = _validate_authors(item["authors"])
    if not valid:
        return False, msg

    # Validate year
    valid, msg = _validate_year(item["year"])
    if not valid:
        return False, msg

    # Validate venue
    if not isinstance(item["venue"], str):
        return False, "Venue must be a string"
    valid, msg = _validate_venue(item["venue"])
    if not valid:
        return False, msg

    # Validate publication_type
    valid_types = ["article", "conference", "preprint", "thesis", "book", "patent", "unknown"]
    if item["publication_type"] not in valid_types:
        return False, f"Invalid publication_type, must be one of: {', '.join(valid_types)}"

    # Validate identifiers
    valid, msg = _validate_identifiers(item["identifiers"])
    if not valid:
        return False, msg

    # Validate URLs
    if not isinstance(item["urls"], list):
        return False, "URLs must be a list"
    if len(item["urls"]) > MAX_URLS:
        return False, f"URLs exceed maximum of {MAX_URLS}"
    for url_entry in item["urls"]:
        if not isinstance(url_entry, dict):
            return False, "Each URL entry must be a dict"
        if "id" not in url_entry or "url" not in url_entry or "kind" not in url_entry:
            return False, "URL entry must have id, url, and kind"
        valid_kinds = ["publisher", "preprint", "pdf", "project", "other"]
        if url_entry["kind"] not in valid_kinds:
            return False, f"Invalid URL kind: {url_entry['kind']}"

    # Validate attachments
    if not isinstance(item["attachments"], list):
        return False, "Attachments must be a list"
    if len(item["attachments"]) > MAX_ATTACHMENTS:
        return False, f"Attachments exceed maximum of {MAX_ATTACHMENTS}"
    for attachment in item["attachments"]:
        if not isinstance(attachment, dict):
            return False, "Each attachment must be a dict"
        required_attach_fields = ["id", "path", "sha256", "media_type", "added_at"]
        for field in required_attach_fields:
            if field not in attachment:
                return False, f"Attachment missing field: {field}"

    # Validate identity_status
    if item["identity_status"] not in ["verified", "unverified"]:
        return False, "identity_status must be 'verified' or 'unverified'"

    # Validate reading_status
    if item["reading_status"] not in ["unread", "reading", "read"]:
        return False, "reading_status must be 'unread', 'reading', or 'read'"

    # Validate collection_source
    if item["collection_source"] not in ["manual", "agent", "migration"]:
        return False, "collection_source must be 'manual', 'agent', or 'migration'"

    # Validate source_refs
    if not isinstance(item["source_refs"], list):
        return False, "source_refs must be a list"
    if len(item["source_refs"]) > MAX_SOURCE_REFS:
        return False, f"source_refs exceed maximum of {MAX_SOURCE_REFS}"

    # Validate tags
    if not isinstance(item["tags"], list):
        return False, "tags must be a list"
    if len(item["tags"]) > MAX_TAGS:
        return False, f"tags exceed maximum of {MAX_TAGS}"
    for tag in item["tags"]:
        if not isinstance(tag, str):
            return False, "Each tag must be a string"
        if len(tag) > MAX_TAG_LENGTH:
            return False, f"Tag exceeds {MAX_TAG_LENGTH} characters"

    # Validate reading_note_paths
    if not isinstance(item["reading_note_paths"], list):
        return False, "reading_note_paths must be a list"
    if len(item["reading_note_paths"]) > MAX_READING_NOTE_PATHS:
        return False, f"reading_note_paths exceed maximum of {MAX_READING_NOTE_PATHS}"

    # Validate archived
    if not isinstance(item["archived"], bool):
        return False, "archived must be a boolean"

    return True, ""


def _check_identity_conflict(
    new_item: dict[str, Any],
    existing_items: list[dict[str, Any]]
) -> tuple[bool, Optional[str], list[str]]:
    """
    Check for identity conflicts per F-11.

    Returns: (has_conflict, conflicting_item_id, warnings)

    Rules:
    1. Same DOI → same literature (if DOIs are non-empty)
    2. Same arXiv base ID → same literature (if arXiv IDs are non-empty and no conflicting DOI)
    3. Same normalized publisher URL → same literature (if no conflicting strong IDs)
    4. Same title (NFKC/casefold/whitespace-folded) → warning only, not auto-merge
    """
    new_doi = new_item["identifiers"].get("doi")
    new_arxiv = new_item["identifiers"].get("arxiv")
    new_title = new_item["title"]

    # Normalize new identifiers
    new_doi_norm = _normalize_doi(new_doi) if new_doi else ""
    new_arxiv_norm = _normalize_arxiv(new_arxiv) if new_arxiv else ""

    # Normalize title for comparison
    new_title_norm = unicodedata.normalize("NFKC", new_title).casefold()
    new_title_norm = re.sub(r"\s+", " ", new_title_norm).strip()

    # Collect normalized publisher URLs from new item
    new_publisher_urls = set()
    for url_entry in new_item.get("urls", []):
        if url_entry.get("kind") == "publisher":
            normalized = _normalize_url(url_entry.get("url", ""))
            if normalized:
                new_publisher_urls.add(normalized)

    warnings = []

    for existing in existing_items:
        # Skip if same ID (updating existing item)
        if existing.get("id") == new_item.get("id"):
            continue

        existing_doi = existing["identifiers"].get("doi")
        existing_arxiv = existing["identifiers"].get("arxiv")

        existing_doi_norm = _normalize_doi(existing_doi) if existing_doi else ""
        existing_arxiv_norm = _normalize_arxiv(existing_arxiv) if existing_arxiv else ""

        # Rule 1: Same DOI
        if new_doi_norm and existing_doi_norm and new_doi_norm == existing_doi_norm:
            return True, existing["id"], warnings

        # Rule 2: Same arXiv ID (no conflicting DOI)
        if new_arxiv_norm and existing_arxiv_norm and new_arxiv_norm == existing_arxiv_norm:
            # Check if DOIs conflict
            if new_doi_norm and existing_doi_norm and new_doi_norm != existing_doi_norm:
                # Conflicting DOIs for same arXiv ID - needs confirmation
                return True, existing["id"], warnings
            return True, existing["id"], warnings

        # Rule 3: Same publisher URL (no conflicting strong IDs)
        existing_publisher_urls = set()
        for url_entry in existing.get("urls", []):
            if url_entry.get("kind") == "publisher":
                normalized = _normalize_url(url_entry.get("url", ""))
                if normalized:
                    existing_publisher_urls.add(normalized)

        common_urls = new_publisher_urls & existing_publisher_urls
        if common_urls:
            # Check for conflicting strong identifiers
            doi_conflict = (new_doi_norm and existing_doi_norm and
                           new_doi_norm != existing_doi_norm)
            arxiv_conflict = (new_arxiv_norm and existing_arxiv_norm and
                             new_arxiv_norm != existing_arxiv_norm)
            if doi_conflict or arxiv_conflict:
                # Same URL but different strong IDs - needs confirmation
                return True, existing["id"], warnings
            return True, existing["id"], warnings

        # Rule 4: Same title - warning only
        existing_title_norm = unicodedata.normalize("NFKC", existing["title"]).casefold()
        existing_title_norm = re.sub(r"\s+", " ", existing_title_norm).strip()
        if new_title_norm == existing_title_norm:
            warnings.append(f"Title matches existing item {existing['id']}: {existing['title']}")

    return False, None, warnings


class LiteratureCatalog:
    """
    Strict literature catalog manager.

    Maintains <wiki_root>/.literature/catalog.json with:
    - Pure link-based entries
    - Strong identity deduplication
    - Attachment tracking
    - History preservation
    """

    def __init__(self, wiki_root: str):
        """Initialize catalog at wiki_root/.literature/."""
        self.wiki_root = Path(wiki_root).resolve()
        self.literature_dir = self.wiki_root / ".literature"
        self.catalog_path = self.literature_dir / "catalog.json"
        self.history_dir = self.literature_dir / "history"

        # Ensure directories exist
        self.literature_dir.mkdir(parents=True, exist_ok=True)
        self.history_dir.mkdir(parents=True, exist_ok=True)

    def _load_catalog(self) -> dict[str, Any]:
        """Load catalog from disk, or return empty structure if not exists."""
        if not self.catalog_path.exists():
            return {
                "schema_version": 1,
                "items": [],
                "dismissed_candidates": []
            }

        try:
            with open(self.catalog_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            # Validate schema version
            if data.get("schema_version") != 1:
                raise LiteratureCatalogError(
                    f"Unsupported catalog schema version: {data.get('schema_version')}"
                )

            return data
        except json.JSONDecodeError as e:
            raise LiteratureCatalogError(f"Catalog JSON decode error: {e}")
        except Exception as e:
            raise LiteratureCatalogError(f"Failed to load catalog: {e}")

    def _save_catalog(self, catalog: dict[str, Any], expected_revision: Optional[str] = None):
        """
        Save catalog to disk with atomic replace and revision check.

        Args:
            catalog: Catalog dict to save
            expected_revision: If provided, checks current file revision first

        Raises:
            RevisionConflictError: If expected_revision doesn't match current
        """
        # Check revision if expected
        if expected_revision is not None:
            current_revision = self.get_revision()
            if current_revision != expected_revision:
                raise RevisionConflictError(
                    f"Catalog revision mismatch: expected {expected_revision}, "
                    f"got {current_revision}"
                )

        # Save history before overwriting (only if file exists and has content)
        if self.catalog_path.exists() and self.catalog_path.stat().st_size > 0:
            self._save_history()

        # Atomic write: temp file + rename
        temp_path = self.catalog_path.with_suffix(".tmp")
        try:
            with open(temp_path, "w", encoding="utf-8", newline="\n") as f:
                json.dump(catalog, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())

            # Atomic replace
            temp_path.replace(self.catalog_path)
        except Exception as e:
            if temp_path.exists():
                temp_path.unlink()
            raise LiteratureCatalogError(f"Failed to save catalog: {e}")

    def _save_history(self):
        """Save current catalog to history directory."""
        if not self.catalog_path.exists():
            return

        # History filename: catalog_<timestamp>_<short-hash>.json
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        revision = self.get_revision()
        short_hash = revision[:12] if revision else "empty"
        history_name = f"catalog_{timestamp}_{short_hash}.json"
        history_path = self.history_dir / history_name

        try:
            shutil.copy2(self.catalog_path, history_path)
        except Exception:
            # History save failure shouldn't block main operation
            pass

    def get_revision(self) -> str:
        """
        Get current catalog revision (SHA256 of file bytes).

        Returns empty string if file doesn't exist.
        """
        if not self.catalog_path.exists():
            return ""

        try:
            with open(self.catalog_path, "rb") as f:
                data = f.read()
            return _compute_sha256(data)
        except Exception:
            return ""

    def list_items(
        self,
        *,
        status: Optional[str] = None,
        archived: Optional[bool] = None
    ) -> dict[str, Any]:
        """
        List catalog items with optional filters.

        Args:
            status: Filter by reading_status (unread/reading/read)
            archived: Filter by archived status

        Returns:
            {
                "items": [...],
                "revision": "...",
                "count": N
            }
        """
        catalog = self._load_catalog()
        items = catalog["items"]

        # Apply filters
        if status is not None:
            items = [item for item in items if item.get("reading_status") == status]
        if archived is not None:
            items = [item for item in items if item.get("archived") == archived]

        return {
            "items": items,
            "revision": self.get_revision(),
            "count": len(items)
        }

    def get_item(self, item_id: str) -> Optional[dict[str, Any]]:
        """Get a single item by ID."""
        catalog = self._load_catalog()
        for item in catalog["items"]:
            if item["id"] == item_id:
                return item
        return None

    def create_item(
        self,
        title: str,
        *,
        authors: Optional[list[str]] = None,
        year: Optional[int] = None,
        venue: str = "",
        publication_type: str = "unknown",
        doi: Optional[str] = None,
        arxiv: Optional[str] = None,
        urls: Optional[list[dict[str, Any]]] = None,
        tags: Optional[list[str]] = None,
        collection_source: str = "manual",
        source_refs: Optional[list[Any]] = None,
        expected_revision: Optional[str] = None,
        request_key: str = ""
    ) -> dict[str, Any]:
        """
        Create a new literature item.

        Performs identity deduplication per F-11.

        Returns:
            {
                "ok": True,
                "item": {...},
                "revision": "...",
                "warnings": [...]
            }
        """
        catalog = self._load_catalog()

        # Normalize identifiers
        doi_norm = _normalize_doi(doi) if doi else None
        arxiv_norm = _normalize_arxiv(arxiv) if arxiv else None

        # Build new item
        now = _utc_now()
        new_item = {
            "id": _new_id(),
            "title": title.strip(),
            "authors": authors or [],
            "year": year,
            "venue": venue,
            "publication_type": publication_type,
            "identifiers": {
                "doi": doi_norm,
                "arxiv": arxiv_norm
            },
            "urls": urls or [],
            "attachments": [],
            "identity_status": "unverified",
            "reading_status": "unread",
            "collection_source": collection_source,
            "source_refs": source_refs or [],
            "tags": tags or [],
            "reading_note_paths": [],
            "archived": False,
            "created_at": now,
            "updated_at": now
        }

        # Validate item
        valid, msg = _validate_item(new_item)
        if not valid:
            return {
                "ok": False,
                "error": {"code": "INVALID_INPUT", "message": msg}
            }

        # Check for identity conflicts
        has_conflict, conflicting_id, warnings = _check_identity_conflict(
            new_item, catalog["items"]
        )

        if has_conflict:
            return {
                "ok": False,
                "error": {
                    "code": "IDENTITY_CONFLICT",
                    "message": f"Item conflicts with existing item {conflicting_id}",
                    "details": {"conflicting_id": conflicting_id}
                }
            }

        # Add to catalog
        catalog["items"].append(new_item)
        self._save_catalog(catalog, expected_revision)

        return {
            "ok": True,
            "item": new_item,
            "revision": self.get_revision(),
            "warnings": warnings,
            "request_key": request_key
        }

    def update_item(
        self,
        item_id: str,
        updates: dict[str, Any],
        *,
        expected_revision: Optional[str] = None,
        request_key: str = ""
    ) -> dict[str, Any]:
        """
        Update an existing item.

        Args:
            item_id: ID of item to update
            updates: Dict of fields to update
            expected_revision: Expected catalog revision (CAS)
            request_key: Request tracking key

        Returns success response with updated item.
        """
        catalog = self._load_catalog()

        # Find item
        item = None
        item_index = -1
        for i, it in enumerate(catalog["items"]):
            if it["id"] == item_id:
                item = it
                item_index = i
                break

        if item is None:
            return {
                "ok": False,
                "error": {"code": "NOT_FOUND", "message": f"Item not found: {item_id}"}
            }

        # Apply updates
        updated_item = {**item, **updates, "updated_at": _utc_now()}

        # Validate updated item
        valid, msg = _validate_item(updated_item)
        if not valid:
            return {
                "ok": False,
                "error": {"code": "INVALID_INPUT", "message": msg}
            }

        # Replace in catalog
        catalog["items"][item_index] = updated_item
        self._save_catalog(catalog, expected_revision)

        return {
            "ok": True,
            "item": updated_item,
            "revision": self.get_revision(),
            "request_key": request_key
        }

    def remove_item(
        self,
        item_id: str,
        *,
        expected_revision: Optional[str] = None,
        request_key: str = ""
    ) -> dict[str, Any]:
        """
        Remove an item from catalog (soft delete to dismissed_candidates).

        Per F-12: provides 10-second undo via history.
        """
        catalog = self._load_catalog()

        # Find and remove item
        item = None
        for i, it in enumerate(catalog["items"]):
            if it["id"] == item_id:
                item = catalog["items"].pop(i)
                break

        if item is None:
            return {
                "ok": False,
                "error": {"code": "NOT_FOUND", "message": f"Item not found: {item_id}"}
            }

        # Add to dismissed candidates
        dismissed_entry = {
            "item": item,
            "dismissed_at": _utc_now()
        }
        catalog["dismissed_candidates"].append(dismissed_entry)

        self._save_catalog(catalog, expected_revision)

        return {
            "ok": True,
            "removed_item": item,
            "revision": self.get_revision(),
            "request_key": request_key
        }

    def restore_item(
        self,
        item_id: str,
        *,
        expected_revision: Optional[str] = None,
        request_key: str = ""
    ) -> dict[str, Any]:
        """Restore a dismissed item back to the catalog."""
        catalog = self._load_catalog()

        # Find in dismissed candidates
        dismissed_item = None
        dismissed_index = -1
        for i, entry in enumerate(catalog["dismissed_candidates"]):
            if entry["item"]["id"] == item_id:
                dismissed_item = entry["item"]
                dismissed_index = i
                break

        if dismissed_item is None:
            return {
                "ok": False,
                "error": {"code": "NOT_FOUND", "message": f"Dismissed item not found: {item_id}"}
            }

        # Check for conflicts with current items
        has_conflict, conflicting_id, warnings = _check_identity_conflict(
            dismissed_item, catalog["items"]
        )

        if has_conflict:
            return {
                "ok": False,
                "error": {
                    "code": "IDENTITY_CONFLICT",
                    "message": f"Cannot restore: conflicts with existing item {conflicting_id}",
                    "details": {"conflicting_id": conflicting_id}
                }
            }

        # Restore to items
        restored_item = {**dismissed_item, "updated_at": _utc_now()}
        catalog["items"].append(restored_item)
        catalog["dismissed_candidates"].pop(dismissed_index)

        self._save_catalog(catalog, expected_revision)

        return {
            "ok": True,
            "item": restored_item,
            "revision": self.get_revision(),
            "warnings": warnings,
            "request_key": request_key
        }
