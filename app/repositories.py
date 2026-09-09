from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path


class RepositoryError(ValueError):
    pass


@dataclass
class InstructionPatch:
    action: str
    path: str
    body: list[str]


class RepositoryManager:
    def __init__(self, config: dict):
        self.roots = [Path(os.path.expanduser(root)).resolve() for root in config.get("roots", [])]
        self.ignored = set(config.get("ignored_directories", []))
        self.max_files = int(config.get("max_context_files", 120))
        self.max_output = int(config.get("max_tool_output_chars", 20000))
        self.allowed_commands = [tuple(item) for item in config.get("allowed_commands", [])]
        for root in self.roots:
            root.mkdir(parents=True, exist_ok=True)

    def projects(self) -> list[dict]:
        found: list[dict] = []
        for root in self.roots:
            for child in sorted(root.iterdir(), key=lambda value: value.name.lower()):
                if child.is_dir() and child.name not in self.ignored:
                    found.append({
                        "id": f"{self.roots.index(root)}:{child.name}",
                        "name": child.name,
                        "path": str(child),
                        "git": (child / ".git").exists(),
                    })
        return found

    def create_project(self, name: str, initialize_git: bool = True) -> dict:
        clean = name.strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", clean):
            raise RepositoryError("Project name may contain letters, numbers, dots, underscores, and hyphens")
        if not self.roots:
            raise RepositoryError("No project workspace is configured")
        project = (self.roots[0] / clean).resolve()
        if project.exists():
            raise RepositoryError("A project with that name already exists")
        project.mkdir(parents=False)
        (project / "JARVIS.md").write_text(
            f"# {clean}\n\n## Purpose\n\nDescribe this project.\n\n## Commands\n\nAdd test, lint, and start commands here.\n",
            encoding="utf-8",
        )
        if initialize_git:
            subprocess.run(["git", "init"], cwd=project, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
        return next(item for item in self.projects() if item["path"] == str(project))

    def resolve_project(self, project_id: str | None) -> Path | None:
        if not project_id:
            return None
        try:
            root_index_text, name = project_id.split(":", 1)
            root = self.roots[int(root_index_text)]
        except (ValueError, IndexError):
            raise RepositoryError("Unknown project identifier")
        candidate = (root / name).resolve()
        if candidate.parent != root or not candidate.is_dir():
            raise RepositoryError("Project is outside an allowed workspace")
        return candidate

    def resolve_file(self, project_id: str, relative_path: str) -> Path:
        project = self.resolve_project(project_id)
        if project is None:
            raise RepositoryError("Select a project first")
        candidate = (project / relative_path).resolve()
        if candidate != project and project not in candidate.parents:
            raise RepositoryError("Path escapes the selected project")
        return candidate

    def tree(self, project_id: str) -> str:
        project = self.resolve_project(project_id)
        if project is None:
            return "No project selected."
        entries: list[str] = []
        for path in project.rglob("*"):
            if any(part in self.ignored for part in path.relative_to(project).parts):
                continue
            entries.append(str(path.relative_to(project)) + ("/" if path.is_dir() else ""))
            if len(entries) >= self.max_files:
                entries.append("... tree truncated ...")
                break
        return "\n".join(entries)

    def instructions(self, project_id: str) -> str:
        project = self.resolve_project(project_id)
        if project is None:
            return ""
        for name in ("JARVIS.md", "AGENTS.md", "README.md"):
            path = project / name
            if path.is_file():
                return path.read_text(encoding="utf-8", errors="replace")[:12000]
        return ""

    def git_status(self, project_id: str) -> str:
        project = self.resolve_project(project_id)
        if project is None:
            return "No project selected."
        return self._run(["git", "status", "--short", "--branch"], project)

    def read_file(self, project_id: str, relative_path: str, start_line: int = 1, end_line: int = 400) -> str:
        path = self.resolve_file(project_id, relative_path)
        if not path.is_file():
            raise RepositoryError("File does not exist")
        if path.stat().st_size > 2_000_000:
            raise RepositoryError("File is too large for direct context")
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        start = max(1, int(start_line))
        end = min(len(lines), max(start, int(end_line)))
        return "\n".join(f"{index}: {lines[index - 1]}" for index in range(start, end + 1))[: self.max_output]

    def search(self, project_id: str, query: str) -> str:
        project = self.resolve_project(project_id)
        if project is None:
            raise RepositoryError("Select a project first")
        result = subprocess.run(
            ["rg", "--line-number", "--hidden", "--glob", "!.git/**", "--", query, "."],
            cwd=project,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=15,
            check=False,
        )
        return (result.stdout or "No matches.")[: self.max_output]

    def command_allowed(self, argv: list[str]) -> bool:
        value = tuple(argv)
        return any(value[: len(prefix)] == prefix for prefix in self.allowed_commands)

    def run_allowed(self, project_id: str, argv: list[str]) -> str:
        if not argv or not self.command_allowed(argv):
            raise RepositoryError("Command is not in the configured allowlist")
        project = self.resolve_project(project_id)
        if project is None:
            raise RepositoryError("Select a project first")
        return self._run(argv, project, timeout=90)

    def apply_patch(self, project_id: str, patch: str) -> str:
        project = self.resolve_project(project_id)
        if project is None:
            raise RepositoryError("Select a project first")
        if len(patch) > 250_000:
            raise RepositoryError("Patch is too large")
        patch = self._clean_patch(patch)
        if patch.startswith("*** Begin Patch"):
            return self._apply_instruction_patch(project, patch)
        if not (patch.startswith("diff --git ") or patch.startswith("--- ")):
            raise RepositoryError(
                "Patch is not a unified diff. It must begin with 'diff --git', '--- a/', or '*** Begin Patch'."
            )
        check = subprocess.run(
            ["git", "apply", "--check", "--recount", "--whitespace=nowarn", "-"],
            cwd=project,
            input=patch,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=20,
            check=False,
        )
        if check.returncode:
            raise RepositoryError(f"Patch check failed:\n{check.stdout}")
        applied = subprocess.run(
            ["git", "apply", "--recount", "--whitespace=nowarn", "-"],
            cwd=project,
            input=patch,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=20,
            check=False,
        )
        if applied.returncode:
            raise RepositoryError(f"Patch failed:\n{applied.stdout}")
        return self._run(["git", "diff", "--stat"], project)

    @staticmethod
    def _clean_patch(patch: str) -> str:
        value = patch.strip().replace("\r\n", "\n")
        fenced = re.fullmatch(r"```(?:diff|patch)?\s*\n([\s\S]*?)\n```", value, re.IGNORECASE)
        if fenced:
            value = fenced.group(1).strip()
        positions = [position for marker in ("*** Begin Patch", "diff --git ", "--- a/") if (position := value.find(marker)) >= 0]
        if positions:
            value = value[min(positions):]
        if "*** End Patch" in value:
            value = value[: value.index("*** End Patch") + len("*** End Patch")]
        elif value.rstrip().endswith("```"):
            value = value.rstrip()[:-3].rstrip()
        return value.rstrip() + "\n"

    def _apply_instruction_patch(self, project: Path, patch: str) -> str:
        lines = patch.splitlines()
        sections: list[InstructionPatch] = []
        current: InstructionPatch | None = None
        header = re.compile(r"^\*\*\* (Update|Add|Delete) File: (.+)$")
        for line in lines[1:]:
            if line == "*** End Patch":
                break
            match = header.match(line)
            if match:
                current = InstructionPatch(match.group(1).lower(), match.group(2).strip(), [])
                sections.append(current)
            elif current is not None:
                current.body.append(line)
        if not sections:
            raise RepositoryError("Instruction patch contains no file sections")

        staged: dict[Path, str | None] = {}
        for section in sections:
            path = (project / section.path).resolve()
            if path != project and project not in path.parents:
                raise RepositoryError(f"Patch path escapes the active repository: {section.path}")
            if section.action == "add":
                if path.exists() or path in staged:
                    raise RepositoryError(f"Cannot add an existing file: {section.path}")
                added = [line[1:] for line in section.body if line.startswith("+")]
                staged[path] = "\n".join(added) + ("\n" if added else "")
            elif section.action == "delete":
                if not path.is_file():
                    raise RepositoryError(f"Cannot delete missing file: {section.path}")
                staged[path] = None
            else:
                if not path.is_file():
                    raise RepositoryError(f"Cannot update missing file: {section.path}")
                content = staged.get(path, path.read_text(encoding="utf-8", errors="replace"))
                if content is None:
                    raise RepositoryError(f"Cannot update deleted file: {section.path}")
                chunks: list[list[str]] = []
                chunk: list[str] = []
                for line in section.body:
                    if line.startswith("@@"):
                        if chunk:
                            chunks.append(chunk)
                            chunk = []
                    else:
                        chunk.append(line)
                if chunk:
                    chunks.append(chunk)
                if not chunks:
                    raise RepositoryError(f"No change hunks for {section.path}")
                for chunk in chunks:
                    old_lines = [line[1:] for line in chunk if line.startswith((" ", "-"))]
                    new_lines = [line[1:] for line in chunk if line.startswith((" ", "+"))]
                    old = "\n".join(old_lines)
                    new = "\n".join(new_lines)
                    matches = content.count(old) if old else 0
                    if matches != 1:
                        raise RepositoryError(
                            f"Patch context for {section.path} matched {matches} times; expected exactly once"
                        )
                    content = content.replace(old, new, 1)
                staged[path] = content

        for path, content in staged.items():
            if content is None:
                path.unlink()
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
        changed = ", ".join(str(path.relative_to(project)) for path in staged)
        stat = self._run(["git", "diff", "--stat"], project)
        return stat if "Required command" not in stat else f"Patch applied to: {changed}"

    def _run(self, argv: list[str], cwd: Path, timeout: int = 15) -> str:
        try:
            result = subprocess.run(
                argv,
                cwd=cwd,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=timeout,
                check=False,
            )
        except FileNotFoundError:
            return f"Required command not installed: {argv[0]}"
        except subprocess.TimeoutExpired:
            return f"Command timed out after {timeout} seconds."
        return (result.stdout or f"Command exited {result.returncode} with no output.")[: self.max_output]
