import hashlib
import os
from typing import Optional
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MAGIC = b"AGENC1"
SALT_LEN = 16
NONCE_LEN = 12
TAG_LEN = 16
ITERATIONS = 100000


def derive_key(passphrase: str, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", passphrase.encode("utf-8"), salt, ITERATIONS, 32)


def encrypt_bytes(data: bytes, passphrase: str) -> bytes:
    """
    Encrypts arbitrary byte array with AES-256-GCM.
    Returns: b'AGENC1' + 16-byte salt + 12-byte nonce + ciphertext + 16-byte tag
    """
    if not passphrase:
        return data

    salt = os.urandom(SALT_LEN)
    nonce = os.urandom(NONCE_LEN)
    key = derive_key(passphrase, salt)
    aesgcm = AESGCM(key)
    ciphertext_and_tag = aesgcm.encrypt(nonce, data, None)

    return MAGIC + salt + nonce + ciphertext_and_tag


def decrypt_bytes(data: bytes, passphrase: str) -> bytes:
    """
    Decrypts byte array encrypted with AES-256-GCM if it starts with AGENC1 magic header.
    If not encrypted, returns original data.
    """
    if not data or len(data) < len(MAGIC) + SALT_LEN + NONCE_LEN + TAG_LEN:
        return data

    if data[: len(MAGIC)] != MAGIC:
        return data

    if not passphrase:
        raise ValueError(
            "داده‌ها با رمزنگاری سرتاسری AES-256-GCM قفل شده‌اند اما کلید رمزنگاری در تنظیمات وارد نشده است."
        )

    offset = len(MAGIC)
    salt = data[offset : offset + SALT_LEN]
    offset += SALT_LEN
    nonce = data[offset : offset + NONCE_LEN]
    offset += NONCE_LEN
    ciphertext_and_tag = data[offset:]

    key = derive_key(passphrase, salt)
    aesgcm = AESGCM(key)
    return aesgcm.decrypt(nonce, ciphertext_and_tag, None)


def is_encrypted(data: bytes) -> bool:
    return bool(data and data.startswith(MAGIC))
