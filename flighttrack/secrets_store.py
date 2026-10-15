"""API keys and the web page's password: checking, masking and hashing them.

Keys are kept as typed (the services need them as they are), in a file of their own that
only the display's user can read (settings.SECRETS_FILE); they are never shown again in
full: on the display and on the web page they appear as dots and their last four
characters, and the web page is never sent more than that.

The web page's password is not kept at all, only a hash of it: scrypt, salted, with the
parameters OWASP recommends (N=2^17, r=8, p=1: 128 MB and about 0.4 s on the Pi a time).
Only one check runs at a time, so a burst of attempts cannot take the memory of several at
once.
"""

import hashlib
import hmac
import re
import secrets
import threading

# The services a key can be given for, as settings fields, with what each is called.
KEY_FIELDS: dict[str, str] = {
    "opensky_client_id": "OpenSky client ID",
    "opensky_client_secret": "OpenSky client secret",
    "flightaware_key": "FlightAware AeroAPI key",
    "adsbx_key": "ADS-B Exchange API key",
}
# How the settings pages (the display's and the web page's) group them, with a line about each.
KEY_GROUPS: tuple[tuple[str, tuple[str, ...], str], ...] = (
    (
        "OpenSky Network",
        ("opensky_client_id", "opensky_client_secret"),
        "A free account: more requests a day, and flight history.",
    ),
    (
        "FlightAware AeroAPI",
        ("flightaware_key",),
        "Personal tier: a monthly credit, then paid by the request.",
    ),
    ("ADS-B Exchange", ("adsbx_key",), "A paid key, for positions at higher rates."),
)
_KEY = re.compile(r"[A-Za-z0-9._~+/=:-]{6,200}")
MIN_PASSWORD = 6
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2**17, 8, 1
_SCRYPT_MAXMEM = 256 * 2**20  # above the 128 MB these parameters need
_CHECKING = threading.Lock()


# Kept with the keys, but not secret, so shown in full: OpenSky's client ID is the user's name
# with "-api-client" after it, and seeing it whole is how a mistyped one is noticed.
SHOWN_IN_FULL = frozenset({"opensky_client_id"})


def shown(field: str, value: str | None) -> str:
    """What the settings pages show of a key field once it is set."""
    return (value or "") if field in SHOWN_IN_FULL else masked(value)


def masked(value: str | None) -> str:
    """What is shown of a key once it is set: dots and its last four characters."""
    if not value:
        return ""
    return "••••" + (value[-4:] if len(value) >= 10 else "")


def check_key(text: str) -> tuple[str | None, str | None]:
    """A key as typed, made ready to keep: (the key, or None to remove it; an error)."""
    key = text.strip()
    if not key:
        return None, None
    if not _KEY.fullmatch(key):
        return None, "That does not look like a key: letters, digits and . _ - only, no spaces."
    return key, None


def hash_password(password: str, salt: bytes | None = None) -> str:
    """A salted scrypt hash of a password, with its parameters, to keep instead of it."""
    salt = salt if salt is not None else secrets.token_bytes(16)
    with _CHECKING:
        digest = hashlib.scrypt(
            password.encode(),
            salt=salt,
            n=SCRYPT_N,
            r=SCRYPT_R,
            p=SCRYPT_P,
            maxmem=_SCRYPT_MAXMEM,
            dklen=32,
        )
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${salt.hex()}${digest.hex()}"


def password_matches(password: str, stored: str | None) -> bool:
    """Whether a password is the one whose hash is kept."""
    if not stored or not password:
        return False
    try:
        kind, n, r, p, salt, digest = stored.split("$")
        if kind != "scrypt":
            return False
        with _CHECKING:
            tried = hashlib.scrypt(
                password.encode(),
                salt=bytes.fromhex(salt),
                n=int(n),
                r=int(r),
                p=int(p),
                maxmem=_SCRYPT_MAXMEM,
                dklen=len(bytes.fromhex(digest)),
            )
    except ValueError:
        return False
    return hmac.compare_digest(tried.hex(), digest)


def is_hash(stored: object) -> bool:
    return isinstance(stored, str) and stored.startswith("scrypt$")


def password_problem(text: str) -> str | None:
    """What is wrong with a new password as typed, if anything. Empty is allowed: it removes
    the password."""
    if text and len(text) < MIN_PASSWORD:
        return f"At least {MIN_PASSWORD} characters."
    return None


def password_version(stored: str | None) -> str:
    """Changes whenever the password does, so a change signs everyone out of the web page."""
    return hashlib.sha256((stored or "").encode()).hexdigest()[:16]
