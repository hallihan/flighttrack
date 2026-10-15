"""Seeing and joining Wi-Fi networks through NetworkManager's nmcli.

Every call here blocks (a scan takes seconds), so use them from a worker thread. Commands are
run as argument lists, never through a shell, so a network name or password can contain
anything.
"""

import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, replace

Runner = Callable[[list[str], float], subprocess.CompletedProcess]

SCAN_TIMEOUT_S = 25.0
CONNECT_TIMEOUT_S = 60.0


@dataclass(frozen=True, slots=True)
class Network:
    ssid: str
    signal: int  # 0-100
    secured: bool
    active: bool
    saved: bool = False  # joined before: its password is remembered
    # Offers WPA3 and nothing older. The Raspberry Pi's built-in Wi-Fi cannot join these: its
    # firmware does not complete the WPA3 handshake. Mixed WPA2/WPA3 networks are fine.
    wpa3_only: bool = False


def _run(args: list[str], timeout: float) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)


def _split_terse(line: str) -> list[str]:
    """Fields of one line of nmcli's terse output, where ':' separates and '\\' escapes."""
    fields, current, escaped = [], [], False
    for char in line:
        if escaped:
            current.append(char)
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == ":":
            fields.append("".join(current))
            current = []
        else:
            current.append(char)
    fields.append("".join(current))
    return fields


def parse_networks(output: str) -> list[Network]:
    """Networks from `nmcli -t -f ACTIVE,SSID,SIGNAL,SECURITY device wifi list`.

    One entry per name (a network usually has several access points), strongest signal first,
    the connected one at the top. Hidden networks, which have no name, are left out.
    """
    best: dict[str, Network] = {}
    for line in output.splitlines():
        fields = _split_terse(line)
        if len(fields) < 4 or not fields[1]:
            continue
        try:
            signal = int(fields[2])
        except ValueError:
            continue
        security = fields[3].split()
        network = Network(
            fields[1],
            signal,
            fields[3].strip() not in ("", "--"),
            fields[0] == "yes",
            wpa3_only="WPA3" in security and not {"WPA1", "WPA2", "WEP"} & set(security),
        )
        known = best.get(network.ssid)
        if known is None or (network.active, network.signal) > (known.active, known.signal):
            best[network.ssid] = network
    return sorted(best.values(), key=lambda n: (not n.active, -n.signal, n.ssid.lower()))


