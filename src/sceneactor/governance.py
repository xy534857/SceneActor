"""Static governance checks kept outside NPC runtime semantics."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class GovernanceFinding:
    path: str
    line: int
    code: str
    message: str
    severity: str = "warning"


_FORBIDDEN_QUALITY_NAMES = {
    "_IDENTITY_GENERIC_WORDS",
    "_GENERIC_PUBLIC_ATOM_TOKENS",
    "_QUESTION_FUNCTION_TOKENS",
    "_DEICTIC_QUESTION_TOKENS",
    "aliases",
}
_FORBIDDEN_QUALITY_CALLS = {"split", "casefold", "findall"}


def scan_semantic_hardcode(paths: Iterable[str | Path]) -> tuple[GovernanceFinding, ...]:
    """Flag likely semantic hardcoding; does not run in the NPC runtime."""
    findings: list[GovernanceFinding] = []
    for raw_path in paths:
        path = Path(raw_path)
        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(path))
        except (OSError, SyntaxError) as exc:
            findings.append(GovernanceFinding(str(path), 1, "parse_error", str(exc), "error"))
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id in _FORBIDDEN_QUALITY_NAMES:
                        findings.append(
                            GovernanceFinding(
                                str(path), node.lineno, "semantic_word_table",
                                f"quality token table {target.id} requires a waiver",
                            )
                        )
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr in _FORBIDDEN_QUALITY_CALLS:
                    findings.append(
                        GovernanceFinding(
                            str(path), node.lineno, "semantic_text_heuristic",
                            f"{node.func.attr}() in core code may be semantic hardcoding; classify its use",
                        )
                    )
    return tuple(findings)
