from __future__ import annotations

import codecs
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
            text=False,
            shell=False,
        )
        mode = "a" if append else "w"
        try:
            if process.stdout is None:
                raise RuntimeError("streaming process stdout is unavailable")
            read_available = getattr(process.stdout, "read1", None)
            if read_available is None:
                read_available = process.stdout.read
            decoder = codecs.getincrementaldecoder("utf-8")(
                errors="replace"
            )
            with log_path.open(mode, encoding="utf-8", newline="") as log_file:
                while True:
                    chunk = read_available(4096)
                    if not chunk:
                        break
                    text = (
                        chunk
                        if isinstance(chunk, str)
                        else decoder.decode(chunk)
                    )
                    if text:
                        sys.stdout.write(text)
                        sys.stdout.flush()
                        log_file.write(text)
                        log_file.flush()
                tail = decoder.decode(b"", final=True)
                if tail:
                    sys.stdout.write(tail)
                    sys.stdout.flush()
                    log_file.write(tail)
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
