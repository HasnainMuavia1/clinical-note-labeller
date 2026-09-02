from __future__ import annotations

import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .paths import PathEscapeError, resolve_within

NEEDS_APPROVAL = frozenset({"delete", "overwrite"})


@dataclass(frozen=True)
class FileOp:
    op: str
    source: str | None
    target: str
    reason: str


class GuardedFileTools:
    """Every filesystem mutation the agent performs goes through this class.

    Writes are confined to `root`; delete and overwrite require an approval.
    """

    def __init__(self, root: Path, audit: Callable[[str, dict], None]) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._audit = audit

    def toolkit(self):
        from langchain_community.agent_toolkits import FileManagementToolkit

        return FileManagementToolkit(
            root_dir=str(self.root),
            selected_tools=["read_file", "write_file", "list_directory", "copy_file",
                            "move_file", "file_search"],
        )

    def _resolve(self, target: str) -> Path:
        try:
            return resolve_within(self.root, target)
        except PathEscapeError as exc:
            self._audit("path_escape_denied",
                        {"target": target, "root": str(self.root), "error": str(exc)})
            raise

    def unique_target(self, target_rel: str) -> str:
        path = Path(target_rel)
        counter = 2
        candidate = target_rel
        while self._resolve(candidate).exists():
            candidate = str(path.with_name(f"{path.stem}__{counter}{path.suffix}"))
            counter += 1
        return candidate

    def plan_copy(self, source: Path, target_rel: str, reason: str) -> FileOp:
        exists = self._resolve(target_rel).exists()
        return FileOp("overwrite" if exists else "copy", str(source), target_rel, reason)

    def execute(self, op: FileOp, approved: bool = False) -> Path:
        target = self._resolve(op.target)
        if op.op in NEEDS_APPROVAL and not approved:
            self._audit("guarded_op_refused",
                        {"op": op.op, "target": op.target, "reason": op.reason})
            raise PermissionError(f"operation {op.op!r} on {op.target!r} requires approval")

        if op.op == "mkdir":
            target.mkdir(parents=True, exist_ok=True)
        elif op.op == "delete":
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink(missing_ok=True)
        elif op.op in {"copy", "overwrite"}:
            source = Path(op.source or "")
            if not source.is_file():
                raise FileNotFoundError(f"source {op.source!r} does not exist")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        else:
            raise ValueError(f"unknown operation {op.op!r}")

        self._audit("op_executed",
                    {"op": op.op, "target": op.target, "approved": approved, "reason": op.reason})
        return target
