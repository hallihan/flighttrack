#!/usr/bin/env bash
# FlightTrack installer and updater for Raspberry Pi OS (desktop image).
set -euo pipefail

usage() {
    cat <<'EOF'
Usage: sudo bash install.sh [install|update] [options]

Commands:
  install      Install or repair FlightTrack (default). Safe to re-run.
  update       Pull the latest code, then re-run the install steps without apt.

Options:
  --skip-apt   Do not install system packages.
  --no-start   Do not restart the app at the end.
  -h, --help   Show this help.
EOF
}

APT_PACKAGES=(python3 python3-venv git)
SERVICE=flighttrack.service
SYSCTL_FILE=/etc/sysctl.d/90-flighttrack-portal.conf
WIFI_SHIPPED=/usr/lib/modprobe.d/rpi-brcmfmac.conf
WIFI_OVERRIDE=/etc/modprobe.d/rpi-brcmfmac.conf
WIFI_MARKER="Written by the FlightTrack installer"
WIFI_SAE_EXT_BIT=$((1 << 25))

log() { printf '\n==> %s\n' "$*"; }
note() { printf '    %s\n' "$*"; }
die() {
    printf 'install.sh: %s\n' "$*" >&2
    exit 1
}

as_user() { sudo -u "$TARGET_USER" -H "$@"; }

# systemctl --user for the target user, from a root shell. Needs that user's service manager
# to be running, which it is whenever they are logged in (SSH or desktop).
user_systemctl() {
    sudo -u "$TARGET_USER" -H env "XDG_RUNTIME_DIR=$USER_RUNTIME_DIR" systemctl --user "$@"
}

have_user_manager() { [[ -S "$USER_RUNTIME_DIR/bus" ]]; }

check_platform() {
    [[ "$(uname -s)" == "Linux" ]] || die "this installer only runs on Raspberry Pi OS."
    [[ $EUID -eq 0 ]] || die "run with sudo: sudo bash install.sh"
    [[ -n "${SUDO_USER:-}" && "$SUDO_USER" != "root" ]] ||
        die "run with sudo from your normal user account, not as root directly."
    [[ "$REPO" != *[[:space:]]* ]] || die "the checkout path must not contain spaces: $REPO"
    if ! grep -qs "Raspberry Pi" /proc/device-tree/model; then
        note "Warning: this does not look like a Raspberry Pi. Continuing anyway."
    fi
}

install_packages() {
    if [[ $SKIP_APT -eq 1 ]]; then
        log "Skipping system packages (--skip-apt)"
        return
    fi
    command -v apt-get >/dev/null || die "apt-get not found; is this Raspberry Pi OS?"
    log "Installing system packages: ${APT_PACKAGES[*]}"
    apt-get update
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "${APT_PACKAGES[@]}"
}

install_python_env() {
    log "Setting up the Python environment in $REPO/.venv"
    if [[ ! -x "$REPO/.venv/bin/python" ]]; then
        as_user python3 -m venv "$REPO/.venv"
    fi
    as_user "$REPO/.venv/bin/python" -m pip install --quiet --upgrade pip
    as_user "$REPO/.venv/bin/python" -m pip install --quiet --editable "$REPO"
    as_user "$REPO/.venv/bin/python" -c "import pygame, flighttrack" >/dev/null ||
        die "the Python environment is incomplete; see the pip output above."
}

# The settings portal is served on port 80 by the app, which runs as a normal user. Linux
# reserves ports below 1024 for root by default; this lowers that threshold to 80.
allow_portal_port() {
    local setting="net.ipv4.ip_unprivileged_port_start=80"
    log "Allowing non-root programs to listen on port 80 and up: $SYSCTL_FILE"
    if [[ "$(cat "$SYSCTL_FILE" 2>/dev/null)" != "$setting" ]]; then
        printf '%s\n' "$setting" >"$SYSCTL_FILE"
    fi
    sysctl --quiet --load "$SYSCTL_FILE" ||
        note "Warning: could not apply the setting; the portal will use port 8080 instead."
}

# Raspberry Pi OS currently ships a Wi-Fi driver setting that leaves one WPA3 feature (SAE via
# "external auth") switched on, although the Pi's Wi-Fi firmware cannot complete it. The effect
# is that the Pi fails to join networks offering both WPA2 and WPA3, which is most modern
# routers and phone hotspots, because it insists on WPA3. See raspberrypi/linux issue 7634.
# The fix its maintainers give is to add that feature to the driver's disable mask, after
# which such networks are joined with WPA2. It takes effect at the next boot.
fix_wifi_wpa3() {
    WIFI_CHANGED=0
    [[ -r "$WIFI_SHIPPED" ]] || return 0
    local line mask fixed wanted
    line="$(grep -m1 -E '^options brcmfmac .*feature_disable=0x[0-9a-fA-F]+' "$WIFI_SHIPPED")" ||
        return 0
    mask="$(sed -E 's/.*feature_disable=(0x[0-9a-fA-F]+).*/\1/' <<<"$line")"

    if (((mask & WIFI_SAE_EXT_BIT) != 0)); then
        # The OS has since fixed its own setting; ours is no longer needed.
        if grep -qs "$WIFI_MARKER" "$WIFI_OVERRIDE"; then
            log "Removing the Wi-Fi WPA3 workaround, now fixed by the OS: $WIFI_OVERRIDE"
            rm -f "$WIFI_OVERRIDE"
            WIFI_CHANGED=1
        fi
        return 0
    fi
    if [[ -e "$WIFI_OVERRIDE" ]] && ! grep -qs "$WIFI_MARKER" "$WIFI_OVERRIDE"; then
        note "Leaving $WIFI_OVERRIDE alone: it was not written by this installer."
        return 0
    fi

    fixed="$(printf '0x%x' $((mask | WIFI_SAE_EXT_BIT)))"
    wanted="# $WIFI_MARKER. Works around raspberrypi/linux issue 7634:
# the Wi-Fi firmware cannot complete WPA3 (SAE), so stop the driver offering it.
${line/feature_disable=$mask/feature_disable=$fixed}"
    if [[ "$(cat "$WIFI_OVERRIDE" 2>/dev/null)" != "$wanted" ]]; then
        log "Fixing Wi-Fi on WPA2/WPA3 networks: $WIFI_OVERRIDE (feature_disable $mask -> $fixed)"
        printf '%s\n' "$wanted" >"$WIFI_OVERRIDE"
        WIFI_CHANGED=1
    fi
}

