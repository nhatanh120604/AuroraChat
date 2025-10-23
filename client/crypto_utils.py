"""
Cryptographic utilities for secure file transfer in chat application.
Implements RSA for key exchange and AES for file encryption.
"""

import os
import base64
import hashlib
import secrets
from typing import Tuple, Optional, Dict, Any
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa, padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC


class CryptoManager:
    """Manages encryption/decryption operations for file transfers."""
    
    def __init__(self):
        self.private_key = None
        self.public_key = None
        self.peer_public_key = None
        self._generate_key_pair()
    
    def _generate_key_pair(self):
        """Generate RSA key pair for this client."""
        self.private_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=2048,
            backend=default_backend()
        )
        self.public_key = self.private_key.public_key()
    
    def get_public_key_pem(self) -> str:
        """Get public key in PEM format."""
        pem = self.public_key.serialize(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo
        )
        return pem.decode('utf-8')
    
    def set_peer_public_key(self, pem_key: str):
        """Set peer's public key for encryption."""
        try:
            self.peer_public_key = serialization.load_pem_public_key(
                pem_key.encode('utf-8'),
                backend=default_backend()
            )
        except Exception as e:
            raise ValueError(f"Invalid public key: {e}")
    
    def generate_aes_key(self) -> bytes:
        """Generate a random AES key."""
        return secrets.token_bytes(32)  # 256-bit key
    
    def encrypt_aes_key(self, aes_key: bytes) -> str:
        """Encrypt AES key with peer's RSA public key."""
        if not self.peer_public_key:
            raise ValueError("Peer public key not set")
        
        encrypted = self.peer_public_key.encrypt(
            aes_key,
            padding.OAEP(
                mgf=padding.MGF1(algorithm=hashes.SHA256()),
                algorithm=hashes.SHA256(),
                label=None
            )
        )
        return base64.b64encode(encrypted).decode('utf-8')
    
    def decrypt_aes_key(self, encrypted_aes_key: str) -> bytes:
        """Decrypt AES key with our RSA private key."""
        try:
            encrypted_bytes = base64.b64decode(encrypted_aes_key.encode('utf-8'))
            decrypted = self.private_key.decrypt(
                encrypted_bytes,
                padding.OAEP(
                    mgf=padding.MGF1(algorithm=hashes.SHA256()),
                    algorithm=hashes.SHA256(),
                    label=None
                )
            )
            return decrypted
        except Exception as e:
            raise ValueError(f"Failed to decrypt AES key: {e}")
    
    def encrypt_data(self, data: bytes, aes_key: bytes) -> Tuple[bytes, bytes]:
        """Encrypt data with AES-256-CBC."""
        # Generate random IV
        iv = secrets.token_bytes(16)
        
        # Create cipher
        cipher = Cipher(
            algorithms.AES(aes_key),
            modes.CBC(iv),
            backend=default_backend()
        )
        encryptor = cipher.encryptor()
        
        # Pad data to block size (16 bytes)
        padded_data = self._pad_data(data, 16)
        
        # Encrypt
        encrypted = encryptor.update(padded_data) + encryptor.finalize()
        
        return encrypted, iv
    
    def decrypt_data(self, encrypted_data: bytes, aes_key: bytes, iv: bytes) -> bytes:
        """Decrypt data with AES-256-CBC."""
        cipher = Cipher(
            algorithms.AES(aes_key),
            modes.CBC(iv),
            backend=default_backend()
        )
        decryptor = cipher.decryptor()
        
        # Decrypt
        decrypted_padded = decryptor.update(encrypted_data) + decryptor.finalize()
        
        # Remove padding
        return self._unpad_data(decrypted_padded)
    
    def _pad_data(self, data: bytes, block_size: int) -> bytes:
        """PKCS7 padding."""
        padding_length = block_size - (len(data) % block_size)
        padding = bytes([padding_length] * padding_length)
        return data + padding
    
    def _unpad_data(self, padded_data: bytes) -> bytes:
        """Remove PKCS7 padding."""
        padding_length = padded_data[-1]
        return padded_data[:-padding_length]
    
    def calculate_file_hash(self, data: bytes) -> str:
        """Calculate SHA-256 hash of file data."""
        return hashlib.sha256(data).hexdigest()


