import os
from pathlib import Path
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.backends import default_backend


def ensure_directories():
    base = Path(__file__).resolve().parent
    client_dir = base / "client"
    server_dir = base / "server"
    client_dir.mkdir(parents=True, exist_ok=True)
    server_dir.mkdir(parents=True, exist_ok=True)
    return client_dir, server_dir


def generate_server_keys():
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
        backend=default_backend(),
    )
    public_key = private_key.public_key()

    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )

    public_pem = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )

    return private_pem, public_pem


def write_keys(client_dir: Path, server_dir: Path, private_pem: bytes, public_pem: bytes):
    server_priv_path = server_dir / "private_key.pem"
    client_pub_path = client_dir / "public_key.pem"

    # Do not overwrite if already exist to avoid accidental key rotation
    if not server_priv_path.exists():
        server_priv_path.write_bytes(private_pem)
        try:
            os.chmod(server_priv_path, 0o600)
        except Exception:
            # Best-effort on non-POSIX systems
            pass

    if not client_pub_path.exists():
        client_pub_path.write_bytes(public_pem)

    return str(server_priv_path), str(client_pub_path)


def main():
    client_dir, server_dir = ensure_directories()
    private_pem, public_pem = generate_server_keys()
    server_priv, client_pub = write_keys(client_dir, server_dir, private_pem, public_pem)
    print(f"Server private key written to: {server_priv}")
    print(f"Server public key written to client folder: {client_pub}")


if __name__ == "__main__":
    main()


