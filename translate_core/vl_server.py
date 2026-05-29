# translate_core/vl_server.py
#
# VLMServerManager — start/stop mlx_vlm.server as a local subprocess.
# Bound to 127.0.0.1 (not 0.0.0.0) — no LAN exposure during import.
#
# This module is lazy-imported only when --vl is used. The editor never loads it.

import logging
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import httpx

from translate_core.vl_parser import DEFAULT_VL_MODEL, DEFAULT_VL_SERVER_URL

log = logging.getLogger("vl_server")


class VLMServerManager:
    """Manage the lifecycle of a local mlx_vlm.server subprocess."""

    def __init__(
        self,
        model: str = DEFAULT_VL_MODEL,
        port: int = 8081,
        host: str = "127.0.0.1",
        log_path: Path | None = None,
    ):
        self.model = model
        self.port = port
        self.host = host
        self.log_path = log_path or Path("data/.vl_cache/vl_server.log")
        self._proc: subprocess.Popen | None = None

    def _already_running(self) -> bool:
        """Check if a VLM server is already responding on the configured port."""
        try:
            return (
                httpx.get(
                    f"http://{self.host}:{self.port}/v1/models", timeout=3.0
                ).status_code
                == 200
            )
        except Exception:
            return False

    def start(self, timeout: float = 180.0) -> bool:
        """Start the mlx_vlm.server subprocess and wait until it responds.

        Prints progress dots so the user knows it's not frozen.
        """
        if self._already_running():
            print("   VL server already running.")
            return True

        self.log_path.parent.mkdir(parents=True, exist_ok=True)

        with open(self.log_path, "ab") as log_fh:
            self._proc = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "mlx_vlm.server",
                    "--model",
                    self.model,
                    "--port",
                    str(self.port),
                    "--host",
                    self.host,
                ],
                stdout=log_fh,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )

            print(f"   Loading model {self.model.split('/')[-1]}…", flush=True)

            # MLX models take 30-90s to load. Poll every 2s, print dots.
            deadline = time.time() + timeout
            dots = 0
            while time.time() < deadline:
                if self._proc.poll() is not None:
                    print(f"\n   ✗ Server exited early. See {self.log_path}")
                    return False
                if self._already_running():
                    print(f"\n   ✓ Model loaded. Server ready (pid {self._proc.pid}).")
                    return True
                dots += 1
                if dots % 5 == 0:
                    elapsed = int(time.time() - (deadline - timeout))
                    print(f"   … still loading ({elapsed}s)", flush=True)
                time.sleep(2.0)

            print(f"\n   ✗ Server start timed out after {int(timeout)}s. See {self.log_path}")
            return False

    def stop(self):
        """Stop the mlx_vlm.server subprocess (group kill)."""
        if self._proc and self._proc.poll() is None:
            print("   Stopping VL server…", flush=True)
            try:
                os.killpg(os.getpgid(self._proc.pid), signal.SIGTERM)
                self._proc.wait(timeout=10)
            except Exception:
                self._proc.kill()
        self._proc = None