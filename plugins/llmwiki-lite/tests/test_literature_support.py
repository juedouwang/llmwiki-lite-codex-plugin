"""Isolated fixtures, never read the user's registry or activity stores."""
import copy
import json
from datetime import datetime, timezone
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from llmwiki_registry import register_project  # noqa: E402
from literature_catalog import LiteratureCatalog, item_revision, literature_collect  # noqa: E402

NOW = datetime(2026, 9, 19, 12, tzinfo=timezone.utc)


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="llmwiki-literature-test-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.home = str(self.root / "home")
        self.projects = []
        for name in ("alpha", "beta"):
            source = self.root / name / "source"
            source.mkdir(parents=True)
            project = register_project(str(source), name=name, wiki_root=str(self.root / name / "wiki"), state_root=str(self.root / name / "state"), home=self.home)["project"]
            self.projects.append(project)
        self.project, self.other = self.projects
        self.pid = self.project["id"]
        self.catalog = LiteratureCatalog(self.project["wiki_root"])
        self.request = 0

    def collect(self, locator="10.1234/test", **kw):
        self.request += 1
        return literature_collect(kw.pop("project_id", self.pid), locator, f"{self.request:032x}", home=self.home, **kw)

    def item(self, result):
        return self.catalog.get_item(result["item_id"])

    def settings(self, **changes):
        config = {"enabled": True, "literature_enabled": True, "timezone": "Asia/Shanghai", "project_ids": [p["id"] for p in self.projects],
                  "daily_time": "19:00", "start_date": "2026-08-01",
                  "runtime": {"automation_id": "test-only-not-real", "target_thread_id": "test-only", "literature_bound_at": NOW.isoformat()}}
        config.update(changes)
        (Path(self.home) / "reports-settings.json").write_text(json.dumps(config), encoding="utf-8")
        return config

    def source(self, text="论文 https://doi.org/10.1234/test", locator="record:one", kind="record", **kw):
        return {"id": item_revision([self.pid, kind, locator]), "project_id": self.pid, "kind": kind,
                "locator": locator, "revision": item_revision(text), "text": text, "occurred_at": NOW.isoformat(),
                "observed_at": NOW.isoformat(), "certainty": "event", **kw}


class MemoryProvider:
    """Contract fixture only, never advertised as a connected source helper."""
    def __init__(self, sources=(), gaps=()):
        self.sources = list(sources)
        self.gaps = list(gaps)
        self.allowed = True
        self.read_error = False
        self.read_count = 0
        self.inventory_count = 0

    def inventory(self, project, **kw):
        self.inventory_count += 1
        return [copy.deepcopy(s) for s in self.sources if s["project_id"] == project["id"]], list(self.gaps)

    def read(self, project, descriptor, **kw):
        self.read_count += 1
        if self.read_error:
            raise OSError("fixture source unreadable")
        return next((copy.deepcopy(s) for s in self.sources if s["project_id"] == project["id"] and s["locator"] == descriptor["locator"] and s["kind"] == descriptor["kind"]), None)

    def authorize(self, project, source, **kw):
        return self.allowed and source["project_id"] == project["id"]