class FileChunker:
    """Handles file chunking for large file transfers."""
    
    def __init__(self, chunk_size: int = 64 * 1024):  # 64KB default
        self.chunk_size = chunk_size
    
    def chunk_file(self, data: bytes) -> list:
        """Split file data into chunks."""
        chunks = []
        for i in range(0, len(data), self.chunk_size):
            chunk = data[i:i + self.chunk_size]
            chunks.append(chunk)
        return chunks
    
    def create_chunk_metadata(self, filename: str, total_size: int, 
                            total_chunks: int, file_hash: str) -> Dict[str, Any]:
        """Create metadata for file chunks."""
        return {
            "filename": filename,
            "total_size": total_size,
            "total_chunks": total_chunks,
            "file_hash": file_hash,
            "chunk_size": self.chunk_size
        }


class EncryptedFileTransfer:
    """Handles complete encrypted file transfer process."""
    
    def __init__(self, chunk_size: int = 64 * 1024):
        self.crypto_manager = CryptoManager()
        self.chunker = FileChunker(chunk_size)
        self.transfer_id = secrets.token_hex(16)
    
    def prepare_file_for_sending(self, file_data: bytes, filename: str) -> Dict[str, Any]:
        """Prepare file for encrypted transmission."""
        # Calculate file hash
        file_hash = self.crypto_manager.calculate_file_hash(file_data)
        
        # Generate AES key for this file
        aes_key = self.crypto_manager.generate_aes_key()
        
        # Encrypt the entire file
        encrypted_data, iv = self.crypto_manager.encrypt_data(file_data, aes_key)
        
        # Encrypt AES key with peer's public key
        encrypted_aes_key = self.crypto_manager.encrypt_aes_key(aes_key)
        
        # Chunk the encrypted file
        chunks = self.chunker.chunk_file(encrypted_data)
        
        # Create metadata
        metadata = self.chunker.create_chunk_metadata(
            filename, len(file_data), len(chunks), file_hash
        )
        
        return {
            "transfer_id": self.transfer_id,
            "metadata": metadata,
            "encrypted_aes_key": encrypted_aes_key,
            "iv": base64.b64encode(iv).decode('utf-8'),
            "chunks": chunks,
            "total_chunks": len(chunks)
        }
    
    def prepare_chunk(self, chunk_data: bytes, chunk_index: int, 
                     transfer_metadata: Dict[str, Any]) -> Dict[str, Any]:
        """Prepare a single chunk for transmission."""
        return {
            "transfer_id": transfer_metadata["transfer_id"],
            "chunk_index": chunk_index,
            "chunk_data": base64.b64encode(chunk_data).decode('utf-8'),
            "is_last_chunk": chunk_index == transfer_metadata["total_chunks"] - 1,
            "metadata": transfer_metadata["metadata"] if chunk_index == 0 else None
        }
    
    def reassemble_file(self, chunks_data: list, encrypted_aes_key: str, 
                       iv: str, expected_hash: str) -> Tuple[bytes, str]:
        """Reassemble and decrypt file from chunks."""
        # Decrypt AES key
        aes_key = self.crypto_manager.decrypt_aes_key(encrypted_aes_key)
        
        # Decode IV
        iv_bytes = base64.b64decode(iv.encode('utf-8'))
        
        # Reassemble encrypted file data
        encrypted_data = b''.join(chunks_data)
        
        # Decrypt file data
        decrypted_data = self.crypto_manager.decrypt_data(
            encrypted_data, aes_key, iv_bytes
        )
        
        # Verify file hash
        actual_hash = self.crypto_manager.calculate_file_hash(decrypted_data)
        if actual_hash != expected_hash:
            raise ValueError("File hash verification failed - file may be corrupted")
        
        return decrypted_data, actual_hash


# Utility functions for easy integration
def create_crypto_manager() -> CryptoManager:
    """Create a new CryptoManager instance."""
    return CryptoManager()

def create_file_transfer(chunk_size: int = 64 * 1024) -> EncryptedFileTransfer:
    """Create a new EncryptedFileTransfer instance."""
    return EncryptedFileTransfer(chunk_size)
