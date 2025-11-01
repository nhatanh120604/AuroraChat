"""
Minimal crypto utilities for client-side AES encryption and RSA encryption of the
session AES key using the server's public key.
"""

import os
import base64
import secrets
from typing import Tuple, Optional
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


def fetch_server_public_key(server_url: str) -> Optional[bytes]:
    """Fetch server's public key from /public_key endpoint."""
    try:
        import urllib.request
        url = f"{server_url.rstrip('/')}/public_key"
        with urllib.request.urlopen(url, timeout=5) as response:
            return response.read()
    except Exception:
        return None


def load_server_public_key_pem(server_url: Optional[str] = None) -> bytes:
    """Load server public key: first try fetching from server, then fallback to local file."""
    # Try fetching from server if URL provided
    if server_url:
        fetched = fetch_server_public_key(server_url)
        if fetched:
            return fetched
    
    # Fallback to local file
    base_dir = os.path.dirname(__file__)
    pub_path = os.path.join(base_dir, "public_key.pem")
    if os.path.exists(pub_path):
        with open(pub_path, "rb") as f:
            return f.read()
    
    raise FileNotFoundError(
        "Server public key not found. Either set CHAT_SERVER_URL or place public_key.pem in client/"
    )


def rsa_encrypt_with_server_public_key(data: bytes, server_url: Optional[str] = None) -> str:
    pem = load_server_public_key_pem(server_url)
    public_key = serialization.load_pem_public_key(pem, backend=default_backend())
    encrypted = public_key.encrypt(
        data,
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None,
        ),
    )
    return base64.b64encode(encrypted).decode("utf-8")


def generate_aes_key() -> bytes:
    return secrets.token_bytes(32)


def _pkcs7_pad(data: bytes, block_size: int = 16) -> bytes:
    pad_len = block_size - (len(data) % block_size)
    return data + bytes([pad_len] * pad_len)


def _pkcs7_unpad(padded: bytes) -> bytes:
    pad_len = padded[-1]
    return padded[:-pad_len]


def aes_encrypt(data: bytes, key: bytes) -> Tuple[bytes, bytes]:
    iv = secrets.token_bytes(16)
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv), backend=default_backend())
    encryptor = cipher.encryptor()
    padded = _pkcs7_pad(data, 16)
    ciphertext = encryptor.update(padded) + encryptor.finalize()
    return ciphertext, iv


def aes_decrypt(ciphertext: bytes, key: bytes, iv: bytes) -> bytes:
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv), backend=default_backend())
    decryptor = cipher.decryptor()
    padded = decryptor.update(ciphertext) + decryptor.finalize()
    return _pkcs7_unpad(padded)

