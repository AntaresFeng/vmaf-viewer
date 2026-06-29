from __future__ import annotations

import subprocess

from vmaf_workflow.models import CommandResult


class SubprocessRunner:
    def run(self, argv: list[str], stdin: str | None = None) -> CommandResult:
        completed = subprocess.run(
            argv,
            input=stdin,
            text=True,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
            check=False,
        )
        return CommandResult(
            tuple(argv),
            completed.returncode,
            completed.stdout,
            completed.stderr,
            stdin,
        )
