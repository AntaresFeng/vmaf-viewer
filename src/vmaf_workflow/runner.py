from __future__ import annotations

import codecs
import subprocess
import sys
import threading
from pathlib import Path
from typing import Callable

from vmaf_workflow.models import CommandResult


OutputCallback = Callable[[str, str], None]

_TERMINATE_TIMEOUT_SECONDS = 5
_KILL_TIMEOUT_SECONDS = 5
_READER_JOIN_TIMEOUT_SECONDS = 1


class ProcessInterrupted(KeyboardInterrupt):
    """Raised after a caller cancels the currently running subprocess."""


def console_output_encoding() -> str:
    """Return the active Windows console output encoding, or UTF-8 elsewhere."""
    code_page = _windows_console_output_code_page()
    if code_page <= 0:
        return "utf-8"
    encoding = f"cp{code_page}"
    try:
        codecs.lookup(encoding)
    except LookupError:
        return "utf-8"
    return encoding


def _windows_console_output_code_page() -> int:
    if sys.platform != "win32":
        return 0
    try:
        import ctypes

        return int(ctypes.windll.kernel32.GetConsoleOutputCP())
    except (AttributeError, OSError):
        return 0


class SubprocessRunner:
    def __init__(
        self,
        output_callback: OutputCallback | None = None,
        *,
        mirror_console: bool = True,
        inherit_stdin: bool = True,
    ) -> None:
        self.output_callback = output_callback
        self.mirror_console = mirror_console
        self.inherit_stdin = inherit_stdin
        self._cancelled = threading.Event()
        self._process_lock = threading.Lock()
        self._process: subprocess.Popen | None = None

    def run(
        self,
        argv: list[str],
        stdin: str | None = None,
        *,
        output_encoding: str = "utf-8",
    ) -> CommandResult:
        if self.output_callback is not None:
            return self._run_with_output(argv, stdin, output_encoding)
        stdin_kwargs = (
            {"stdin": subprocess.DEVNULL}
            if stdin is None and not self.inherit_stdin
            else {}
        )
        completed = subprocess.run(
            argv,
            input=stdin,
            text=True,
            capture_output=True,
            encoding=output_encoding,
            errors="replace",
            shell=False,
            check=False,
            **stdin_kwargs,
        )
        return CommandResult(
            tuple(argv),
            completed.returncode,
            completed.stdout,
            completed.stderr,
            stdin,
        )

    def cancel_current(self) -> None:
        """Request cancellation and terminate the active child process."""
        self._cancelled.set()
        with self._process_lock:
            process = self._process
        if process is not None:
            threading.Thread(
                target=self._terminate_process,
                args=(process,),
                daemon=True,
            ).start()

    def reset_cancellation(self) -> None:
        """Allow a stopped runner to be reused for an explicit retry."""
        self._cancelled.clear()

    def stream(
        self,
        argv: list[str],
        log_path: Path,
        append: bool = False,
    ) -> int:
        self._raise_if_cancelled()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        stdin_kwargs = (
            {"stdin": subprocess.DEVNULL} if not self.inherit_stdin else {}
        )
        process = subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=False,
            shell=False,
            **stdin_kwargs,
        )
        mode = "a" if append else "w"
        try:
            self._set_process(process)
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
                        self._emit("stdout", text)
                        log_file.write(text)
                        log_file.flush()
                tail = decoder.decode(b"", final=True)
                if tail:
                    self._emit("stdout", tail)
                    log_file.write(tail)
                    log_file.flush()
            returncode = process.wait()
            if self._cancelled.is_set():
                raise ProcessInterrupted()
            return returncode
        except ProcessInterrupted:
            raise
        except KeyboardInterrupt:
            self._terminate_process(process)
            raise
        except BaseException:
            self._terminate_process(process)
            raise
        finally:
            self._clear_process(process)

    def _run_with_output(
        self,
        argv: list[str],
        stdin: str | None,
        output_encoding: str,
    ) -> CommandResult:
        self._raise_if_cancelled()
        process = subprocess.Popen(
            argv,
            stdin=(
                subprocess.PIPE
                if stdin is not None
                else None
                if self.inherit_stdin
                else subprocess.DEVNULL
            ),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=False,
            shell=False,
        )
        stdout_chunks: list[str] = []
        stderr_chunks: list[str] = []
        capture_enabled = threading.Event()
        capture_enabled.set()
        emit_enabled = threading.Event()
        emit_enabled.set()

        def read_stream(name: str, pipe, chunks: list[str]) -> None:
            decoder = codecs.getincrementaldecoder(output_encoding)(
                errors="replace"
            )
            read_available = getattr(pipe, "read1", None) or pipe.read
            try:
                while capture_enabled.is_set():
                    chunk = read_available(4096)
                    if not chunk or not capture_enabled.is_set():
                        break
                    text = decoder.decode(chunk)
                    if text:
                        chunks.append(text)
                        if emit_enabled.is_set():
                            try:
                                self._emit(name, text)
                            except Exception:
                                # Output presentation must not corrupt command capture.
                                emit_enabled.clear()
                if capture_enabled.is_set():
                    tail = decoder.decode(b"", final=True)
                    if tail:
                        chunks.append(tail)
                        if emit_enabled.is_set():
                            try:
                                self._emit(name, tail)
                            except Exception:
                                emit_enabled.clear()
            except OSError:
                if capture_enabled.is_set():
                    emit_enabled.clear()

        readers: list[threading.Thread] = []
        pipes: list[object] = []
        pending_error: BaseException | None = None
        returncode: int | None = None
        try:
            self._set_process(process)
            for name, pipe, chunks in (
                ("stdout", process.stdout, stdout_chunks),
                ("stderr", process.stderr, stderr_chunks),
            ):
                if pipe is None:
                    raise RuntimeError(f"subprocess {name} is unavailable")
                pipes.append(pipe)
                thread = threading.Thread(
                    target=read_stream,
                    args=(name, pipe, chunks),
                    daemon=True,
                )
                readers.append(thread)
                thread.start()
            if stdin is not None:
                self._write_stdin(process, stdin)
            returncode = process.wait()
            if self._cancelled.is_set():
                raise ProcessInterrupted()
        except ProcessInterrupted as exc:
            pending_error = exc
        except KeyboardInterrupt:
            self._terminate_process(process)
            raise
        except BaseException as exc:
            pending_error = exc
            self._terminate_process(process)
        finally:
            self._finish_readers(readers, pipes, capture_enabled, emit_enabled)
            self._clear_process(process)

        if pending_error is not None:
            raise pending_error
        if returncode is None:
            raise RuntimeError("subprocess did not produce a return code")
        return CommandResult(
            tuple(argv),
            returncode,
            "".join(stdout_chunks),
            "".join(stderr_chunks),
            stdin,
        )

    def _emit(self, stream: str, text: str) -> None:
        if self.mirror_console:
            target = sys.stdout if stream == "stdout" else sys.stderr
            target.write(text)
            target.flush()
        if self.output_callback is not None:
            try:
                self.output_callback(stream, text)
            except Exception:
                # UI presentation failures must not alter downloader/SSH semantics.
                return

    def _raise_if_cancelled(self) -> None:
        if self._cancelled.is_set():
            raise ProcessInterrupted()

    def _set_process(self, process: subprocess.Popen) -> None:
        with self._process_lock:
            self._process = process
            cancelled = self._cancelled.is_set()
        if cancelled:
            self._terminate_process(process)
            self._clear_process(process)
            raise ProcessInterrupted()

    def _clear_process(self, process: subprocess.Popen) -> None:
        with self._process_lock:
            if self._process is process:
                self._process = None

    @staticmethod
    def _terminate_process(process: subprocess.Popen) -> None:
        poll = getattr(process, "poll", None)
        if callable(poll) and poll() is not None:
            return
        try:
            process.terminate()
        except OSError:
            return
        try:
            process.wait(timeout=_TERMINATE_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            try:
                process.kill()
            except OSError:
                return
            try:
                process.wait(timeout=_KILL_TIMEOUT_SECONDS)
            except (OSError, subprocess.TimeoutExpired):
                return
        except OSError:
            return

    def _write_stdin(self, process: subprocess.Popen, stdin: str) -> None:
        if process.stdin is None:
            raise RuntimeError("subprocess stdin is unavailable")
        try:
            process.stdin.write(stdin.encode("utf-8"))
        except OSError:
            if self._cancelled.is_set():
                raise ProcessInterrupted() from None
        finally:
            try:
                process.stdin.close()
            except OSError:
                if self._cancelled.is_set():
                    raise ProcessInterrupted() from None

    @staticmethod
    def _finish_readers(
        readers: list[threading.Thread],
        pipes: list[object],
        capture_enabled: threading.Event,
        emit_enabled: threading.Event,
    ) -> None:
        for thread in readers:
            thread.join(timeout=_READER_JOIN_TIMEOUT_SECONDS)
        if all(not thread.is_alive() for thread in readers):
            capture_enabled.clear()
            emit_enabled.clear()
            return

        capture_enabled.clear()
        emit_enabled.clear()
        closers: list[threading.Thread] = []
        for pipe in pipes:
            close = getattr(pipe, "close", None)
            if callable(close):
                closer = threading.Thread(
                    target=SubprocessRunner._close_pipe,
                    args=(close,),
                    daemon=True,
                )
                closers.append(closer)
                closer.start()
        for thread in readers:
            if thread.is_alive():
                thread.join(timeout=_READER_JOIN_TIMEOUT_SECONDS)
        for closer in closers:
            closer.join(timeout=_READER_JOIN_TIMEOUT_SECONDS)

    @staticmethod
    def _close_pipe(close: Callable[[], None]) -> None:
        try:
            close()
        except OSError:
            pass
