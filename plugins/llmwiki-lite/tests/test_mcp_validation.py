"""Required arguments are rejected before invoking any tool or leaking a traceback."""
import contextlib
import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import mcp_server as mcp  # noqa: E402
from llmwiki_core import LLMWikiError  # noqa: E402


class MCPValidationTests(unittest.TestCase):
    def call(self, name, args):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            result = mcp.handle({"id": 1, "method": "tools/call",
                                 "params": {"name": name, "arguments": args}})["result"]
        self.assertEqual(stderr.getvalue(), "")
        return result, json.loads(result["content"][0]["text"])

    def test_every_required_field_is_checked(self):
        for tool in mcp.TOOLS:
            required = tool["inputSchema"]["required"]
            for missing in required:
                with self.subTest(tool=tool["name"], missing=missing):
                    args = {key: "present" for key in required if key != missing}
                    result, payload = self.call(tool["name"], args)
                    self.assertTrue(result["isError"])
                    self.assertEqual(payload["error"], f"Missing required argument(s): {missing}")
                    with self.assertRaisesRegex(LLMWikiError, "Missing required"):
                        mcp.dispatch(tool["name"], args)

    def test_optional_arguments_can_be_omitted(self):
        with patch.object(mcp, "list_projects", return_value={"ok": True, "projects": []}) as target:
            result, payload = self.call("llmwiki_project_list", {})
            self.assertFalse(result["isError"])
            self.assertTrue(payload["ok"])
            target.assert_called_once_with()
        with patch.object(mcp, "read_record", return_value={"ok": True}) as target:
            result, _ = self.call("llmwiki_record_read", {"project_root": "root", "record_id": "id"})
            self.assertFalse(result["isError"])
            target.assert_called_once_with(project_root="root", record_id="id")

    def test_extra_fields_and_non_objects_still_rejected(self):
        result, payload = self.call("llmwiki_project_list", {"unexpected": True})
        self.assertTrue(result["isError"])
        self.assertIn("Unknown argument(s): unexpected", payload["error"])
        for args in (None, [], "bad"):
            with self.subTest(args=args):
                result, payload = self.call("llmwiki_progress_get", args)
                self.assertTrue(result["isError"])
                self.assertIn("must be an object", payload["error"])

    def test_explicit_empty_project_never_falls_back(self):
        for name in ("llmwiki_progress_get", "llmwiki_progress_context_write"):
            tool = next(tool for tool in mcp.TOOLS if tool["name"] == name)
            for value in (None, "", "   "):
                with self.subTest(tool=name, value=value):
                    args = dict.fromkeys(tool["inputSchema"]["required"], "present")
                    args["project_root"] = value
                    with patch("llmwiki_registry.get_project") as resolve:
                        result, _ = self.call(name, args)
                        self.assertTrue(result["isError"])
                        resolve.assert_not_called()


if __name__ == "__main__":
    unittest.main()
