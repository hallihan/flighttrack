#!/usr/bin/env bash
# Run by the desktop session at login (see ~/.config/autostart/flighttrack.desktop).
# Passes the session's display environment to the systemd user manager, then starts the app
# as a user service so it is restarted on failure and logs to the journal.
set -euo pipefail

vars=()
for name in DISPLAY WAYLAND_DISPLAY XAUTHORITY XDG_CURRENT_DESKTOP XDG_SESSION_TYPE; do
    if [[ -n "${!name:-}" ]]; then
        vars+=("$name")
    fi
done
if ((${#vars[@]} > 0)); then
    systemctl --user import-environment "${vars[@]}"
fi

systemctl --user restart flighttrack.service
