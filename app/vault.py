"""Encryption for secrets kept on disk (login sessions, records of logged-in jobs).

The key is generated once and stored in the operating system's credential store (Windows
Credential Manager via `keyring`), never next to the data. Copying the data folder to another
computer or account therefore does not reveal sessions or encrypted records.
"""
import json
import threading

from cryptography.fernet import Fernet, InvalidToken

SERVICE = "UniversalScraper"
KEY_NAME = "vault-key"

_lock = threading.Lock()
_fernet: Fernet | None = None


class VaultError(RuntimeError):
    pass


def _load_key() -> bytes:
    try:
        import keyring
        key = keyring.get_password(SERVICE, KEY_NAME)
        if not key:
            key = Fernet.generate_key().decode()
            keyring.set_password(SERVICE, KEY_NAME, key)
        return key.encode()
    except Exception as e:  # no credential store available
        raise VaultError("Could not use the system credential store for the encryption key "
                         f"({type(e).__name__}). Logged-in sessions need it.") from e


def _f() -> Fernet:
    global _fernet
    with _lock:
        if _fernet is None:
            _fernet = Fernet(_load_key())
        return _fernet


def set_key_for_tests(key: bytes | None) -> None:
    global _fernet
    with _lock:
        _fernet = Fernet(key) if key else None


def encrypt(data: bytes) -> bytes:
    return _f().encrypt(data)


def decrypt(token: bytes) -> bytes:
    try:
        return _f().decrypt(token)
    except InvalidToken as e:
        raise VaultError("Could not decrypt: wrong key or damaged data") from e


def encrypt_json(obj) -> str:
    return encrypt(json.dumps(obj, ensure_ascii=False).encode("utf-8")).decode("ascii")


def decrypt_json(token: str):
    return json.loads(decrypt(token.encode("ascii")).decode("utf-8"))


def forget_key() -> None:
    """Delete the key from the credential store (makes all encrypted data unreadable)."""
    try:
        import keyring
        keyring.delete_password(SERVICE, KEY_NAME)
    except Exception:
        pass
    set_key_for_tests(None)
