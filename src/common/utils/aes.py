"""AES encryption utilities for sensitive data."""

import base64
import json
from typing import Any, Dict

from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad

from src.common.core.config import get_settings

settings = get_settings()


def get_aes_key() -> bytes:
    """Get AES key from settings, must be 16/24/32 bytes."""
    key = settings.jwt_secret_key.encode()[:32]
    return key.ljust(32, b'0')


def encrypt_conf(conf: Dict[str, Any]) -> str:
    """Encrypt datasource configuration to base64 string."""
    key = get_aes_key()
    iv = key[:16]  # Use first 16 bytes as IV

    # Convert dict to JSON string then to bytes
    plaintext = json.dumps(conf, ensure_ascii=False).encode('utf-8')
    padded_data = pad(plaintext, AES.block_size)

    cipher = AES.new(key, AES.MODE_CBC, iv)
    encrypted = cipher.encrypt(padded_data)

    # Combine IV and encrypted data, then base64 encode
    combined = iv + encrypted
    return base64.b64encode(combined).decode('utf-8')


def decrypt_conf(encrypted_str: str) -> Dict[str, Any]:
    """Decrypt base64 string to datasource configuration dict."""
    if not encrypted_str:
        return {}

    key = get_aes_key()
    iv = key[:16]

    # Decode base64 and extract IV
    combined = base64.b64decode(encrypted_str)
    extracted_iv = combined[:16]
    encrypted = combined[16:]

    cipher = AES.new(key, AES.MODE_CBC, extracted_iv)
    decrypted = cipher.decrypt(encrypted)
    unpadded = unpad(decrypted, AES.block_size)

    return json.loads(unpadded.decode('utf-8'))