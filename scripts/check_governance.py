#!/usr/bin/env python3
"""Fail CI when core code introduces unreviewed semantic hardcoding."""

from __future__ import annotations

import sys
from pathlib import Path

from sceneactor.governance import scan_semantic_hardcode

TARGETS = (
    Path("src/sceneactor/cognition.py"),
    Path("src/sceneactor/contracts.py"),
    Path("src/sceneactor/performance.py"),
    Path("src/sceneactor/runtime.py"),
    Path("src/sceneactor/rehearsal.py"),
)

findings = scan_semantic_hardcode(TARGETS)
for finding in findings:
    print(f"{finding.path}:{finding.line}: {finding.code}: {finding.message}")
if findings:
    raise SystemExit(1)
print("governance scan passed")
