"""Shutting the device down cleanly, through systemd's login manager.

From the desktop session the app runs in, the logged-in user is allowed to power the machine
off without a password. That is asked, not assumed: can_power_off() puts the question to the
system, and the button is only offered when the answer is yes.
"""

import shutil
import subprocess
from collections.abc import Callable

Runner = Callable[[list[str], float], subprocess.CompletedProcess]

_ASK = [
    "busctl",
    "call",
    "org.freedesktop.login1",
    "/org/freedesktop/login1",
    "org.freedesktop.login1.Manager",
    "CanPowerOff",
]


def _run(args: list[str], timeout: float) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)


class Power:
    def __init__(self, runner: Runner | None = None, available: bool | None = None) -> None:
        self._run = runner or _run
        if available is None:
            available = shutil.which("systemctl") is not None and shutil.which("busctl") is not None
        self.available = available

    def can_power_off(self) -> bool:
        """Whether this session may shut the machine down without being asked for a password.
        Runs a command, so call it from a worker thread."""
        if not self.available:
            return False
        try:
            result = self._run(_ASK, 5.0)
        except (OSError, subprocess.TimeoutExpired):
            return False
        return result.returncode == 0 and '"yes"' in result.stdout

    def power_off(self) -> tuple[bool, str]:
        """Shut down. Returns (started, message); on success the machine goes down within
        seconds, stopping this app along with everything else."""
        if not self.available:
            return False, "Shutting down is not available on this system."
        try:
            result = self._run(["systemctl", "poweroff", "--no-ask-password"], 15.0)
        except subprocess.TimeoutExpired:
            return False, "The system did not respond."
        except OSError as exc:
            return False, f"Could not shut down: {exc}"
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip().splitlines()
            return False, (detail[-1] if detail else "The system refused to shut down.")[:120]
        return True, "Shutting down..."
