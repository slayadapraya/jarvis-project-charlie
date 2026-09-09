from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from app.actions import ActionStore
from app.database import Database
from app.prompts import system_prompt
from app.repositories import RepositoryError, RepositoryManager
from app.tool_parser import parse_text_tool_call


class DatabaseTests(unittest.TestCase):
    def test_session_project_and_messages_persist(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "jarvis.db"
            database = Database(path)
            database.add_message("default", "user", "Work on the API")
            database.set_project("default", "0:api")
            reopened = Database(path)
            self.assertEqual(reopened.get_session("default")["active_project"], "0:api")
            self.assertEqual(reopened.messages("default")[-1]["content"], "Work on the API")

    def test_thread_can_be_renamed(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "jarvis.db")
            renamed = database.rename_session("default", "  Main Jarvis Build  ")
            self.assertEqual(renamed["title"], "Main Jarvis Build")

    def test_legacy_import(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            legacy = root / "history.json"
            legacy.write_text(json.dumps({"old": [{"role": "user", "content": "hello"}]}))
            database = Database(root / "jarvis.db")
            self.assertEqual(database.import_legacy(legacy), 1)
            self.assertEqual(database.messages("old")[0]["content"], "hello")

    def test_cross_chat_memories_and_topics(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "jarvis.db")
            database.add_message("default", "user", "What did we decide about the API?")
            learned = database.learn_from_message("My name is Charlie")
            self.assertEqual(learned[0]["content"], "Charlie")
            self.assertEqual(database.list_memories()[0]["key"], "user_name")
            self.assertIn("What did we decide", database.recent_user_topics()[0])
            database.delete_memory("user_name")
            self.assertEqual(database.list_memories(), [])


class RepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "workspace"
        self.project = self.root / "demo"
        self.project.mkdir(parents=True)
        (self.project / "main.py").write_text("print('hello')\n", encoding="utf-8")
        self.manager = RepositoryManager({"roots": [str(self.root)], "allowed_commands": [["git", "status"]]})

    def tearDown(self):
        self.temp.cleanup()

    def test_project_and_file_are_resolved(self):
        self.assertEqual(self.manager.resolve_project("0:demo"), self.project.resolve())
        self.assertIn("print", self.manager.read_file("0:demo", "main.py"))

    def test_path_escape_is_rejected(self):
        with self.assertRaises(RepositoryError):
            self.manager.resolve_file("0:demo", "../../private.txt")

    def test_unknown_project_is_rejected(self):
        with self.assertRaises(RepositoryError):
            self.manager.resolve_project("0:../outside")

    def test_create_project_initializes_instructions(self):
        project = self.manager.create_project("new-demo", initialize_git=False)
        self.assertEqual(project["name"], "new-demo")
        self.assertTrue((self.root / "new-demo" / "JARVIS.md").is_file())

    def test_instruction_patch_format_is_applied(self):
        patch = """*** Begin Patch
*** Update File: main.py
@@
-print('hello')
+print('hello from JARVIS')
*** End Patch"""
        self.manager.apply_patch("0:demo", patch)
        self.assertEqual((self.project / "main.py").read_text(), "print('hello from JARVIS')\n")

    def test_instruction_patch_cannot_escape_project(self):
        patch = """*** Begin Patch
*** Add File: ../outside.txt
+not allowed
*** End Patch"""
        with self.assertRaises(RepositoryError):
            self.manager.apply_patch("0:demo", patch)

    def test_fenced_unified_diff_is_cleaned(self):
        patch = """```diff
--- a/main.py
+++ b/main.py
@@ -1 +1 @@
-print('hello')
+print('updated')
```"""
        self.manager.apply_patch("0:demo", patch)
        self.assertEqual((self.project / "main.py").read_text(), "print('updated')\n")


class PromptAndActionTests(unittest.TestCase):
    def test_prompt_contains_memory_and_project(self):
        prompt = system_prompt("coding", "Use repository A", "Repository ID: 0:A")
        self.assertIn("Use repository A", prompt)
        self.assertIn("Repository ID: 0:A", prompt)

    def test_actions_are_bound_to_session(self):
        store = ActionStore()
        action = store.queue("one", "run_command", {}, "test")
        with self.assertRaises(ValueError):
            store.pop(action.id, "two")
        self.assertEqual(store.pop(action.id, "one").description, "test")

    def test_text_tool_call_fallback(self):
        content = '''Here is the request:\n```json\n{"name":"send_email","arguments":{"to":"person@example.com","subject":"Hi","body":"Hello"}}\n```'''
        parsed = parse_text_tool_call(content)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed[0], "send_email")
        self.assertEqual(parsed[1]["subject"], "Hi")

    def test_unknown_text_call_is_not_accepted(self):
        self.assertIsNone(parse_text_tool_call('{"name":"delete_everything","arguments":{}}'))


class TailscaleScriptTests(unittest.TestCase):
    def test_existing_private_serve_writes_phone_url(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            mock_bin = root / "bin"
            state = root / "state"
            mock_bin.mkdir()
            tailscale = mock_bin / "tailscale"
            tailscale.write_text(
                """#!/usr/bin/env bash
if [[ "$1 $2" == "status --json" ]]; then
  echo '{"BackendState":"Running","Self":{"DNSName":"charlie-pc.example.ts.net."}}'
elif [[ "$1 $2" == "serve status" ]]; then
  echo 'https://charlie-pc.example.ts.net'
  echo '|-- / proxy http://127.0.0.1:8123'
else
  exit 2
fi
""",
                encoding="utf-8",
            )
            tailscale.chmod(0o755)
            script = Path(__file__).resolve().parent.parent / "scripts" / "enable_private_tailscale.sh"
            env = {
                **os.environ,
                "PATH": f"{mock_bin}:/usr/bin:/bin",
                "JARVIS_STATE_DIR": str(state),
                "JARVIS_TAILSCALE_ALLOW_SUDO": "0",
            }
            result = subprocess.run([str(script), "8123"], text=True, capture_output=True, env=env, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("https://charlie-pc.example.ts.net", result.stdout)
            self.assertEqual((state / "phone-url.txt").read_text().strip(), "https://charlie-pc.example.ts.net")


if __name__ == "__main__":
    unittest.main()
