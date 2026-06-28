from __future__ import annotations

from dataclasses import dataclass, field
import subprocess

from vmaf_workflow.models import CommandResult


class SubprocessRunner:
    def run(self, argv: list[str], stdin: str | None = None) -> CommandResult:
        completed = subprocess.run(
            argv,
            input=stdin,
            text=True,
            capture_output=True,
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


@dataclass
class DryRunRunner:
    commands: list[CommandResult] = field(default_factory=list)

    def run(self, argv: list[str], stdin: str | None = None) -> CommandResult:
        result = CommandResult(tuple(argv), 0, "", "", stdin)
        self.commands.append(result)
        return result