install_autostart() {
    local unit_dir="$USER_HOME/.config/systemd/user"
    local autostart_dir="$USER_HOME/.config/autostart"

    log "Installing the user service: $unit_dir/$SERVICE"
    as_user mkdir -p "$unit_dir" "$autostart_dir"
    sed "s|@REPO@|$REPO|g" "$REPO/systemd/$SERVICE" | as_user tee "$unit_dir/$SERVICE" >/dev/null

    log "Installing the desktop autostart entry: $autostart_dir/flighttrack.desktop"
    as_user tee "$autostart_dir/flighttrack.desktop" >/dev/null <<EOF
[Desktop Entry]
Type=Application
Name=FlightTrack
Comment=Start the FlightTrack display when the desktop session begins
Exec=/usr/bin/env bash $REPO/scripts/session-start.sh
NoDisplay=true
EOF
}

start_app() {
    if ! have_user_manager; then
        log "Not starting: no running session for $TARGET_USER. Reboot to start FlightTrack."
        return
    fi
    user_systemctl daemon-reload
    if [[ $NO_START -eq 1 ]]; then
        log "Not restarting the app (--no-start)"
    elif user_systemctl is-active --quiet "$SERVICE"; then
        log "Restarting FlightTrack"
        user_systemctl restart "$SERVICE"
    else
        log "FlightTrack will start with the desktop session. Reboot now:  sudo reboot"
    fi
}

summary() {
    cat <<EOF

FlightTrack is installed.

System-level changes made by this run:
EOF
    if [[ $SKIP_APT -eq 0 ]]; then
        note "apt packages ensured: ${APT_PACKAGES[*]}"
    fi
    note "$SYSCTL_FILE: programs run by any user may listen on ports 80 and up"
    if grep -qs "$WIFI_MARKER" "$WIFI_OVERRIDE"; then
        note "$WIFI_OVERRIDE: Wi-Fi driver told not to attempt WPA3, which its firmware cannot do"
    fi
    if [[ ${WIFI_CHANGED:-0} -eq 1 ]]; then
        printf '\nThe Wi-Fi fix takes effect after a reboot:  sudo reboot\n'
    fi
    cat <<EOF

Changes in $TARGET_USER's home:
    $REPO/.venv                                   Python environment
    ~/.config/systemd/user/$SERVICE       user service
    ~/.config/autostart/flighttrack.desktop       starts the service at desktop login

Requires desktop autologin (the Raspberry Pi OS default; see raspi-config > System Options).

Useful commands (as $TARGET_USER, no sudo):
    systemctl --user status flighttrack      is it running?
    journalctl --user-unit flighttrack -f    live log
    systemctl --user restart flighttrack     restart the display
    sudo bash $REPO/install.sh update        update to the latest version

Settings: open http://$(hostname).local in a browser on the same network.
EOF
}

do_install() {
    install_packages
    install_python_env
    allow_portal_port
    fix_wifi_wpa3
    install_autostart
    start_app
    summary
}

do_update() {
    log "Updating the checkout in $REPO"
    as_user git -C "$REPO" rev-parse --is-inside-work-tree >/dev/null 2>&1 ||
        die "$REPO is not a git checkout; cannot update."
    [[ -z "$(as_user git -C "$REPO" status --porcelain)" ]] ||
        die "the checkout has local changes. Commit, stash or discard them, then retry."
    as_user git -C "$REPO" pull --ff-only

    # Hand over to the installer we just pulled, which may differ from the one running now.
    local args=(install --skip-apt)
    if [[ $NO_START -eq 1 ]]; then
        args+=(--no-start)
    fi
    exec bash "$REPO/install.sh" "${args[@]}"
}

main() {
    local command=install
    SKIP_APT=0
    NO_START=0
    while [[ $# -gt 0 ]]; do
        case "$1" in
            install | update) command=$1 ;;
            --skip-apt) SKIP_APT=1 ;;
            --no-start) NO_START=1 ;;
            -h | --help)
                usage
                exit 0
                ;;
            *)
                usage >&2
                die "unknown argument: $1"
                ;;
        esac
        shift
    done

    REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    check_platform
    TARGET_USER="$SUDO_USER"
    USER_HOME="$(getent passwd "$TARGET_USER" | cut -d: -f6)"
    USER_RUNTIME_DIR="/run/user/$(id -u "$TARGET_USER")"

    case "$command" in
        install) do_install ;;
        update) do_update ;;
    esac
}

# Everything is inside functions and main is called on the last line, so bash has parsed the
# whole file before running any of it. That keeps "update" safe when it replaces this script.
main "$@"
