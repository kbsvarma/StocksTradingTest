#!/usr/bin/env python3
"""Audit the complete Git index without printing credential values.

Requires Gitleaks on PATH, or an explicit GITLEAKS_BIN executable path.
This exports staged blobs, not potentially different working-tree contents.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile


def main() -> int:
    root = Path(subprocess.check_output(
        ["git", "rev-parse", "--show-toplevel"], text=True).strip())
    binary = os.environ.get("GITLEAKS_BIN") or shutil.which("gitleaks")
    if not binary:
        print("Install Gitleaks or set GITLEAKS_BIN; audit not performed.")
        return 2
    binary = str(Path(binary).resolve())
    entries = subprocess.check_output(
        ["git", "ls-files", "--stage", "-z"], cwd=root).decode().split("\0")
    issues = []
    private_name = re.compile(
        r"^(?:\.env(?:\..*)?|\.webull_env(?:\..*)?|credentials(?:\..*)?|"
        r"secrets\.(?:json|toml|ya?ml)|id_(?:rsa|ed25519).*|ARM_.*|HALT.*)$",
        re.I,
    )
    forbidden_suffixes = {".pem", ".key", ".p12", ".pfx", ".log", ".db"}
    for entry in filter(None, entries):
        metadata, name = entry.split("\t", 1)
        mode, _, stage = metadata.split()
        path = Path(name)
        if stage != "0" or mode == "160000":
            issues.append((name, "unmerged entry or embedded repository"))
        if ((private_name.match(path.name) and not path.name.endswith(".example"))
                or path.suffix in forbidden_suffixes or ".sqlite" in path.name
                or path.name == "MEMORY.md"):
            issues.append((name, "private/runtime file must not be tracked"))
    with tempfile.TemporaryDirectory(prefix="stocks-staged-audit-") as tmp:
        tree = Path(tmp) / "tree"
        tree.mkdir()
        subprocess.run(["git", "checkout-index", "--all", f"--prefix={tree}/"],
                       cwd=root, check=True, capture_output=True)
        account_literal = re.compile(
            r'''(?i)\baccount[_-]?(?:id|number)\s*["']?\s*[:=]\s*["']([A-Za-z0-9_-]{16,})["']'''
        )
        for file in tree.rglob("*"):
            if not file.is_file() or file.is_symlink():
                continue
            if file.stat().st_size > 5_000_000:
                issues.append((str(file.relative_to(tree)), "oversized source artifact"))
                continue
            try:
                contents = file.read_text()
            except UnicodeError:
                continue
            for number, line in enumerate(contents.splitlines(), 1):
                match = account_literal.search(line)
                if match and not re.search(r"(?i)example|dummy|test|your|placeholder", match[1]):
                    issues.append((f"{file.relative_to(tree)}:{number}",
                                   "literal broker account identifier"))
        report = Path(tmp) / "gitleaks.json"
        result = subprocess.run(
            [binary, "dir", str(tree), "--config", str(tree / ".gitleaks.toml"),
             "--no-banner", "--redact=100", "--report-format=json",
             f"--report-path={report}"], capture_output=True, text=True)
        if result.returncode not in (0, 1) or not report.exists():
            print("Gitleaks failed to complete; no clean result.")
            return 2
        for finding in json.loads(report.read_text()):
            name = str(finding["File"])
            if name.startswith(str(tree) + "/"):
                name = name[len(str(tree)) + 1:]
            issues.append((f"{name}:{finding['StartLine']}", finding["RuleID"]))
        if result.returncode == 1 and not issues:
            print("Gitleaks returned a failure without findings; audit incomplete.")
            return 2
    for name, reason in issues:
        print(f"BLOCKED {name}: {reason}")
    if issues:
        return 1
    print("PASS: complete staged tree scanned; no detected secrets or private runtime files.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
