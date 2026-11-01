import hashlib
import json
import os
from typing import Any, Dict, Optional, Union
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MAGIC = b"AGENC1"
SALT_LEN = 16
NONCE_LEN = 12
TAG_LEN = 16
ITERATIONS = 100000

# Security constants for global mandatory AES-256-GCM encryption
NTTM_SECURE_ENCRYPT_V1_SALT = "NTTM_SECURE_ENCRYPT_V1_SALT"
DEFAULT_SECURITY_SALT = NTTM_SECURE_ENCRYPT_V1_SALT


def get_effective_key(user_key: Optional[str] = None) -> str:
    """
    Returns the user key if provided and non-empty.
    If empty, null, or whitespace, returns a deterministic, secure fallback key derived
    from NTTM_SECURE_ENCRYPT_V1_SALT so that payloads are ALWAYS encrypted with AES-256-GCM.
    """
    if user_key is not None and str(user_key).strip():
        return str(user_key).strip()
    return hashlib.sha256(NTTM_SECURE_ENCRYPT_V1_SALT.encode("utf-8")).hexdigest()


def derive_key(passphrase: str, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", passphrase.encode("utf-8"), salt, ITERATIONS, 32)


def encrypt_bytes(data: bytes, passphrase: Optional[str] = None) -> bytes:
    """
    Encrypts arbitrary byte array with AES-256-GCM.
    Returns: b'AGENC1' + 16-byte salt + 12-byte nonce + ciphertext + 16-byte tag.
    Always uses the effective key (falls back to standard NTTM secure key if passphrase is empty),
    ensuring that no data packet is transmitted unencrypted.
    """
    effective_passphrase = get_effective_key(passphrase)
    salt = os.urandom(SALT_LEN)
    nonce = os.urandom(NONCE_LEN)
    key = derive_key(effective_passphrase, salt)
    aesgcm = AESGCM(key)
    ciphertext_and_tag = aesgcm.encrypt(nonce, data, None)

    return MAGIC + salt + nonce + ciphertext_and_tag


def decrypt_bytes(data: bytes, passphrase: Optional[str] = None) -> bytes:
    """
    Decrypts byte array encrypted with AES-256-GCM if it starts with AGENC1 magic header.
    If not encrypted, returns original data.
    Uses the effective key (user key or standard fallback).
    """
    if not data or len(data) < len(MAGIC) + SALT_LEN + NONCE_LEN + TAG_LEN:
        return data

    if data[: len(MAGIC)] != MAGIC:
        return data

    effective_passphrase = get_effective_key(passphrase)

    offset = len(MAGIC)
    salt = data[offset : offset + SALT_LEN]
    offset += SALT_LEN
    nonce = data[offset : offset + NONCE_LEN]
    offset += NONCE_LEN
    ciphertext_and_tag = data[offset:]

    key = derive_key(effective_passphrase, salt)
    aesgcm = AESGCM(key)
    try:
        return aesgcm.decrypt(nonce, ciphertext_and_tag, None)
    except Exception as e:
        # If decryption with effective passphrase failed and user had provided a custom passphrase,
        # try the fallback standard key just in case it was encrypted with default key, or vice-versa
        default_key = get_effective_key(None)
        if effective_passphrase != default_key:
            try:
                fallback_key = derive_key(default_key, salt)
                return AESGCM(fallback_key).decrypt(nonce, ciphertext_and_tag, None)
            except Exception:
                pass
        raise ValueError(f"رمزگشایی بسته با کلید امنیتی ناموفق بود: {e}") from e


def encrypt_envelope(envelope_data: Union[bytes, str, dict], passphrase: Optional[str] = None) -> bytes:
    """
    Guarantees AES-256-GCM encryption for any envelope payload (bytes, str, or dict).
    Always uses get_effective_key(passphrase) so that no raw data is sent unencrypted.
    """
    if isinstance(envelope_data, dict):
        raw_bytes = json.dumps(envelope_data, ensure_ascii=False, indent=2).encode("utf-8")
    elif isinstance(envelope_data, str):
        raw_bytes = envelope_data.encode("utf-8")
    elif isinstance(envelope_data, (bytes, bytearray)):
        raw_bytes = bytes(envelope_data)
    else:
        raise TypeError(f"Unsupported envelope type: {type(envelope_data)}")

    effective_passphrase = get_effective_key(passphrase)
    return encrypt_bytes(raw_bytes, effective_passphrase)


def decrypt_envelope(data: bytes, passphrase: Optional[str] = None) -> bytes:
    """
    Decrypts an envelope with AES-256-GCM using get_effective_key(passphrase).
    Returns the decrypted bytes.
    """
    effective_passphrase = get_effective_key(passphrase)
    return decrypt_bytes(data, effective_passphrase)


def is_encrypted(data: bytes) -> bool:
    return bool(data and data.startswith(MAGIC))
