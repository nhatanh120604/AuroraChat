import socketio
from flask import Flask
import eventlet
import logging
import os
import threading
#from dotenv import load_dotenv
import base64
from datetime import datetime, timezone
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


MAX_PUBLIC_HISTORY = 200
MAX_FILE_BYTES = 5 * 1024 * 1024  # 5 MB safety cap


def _sanitize_file_payload(data):
    """Validate and clamp incoming file payloads."""
    if not isinstance(data, dict):
        return None

    name = str(data.get("name", ""))[:255]
    mime = str(data.get("mime", "application/octet-stream"))[:255]
    size = data.get("size", 0)
    try:
        size = int(size)
    except (TypeError, ValueError):
        size = 0

    b64_data = data.get("data")
    if not isinstance(b64_data, str) or not b64_data:
        return None

    if size > MAX_FILE_BYTES:
        logging.warning("Rejected file '%s' exceeding size cap", name)
        return None

    if len(b64_data) > (MAX_FILE_BYTES * 4) // 3 + 8:
        logging.warning("Rejected file '%s' due to encoded length", name)
        return None

    return {
        "name": name,
        "mime": mime,
        "size": size,
        "data": b64_data,
    }


# Load environment variables from .env file
#load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)


class ChatServer:
    def __init__(self, test=False):
        self.sio = socketio.Server(
            cors_allowed_origins="*",
            max_http_buffer_size=MAX_FILE_BYTES * 2,
        )
        self.app = Flask(__name__)
        self.app.wsgi_app = socketio.WSGIApp(self.sio, self.app.wsgi_app)
        self.clients = {}
        self.test = test
        self.lock = threading.Lock()  # Lock for thread-safe operations on clients dict
        self.public_history = []
        self.private_message_counter = 0
        self.private_messages = {}
        self.session_keys = {}  # sid -> AES key (bytes)
        # Uploads directory for caching plaintext files
        self.upload_dir = os.path.join(os.path.dirname(__file__), "uploads")
        try:
            os.makedirs(self.upload_dir, exist_ok=True)
        except OSError:
            pass
        
        # File transfer tracking
        self.active_file_transfers = {}  # transfer_id -> transfer_info
        self.file_transfer_lock = threading.Lock()

        self.register_events()

    @staticmethod
    def _load_private_key():
        base_dir = os.path.dirname(__file__)
        priv_path = os.path.join(base_dir, "private_key.pem")
        with open(priv_path, "rb") as f:
            return serialization.load_pem_private_key(f.read(), password=None, backend=default_backend())

    @staticmethod
    def _pkcs7_unpad(padded: bytes) -> bytes:
        pad_len = padded[-1]
        return padded[:-pad_len]

    @staticmethod
    def _aes_decrypt(ciphertext: bytes, key: bytes, iv: bytes) -> bytes:
        cipher = Cipher(algorithms.AES(key), modes.CBC(iv), backend=default_backend())
        decryptor = cipher.decryptor()
        padded = decryptor.update(ciphertext) + decryptor.finalize()
        return ChatServer._pkcs7_unpad(padded)

    def register_events(self):

        @self.sio.event
        def connect(sid, environ):
            logging.info(f"Client connected: {sid}")
            with self.lock:
                history_snapshot = list(self.public_history)
            if history_snapshot:
                self.sio.emit("chat_history", {"messages": history_snapshot}, to=sid)
            # Ensure private key is loaded
            try:
                if not hasattr(self, "_private_key"):
                    self._private_key = self._load_private_key()
            except Exception as e:
                logging.error(f"Failed to load server private key: {e}")

        @self.sio.event
        def disconnect(sid):
            logging.info(f"Client disconnected: {sid}")
            with self.lock:
                username = self.clients.pop(sid, None)
                self.session_keys.pop(sid, None)
                if username:
                    # Notify remaining clients by sending the updated user list
                    self.sio.emit(
                        "update_user_list", {"users": list(self.clients.values())}
                    )
                    logging.info(f"User left: {username}")

        @self.sio.event
        def register(sid, data):
            username = data.get("username")

            # Input validation
            if not username or not isinstance(username, str) or not username.strip():
                self.sio.emit(
                    "error", {"message": "A valid username is required."}, to=sid
                )
                logging.warning(
                    f"Invalid registration attempt from {sid} with username: {username}"
                )
                return

            username = username.strip()

            with self.lock:
                # Enforce unique usernames (case-insensitive)
                if any((name or "").lower() == username.lower() for name in self.clients.values()):
                    self.sio.emit(
                        "error",
                        {"message": f"Username '{username}' is already taken."},
                        to=sid,
                    )
                    logging.warning(
                        f"Registration failed for {sid}: username '{username}' taken."
                    )
                    return

                self.clients[sid] = username
                users_snapshot = list(self.clients.values())
                history_snapshot = list(self.public_history)

            # Notify all clients (including the new one) with the updated user list
            self.sio.emit("update_user_list", {"users": users_snapshot})
            logging.info(f"User registered: {username} with SID: {sid}")

            if history_snapshot:
                self.sio.emit("chat_history", {"messages": history_snapshot}, to=sid)

        @self.sio.event
        def session_key(sid, data):
            enc_key_b64 = data.get("encrypted_aes_key")
            if not enc_key_b64:
                self.sio.emit("error", {"message": "Missing encrypted AES key."}, to=sid)
                return
            try:
                enc_bytes = base64.b64decode(enc_key_b64.encode("utf-8"))
                aes_key = self._private_key.decrypt(
                    enc_bytes,
                    padding.OAEP(
                        mgf=padding.MGF1(algorithm=hashes.SHA256()),
                        algorithm=hashes.SHA256(),
                        label=None,
                    ),
                )
                with self.lock:
                    self.session_keys[sid] = aes_key
                logging.info(f"Stored session AES key for {sid}")
                # Acknowledge to client so it can start sending encrypted payloads
                self.sio.emit("session_key_ok", {"ok": True}, to=sid)
            except Exception as e:
                logging.error(f"Failed to decrypt session key from {sid}: {e}")
                self.sio.emit("error", {"message": "Invalid session key."}, to=sid)

        @self.sio.event
        def message(sid, data):
            """Handle incoming messages from a client and broadcast them."""
            with self.lock:
                sender_username = self.clients.get(sid, "Unknown")

            # Decrypt if encrypted
            file_payload = None
            if data.get("enc"):
                try:
                    key = self.session_keys.get(sid)
                    if not key:
                        self.sio.emit("error", {"message": "Session key not found."}, to=sid)
                        return
                    ct = base64.b64decode(data.get("ciphertext", ""))
                    iv = base64.b64decode(data.get("iv", ""))
                    message_text = ChatServer._aes_decrypt(ct, key, iv).decode("utf-8", errors="replace")
                except Exception as e:
                    self.sio.emit("error", {"message": f"Decrypt failed: {e}"}, to=sid)
                    return
            else:
                message_text = data.get("message")
                file_payload = _sanitize_file_payload(data.get("file"))

            if isinstance(message_text, str):
                message_text = message_text.strip()
            else:
                message_text = ""

            # Input validation
            if not message_text and not file_payload:
                logging.warning(
                    f"Empty message payload from {sender_username} ({sid}) ignored."
                )
                return

            # The data from the client is the entire payload. Add sender and server timestamp (if missing) and broadcast.
            if data:
                server_ts = data.get("timestamp")
                if not server_ts:
                    server_ts = datetime.now(timezone.utc).isoformat()
                # Prepare the payload to be sent to all clients
                broadcast_data = {
                    "username": sender_username,
                    "message": message_text,
                    "timestamp": server_ts,
                }
                if file_payload:
                    broadcast_data["file"] = file_payload

                with self.lock:
                    self.public_history.append(broadcast_data)
                    if len(self.public_history) > MAX_PUBLIC_HISTORY:
                        self.public_history.pop(0)

                # Emit to all clients. By removing `skip_sid`, the sender will also receive their own message.
                self.sio.emit("message", broadcast_data)

        @self.sio.event
        def private_message(sid, data):
            """Handle private messages between users."""
            with self.lock:
                sender_username = self.clients.get(sid, "Unknown")

            recipient_username = data.get("recipient")
            # Decrypt if encrypted
            file_payload = None
            if data.get("enc"):
                try:
                    key = self.session_keys.get(sid)
                    if not key:
                        self.sio.emit("error", {"message": "Session key not found."}, to=sid)
                        return
                    ct = base64.b64decode(data.get("ciphertext", ""))
                    iv = base64.b64decode(data.get("iv", ""))
                    message = ChatServer._aes_decrypt(ct, key, iv).decode("utf-8", errors="replace")
                except Exception as e:
                    self.sio.emit("error", {"message": f"Decrypt failed: {e}"}, to=sid)
                    return
            else:
                message = data.get("message")
                file_payload = _sanitize_file_payload(data.get("file"))

            # Input validation
            if (
                not recipient_username
                or not isinstance(recipient_username, str)
                or not recipient_username.strip()
            ):
                self.sio.emit(
                    "error", {"message": "A valid recipient is required."}, to=sid
                )
                return

            if isinstance(message, str):
                message = message.strip()
            else:
                message = ""

            if not message and not file_payload:
                self.sio.emit(
                    "error",
                    {
                        "message": "Cannot send an empty message. Attach a file or include text."
                    },
                    to=sid,
                )
                return

            logging.info(
                f"Private message request from {sender_username} to {recipient_username}"
            )

            # Prevent users from sending messages to themselves
            if sender_username == recipient_username:
                self.sio.emit(
                    "error",
                    {"message": "You cannot send a private message to yourself."},
                    to=sid,
                )
                logging.warning(
                    f"Private message failed: {sender_username} tried to message themselves."
                )
                return

            if recipient_username and message:
                # Find the recipient's socket ID
                recipient_sid = None
                with self.lock:
                    for client_sid, username in self.clients.items():
                        if username == recipient_username:
                            recipient_sid = client_sid
                            break

                if recipient_sid:
                    server_ts = data.get("timestamp")
                    if not server_ts:
                        server_ts = datetime.now(timezone.utc).isoformat()
                    with self.lock:
                        self.private_message_counter += 1
                        message_id = self.private_message_counter
                        self.private_messages[message_id] = {
                            "sender_sid": sid,
                            "recipient_sid": recipient_sid,
                            "status": "sent",
                        }

                    # Send private message to recipient only
                    payload = {
                        "sender": sender_username,
                        "recipient": recipient_username,
                        "message": message,
                        "timestamp": server_ts,
                    }

                    payload["message_id"] = message_id
                    recipient_payload = dict(payload)
                    recipient_payload["status"] = "delivered"
                    sender_payload = dict(payload)
                    sender_payload["status"] = "sent"
                    if file_payload:
                        recipient_payload["file"] = file_payload
                        sender_payload["file"] = file_payload

                    self.sio.emit(
                        "private_message_received", recipient_payload, to=recipient_sid
                    )
                    self.sio.emit("private_message_sent", sender_payload, to=sid)
                    logging.info(
                        f"Private message delivered from {sender_username} to {recipient_username}"
                    )
                else:
                    # Recipient not found - send error back to sender
                    self.sio.emit(
                        "error",
                        {
                            "message": f"User '{recipient_username}' not found or offline."
                        },
                        to=sid,
                    )
                    logging.warning(
                        f"Private message failed: {recipient_username} not found for sender {sender_username}"
                    )
            else:
                # Invalid message data - send error back to sender
                self.sio.emit(
                    "error",
                    {
                        "message": "Invalid private message format. Please specify recipient and message."
                    },
                    to=sid,
                )
                logging.warning(
                    f"Private message failed: invalid format from {sender_username}"
                )

        @self.sio.event
        def request_history(sid, data=None):
            with self.lock:
                history_snapshot = list(self.public_history)
            self.sio.emit("chat_history", {"messages": history_snapshot}, to=sid)

        @self.sio.event
        def typing(sid, data):
            with self.lock:
                username = self.clients.get(sid)

            if not username:
                logging.warning("Typing event from unknown SID: %s", sid)
                return

            context = data.get("context")
            is_typing = bool(data.get("is_typing"))

            if context == "public":
                self.sio.emit(
                    "public_typing",
                    {"username": username, "is_typing": is_typing},
                    skip_sid=sid,
                )
            elif context == "private":
                recipient_username = data.get("recipient")
                if not recipient_username:
                    return

                recipient_sid = None
                with self.lock:
                    for client_sid, name in self.clients.items():
                        if name == recipient_username:
                            recipient_sid = client_sid
                            break

                if recipient_sid:
                    self.sio.emit(
                        "private_typing",
                        {"username": username, "is_typing": is_typing},
                        to=recipient_sid,
                    )
            else:
                logging.debug(
                    "Ignoring typing event with invalid context '%s' from %s",
                    context,
                    username,
                )

        @self.sio.event
        def private_message_read(sid, data):
            message_ids = data.get("message_ids")
            if message_ids is None:
                return

            if not isinstance(message_ids, list):
                message_ids = [message_ids]

            acknowledgements = []

            with self.lock:
                for raw_id in message_ids:
                    try:
                        message_id = int(raw_id)
                    except (TypeError, ValueError):
                        continue

                    message_meta = self.private_messages.get(message_id)
                    if not message_meta:
                        continue

                    if message_meta.get("recipient_sid") != sid:
                        continue

                    if message_meta.get("status") == "seen":
                        continue

                    message_meta["status"] = "seen"
                    acknowledgements.append(
                        (message_meta.get("sender_sid"), message_id)
                    )

            for sender_sid, message_id in acknowledgements:
                if sender_sid:
                    self.sio.emit(
                        "private_message_read",
                        {"message_id": message_id},
                        to=sender_sid,
                    )

        # Removed user-to-user public key exchange in server-managed scheme

        @self.sio.event
        def public_file_chunk(sid, data):
            """Handle public encrypted file chunks."""
            self._handle_file_chunk(sid, data, is_private=False)

        @self.sio.event
        def private_file_chunk(sid, data):
            """Handle private encrypted file chunks."""
            self._handle_file_chunk(sid, data, is_private=True)

        @self.sio.event
        def file_transfer_ack(sid, data):
            """Handle file transfer acknowledgment."""
            transfer_id = data.get("transfer_id")
            success = data.get("success", False)
            error_msg = data.get("error", "")
            
            with self.file_transfer_lock:
                if transfer_id in self.active_file_transfers:
                    transfer_info = self.active_file_transfers[transfer_id]
                    
                    # Forward acknowledgment to sender
                    if transfer_info.get("sender_sid"):
                        self.sio.emit("file_transfer_ack", {
                            "transfer_id": transfer_id,
                            "success": success,
                            "error": error_msg
                        }, to=transfer_info["sender_sid"])
                    
                    # Clean up transfer
                    del self.active_file_transfers[transfer_id]

        # Deprecated encrypted_message route removed

        # Deprecated encrypted_private_message route removed

    def _handle_file_chunk(self, sid, data, is_private=False):
        """Handle encrypted file chunks."""
        transfer_id = data.get("transfer_id")
        chunk_index = data.get("chunk_index")
        chunk_data = data.get("chunk_data")
        is_last_chunk = data.get("is_last_chunk", False)
        metadata = data.get("metadata")
        recipient = data.get("recipient") if is_private else None
        
        with self.lock:
            sender_username = self.clients.get(sid, "Unknown")
        
        if not all([transfer_id, chunk_index is not None, chunk_data]):
            self.sio.emit("error", {"message": "Invalid file chunk"}, to=sid)
            return
        
        with self.file_transfer_lock:
            # Initialize transfer tracking
            if transfer_id not in self.active_file_transfers:
                self.active_file_transfers[transfer_id] = {
                    "sender_sid": sid,
                    "sender_username": sender_username,
                    "recipient": recipient,
                    "is_private": is_private,
                    "total_chunks": 0,
                    "received_chunks": 0,
                    "metadata": None,
                    "encrypted_chunks": {},
                }
            
            transfer_info = self.active_file_transfers[transfer_id]
            
            # Store metadata from first chunk
            if metadata and chunk_index == 0:
                transfer_info["metadata"] = metadata
                transfer_info["total_chunks"] = metadata.get("total_chunks", 0)
                transfer_info["iv"] = metadata.get("iv")
            
            transfer_info["received_chunks"] += 1
            transfer_info["encrypted_chunks"][chunk_index] = base64.b64decode(chunk_data)
            
            # If complete, decrypt and broadcast plaintext chunks
            if transfer_info["received_chunks"] >= transfer_info["total_chunks"] and transfer_info["total_chunks"] > 0:
                try:
                    key = self.session_keys.get(sid)
                    if not key:
                        self.sio.emit("error", {"message": "Session key not found for file."}, to=sid)
                        del self.active_file_transfers[transfer_id]
                        return
                    iv_b64 = transfer_info.get("iv") or ""
                    iv = base64.b64decode(iv_b64)
                    # Reassemble ciphertext
                    chunks_dict = transfer_info["encrypted_chunks"]
                    ciphertext = b"".join(chunks_dict[i] for i in sorted(chunks_dict.keys()))
                    plaintext = ChatServer._aes_decrypt(ciphertext, key, iv)

                    # Cache plaintext to disk
                    filename = transfer_info["metadata"].get("filename", "file")
                    safe_name = os.path.basename(filename) or "file"
                    out_path = os.path.join(self.upload_dir, f"{transfer_id}_{safe_name}")
                    try:
                        with open(out_path, "wb") as f:
                            f.write(plaintext)
                    except OSError as e:
                        logging.error(f"Failed to cache file: {e}")
                        self.sio.emit("error", {"message": f"Server failed caching file: {e}"}, to=sid)
                        del self.active_file_transfers[transfer_id]
                        return

                    # Stream plaintext from disk in chunks
                    chunk_size = transfer_info["metadata"].get("chunk_size", 64 * 1024)
                    total_size = len(plaintext)
                    total_chunks = max(1, (total_size + chunk_size - 1) // chunk_size)

                    def emit_first_and_get_target():
                        server_ts = datetime.now(timezone.utc).isoformat()
                        first_chunk = b""
                        try:
                            with open(out_path, "rb") as f:
                                first_chunk = f.read(chunk_size)
                        except OSError:
                            pass
                        first_payload = {
                            "transfer_id": transfer_id,
                            "chunk_index": 0,
                            "chunk_data": base64.b64encode(first_chunk).decode("utf-8"),
                            "is_last_chunk": total_chunks == 1,
                            "metadata": {
                                "filename": safe_name,
                                "total_size": total_size,
                                "total_chunks": total_chunks,
                                "chunk_size": chunk_size,
                                "username": sender_username,
                                "timestamp": server_ts,
                                "is_private": bool(transfer_info["is_private"]),
                                "recipient": transfer_info["recipient"] if transfer_info["is_private"] else "",
                            },
                        }
                        if transfer_info["is_private"] and transfer_info["recipient"]:
                            target_sid = None
                            with self.lock:
                                for client_sid, username in self.clients.items():
                                    if username == transfer_info["recipient"]:
                                        target_sid = client_sid
                                        break
                            if target_sid:
                                self.sio.emit("file_chunk", first_payload, to=target_sid)
                            else:
                                self.sio.emit("error", {"message": f"Recipient '{transfer_info['recipient']}' not found"}, to=sid)
                                return None
                            return target_sid
                        else:
                            self.sio.emit("file_chunk", first_payload)
                            return "__broadcast__"

                    target = emit_first_and_get_target()
                    if not target:
                        del self.active_file_transfers[transfer_id]
                        return

                    # Remaining chunks
                    try:
                        with open(out_path, "rb") as f:
                            f.seek(chunk_size)
                            index = 1
                            while True:
                                buf = f.read(chunk_size)
                                if not buf:
                                    break
                                payload = {
                                    "transfer_id": transfer_id,
                                    "chunk_index": index,
                                    "chunk_data": base64.b64encode(buf).decode("utf-8"),
                                    "is_last_chunk": index == total_chunks - 1,
                                }
                                if target == "__broadcast__":
                                    self.sio.emit("file_chunk", payload)
                                else:
                                    self.sio.emit("file_chunk", payload, to=target)
                                index += 1
                    except OSError as e:
                        logging.error(f"Failed streaming file: {e}")
                        self.sio.emit("error", {"message": f"Server streaming error: {e}"}, to=sid)
                        del self.active_file_transfers[transfer_id]
                        return

                    logging.info(f"Decrypted file broadcast complete: {transfer_id}")
                except Exception as e:
                    logging.error(f"File decrypt/broadcast failed: {e}")
                    self.sio.emit("error", {"message": f"File decrypt failed: {e}"}, to=sid)
                finally:
                    # Clean up transfer tracking
                    if transfer_id in self.active_file_transfers:
                        del self.active_file_transfers[transfer_id]


if __name__ == "__main__":
    server = ChatServer()
    HOST = os.environ.get("CHAT_HOST", "localhost")
    PORT = int(os.environ.get("CHAT_PORT", 5000))
    logging.info(f"Starting server on {HOST}:{PORT}")
    eventlet.wsgi.server(eventlet.listen((HOST, PORT)), server.app)
