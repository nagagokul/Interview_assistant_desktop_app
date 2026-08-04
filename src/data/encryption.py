"""Fernet encryption-at-rest helpers for SQLite payloads and secrets."""

from __future__ import annotations

import base64
import hashlib
import os
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from src.core.logging_setup import get_logger
from src.core.paths import key_path

log = get_logger("encryption")


def _derive_fernet_key(raw: bytes) -> bytes:
    """Derive a url-safe 32-byte Fernet key from arbitrary secret bytes."""
    digest = hashlib.sha256(raw).digest()
    return base64.urlsafe_b64encode(digest)


def generate_key() -> bytes:
    return Fernet.generate_key()


@lru_cache(maxsize=1)
def load_or_create_key() -> bytes:
    path = key_path()
    if path.is_file():
        key = path.read_bytes().strip()
        if len(key) == 44:  # standard Fernet key length
            return key
        return _derive_fernet_key(key)
    key = generate_key()
    path.parent.mkdir(parents=True, exist_ok=True)
    # Restrictive permissions where supported
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(key)
    log.info("Created new encryption master key at %s", path)
    return key


class Encryptor:
    """Symmetric encrypt/decrypt for text blobs stored in SQLite."""

    def __init__(self, key: bytes | None = None) -> None:
        self._fernet = Fernet(key or load_or_create_key())

    def encrypt(self, plaintext: str | bytes) -> bytes:
        data = plaintext.encode("utf-8") if isinstance(plaintext, str) else plaintext
        return self._fernet.encrypt(data)

    def decrypt(self, token: bytes | str) -> str:
        raw = token.encode("utf-8") if isinstance(token, str) else token
        try:
            return self._fernet.decrypt(raw).decode("utf-8")
        except InvalidToken as exc:
            raise ValueError("Failed to decrypt payload — key mismatch or corruption") from exc

    def encrypt_optional(self, plaintext: str | None) -> bytes | None:
        if plaintext is None:
            return None
        return self.encrypt(plaintext)

    def decrypt_optional(self, token: bytes | None) -> str | None:
        if token is None:
            return None
        return self.decrypt(token)


# Process singleton
ENCRYPTOR = Encryptor()