class Wifi:
    def __init__(self, runner: Runner | None = None, available: bool | None = None) -> None:
        self._run = runner or _run
        self.available = shutil.which("nmcli") is not None if available is None else available

    def scan(self) -> list[Network]:
        """Look for networks now. Raises RuntimeError if the scan cannot be run."""
        result = self._nmcli(
            [
                "-t",
                "-f",
                "ACTIVE,SSID,SIGNAL,SECURITY",
                "device",
                "wifi",
                "list",
                "--rescan",
                "yes",
            ],
            SCAN_TIMEOUT_S,
        )
        if result.returncode != 0:
            raise RuntimeError(_message(result) or "The scan failed.")
        saved = self._wifi_profiles()
        return [
            replace(network, saved=network.ssid in saved)
            for network in parse_networks(result.stdout)
        ]

    def connect(self, ssid: str, password: str | None) -> tuple[bool, str]:
        """Join a network. Returns (succeeded, message for the user). No password is needed
        for an open network or one joined before.

        Every network joined is kept by NetworkManager, which reconnects by itself to any of
        them that is in range whenever the display has no connection. A profile is told to
        keep retrying for ever, where the default gives up after a few attempts.

        If joining fails, the half-made saved profile is removed, so a wrong password is not
        remembered, and NetworkManager goes back to a network it knows.
        """
        try:
            known_before = ssid in self._wifi_profiles()
            args = ["device", "wifi", "connect", ssid]
            if password:
                args += ["password", password]
            result = self._nmcli(args, CONNECT_TIMEOUT_S)
            if result.returncode == 0:
                self.keep_retrying()
                return True, f"Connected to {ssid}."
            if not known_before:
                profile = self._wifi_profiles().get(ssid)
                if profile is not None:
                    self._nmcli(["connection", "delete", "id", profile], 10.0)
        except RuntimeError as exc:
            return False, str(exc)
        return False, _message(result) or "Could not connect."

    def current(self) -> str | None:
        """Name of the Wi-Fi network in use, or None. Does not scan, so it is quick."""
        try:
            result = self._nmcli(
                ["-t", "-f", "ACTIVE,SSID", "device", "wifi", "list", "--rescan", "no"], 10.0
            )
        except RuntimeError:
            return None
        for line in result.stdout.splitlines():
            fields = _split_terse(line)
            if len(fields) >= 2 and fields[0] == "yes" and fields[1]:
                return fields[1]
        return None

    def saved(self) -> list[str]:
        """Names of the networks the display remembers, in alphabetical order."""
        try:
            return sorted(self._wifi_profiles(), key=str.lower)
        except RuntimeError:
            return []

    def forget(self, ssid: str) -> tuple[bool, str]:
        """Stop remembering a network. If it is the one in use, the display drops off it."""
        try:
            profile = self._wifi_profiles().get(ssid)
            if profile is None:
                return False, f"{ssid} is not a saved network."
            result = self._nmcli(["connection", "delete", "id", profile], 15.0)
        except RuntimeError as exc:
            return False, str(exc)
        if result.returncode != 0:
            return False, _message(result) or "Could not remove it."
        return True, f"Removed {ssid}."

    def add(self, ssid: str, password: str | None) -> tuple[bool, str]:
        """Remember a network without joining it now, so the display will connect to it when
        it comes into range. An empty password means an open network."""
        ssid = ssid.strip()
        if not ssid:
            return False, "Enter the network's name."
        if password and not 8 <= len(password) <= 63:
            return False, "A Wi-Fi password is 8 to 63 characters."
        try:
            if ssid in self._wifi_profiles():
                return False, f"{ssid} is already saved."
            args = ["connection", "add", "type", "wifi", "con-name", ssid, "ssid", ssid]
            args += ["connection.autoconnect", "yes", "connection.autoconnect-retries", "0"]
            if password:
                args += ["wifi-sec.key-mgmt", "wpa-psk", "wifi-sec.psk", password]
            result = self._nmcli(args, 15.0)
        except RuntimeError as exc:
            return False, str(exc)
        if result.returncode != 0:
            return False, _message(result) or "Could not save it."
        return True, f"Saved {ssid}."

    def keep_retrying(self) -> None:
        """Tell every saved Wi-Fi network to keep trying to reconnect for ever. Without this
        NetworkManager gives up on a network after a few failed attempts."""
        try:
            for profile in self._wifi_profiles().values():
                self._nmcli(
                    [
                        "connection",
                        "modify",
                        "id",
                        profile,
                        "connection.autoconnect",
                        "yes",
                        "connection.autoconnect-retries",
                        "0",
                    ],
                    10.0,
                )
        except RuntimeError:
            pass  # the networks still work; they just keep the default retry behavior

    def _wifi_profiles(self) -> dict[str, str]:
        """Saved Wi-Fi networks: network name -> the profile that holds its settings. The two
        differ when the profile was made by something else, such as the OS imager."""
        listing = self._nmcli(["-t", "-f", "NAME,TYPE", "connection", "show"], 10.0)
        profiles: dict[str, str] = {}
        for line in listing.stdout.splitlines():
            fields = _split_terse(line)
            if len(fields) < 2 or fields[1] != "802-11-wireless":
                continue
            detail = self._nmcli(
                ["-t", "-f", "802-11-wireless.ssid", "connection", "show", "id", fields[0]], 10.0
            )
            for row in detail.stdout.splitlines():
                parts = _split_terse(row)
                if len(parts) >= 2 and parts[0] == "802-11-wireless.ssid" and parts[1]:
                    profiles[parts[1]] = fields[0]
        return profiles

    def _nmcli(self, args: list[str], timeout: float) -> subprocess.CompletedProcess:
        if not self.available:
            raise RuntimeError("Wi-Fi control is not available on this system.")
        try:
            return self._run(["nmcli", *args], timeout)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("The network did not respond in time.") from exc
        except OSError as exc:
            raise RuntimeError(f"Could not run nmcli: {exc}") from exc


def _message(result: subprocess.CompletedProcess) -> str:
    """nmcli's complaint, tidied for showing on screen."""
    text = (result.stderr or result.stdout or "").strip().splitlines()
    line = text[-1] if text else ""
    if "Secrets were required" in line or "802-11-wireless-security" in line:
        return "Wrong password."
    if "No network with SSID" in line:
        return "That network is no longer in range."
    return line.removeprefix("Error: ")[:120]
