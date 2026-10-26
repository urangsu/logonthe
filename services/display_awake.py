import os
import subprocess
import sys
import threading

from src.logger import logger


class DisplayAwakeGuard:
    """Keep a Mac awake for one feed run without changing system settings."""

    def __init__(self):
        self._process = None
        self._lock = threading.Lock()

    def start(self):
        if sys.platform != "darwin":
            return False
        with self._lock:
            if self._process is not None and self._process.poll() is None:
                return True
            try:
                self._process = subprocess.Popen(
                    ["/usr/bin/caffeinate", "-di", "-w", str(os.getpid())],
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL, close_fds=True,
                )
                if self._process.poll() is not None:
                    self._process.wait()
                    self._process = None
                    logger.log("[POWER][AWAKE_FAILED] caffeinate exited before activation", "WARNING")
                    return False
                logger.log(f"[POWER][AWAKE_REQUESTED] pid={self._process.pid} scope=comment_run")
                return True
            except OSError as error:
                self._process = None
                logger.log(f"[POWER][AWAKE_FAILED] {error}", "WARNING")
                return False

    def stop(self):
        with self._lock:
            process, self._process = self._process, None
            if process is None:
                return
            try:
                if process.poll() is None:
                    process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)
                logger.log("[POWER][AWAKE_RELEASED] scope=comment_run")
            except (OSError, subprocess.TimeoutExpired) as error:
                logger.log(f"[POWER][AWAKE_RELEASE_FAILED] {error}", "WARNING")
