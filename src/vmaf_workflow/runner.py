from __future__ import annotations

import subprocess
import sys
from pathlib import Path

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

    def stream(
        self,
        argv: list[str],
        log_path: Path,
        append: bool = False,
    ) -> int:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        process = subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
        )
        mode = "a" if append else "w"
        try:
            if process.stdout is None:
                raise RuntimeError("streaming process stdout is unavailable")
            with log_path.open(mode, encoding="utf-8", newline="") as log_file:
                while True:
                    chunk = process.stdout.read(4096)
                    if not chunk:
                        break
                    sys.stdout.write(chunk)
                    sys.stdout.flush()
                    log_file.write(chunk)
                    log_file.flush()
            return process.wait()
        except KeyboardInterrupt:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            raise
