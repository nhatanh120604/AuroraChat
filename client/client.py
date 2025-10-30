import sys
import os
import base64
import mimetypes
import tempfile
import threading
import uuid
import time
from pathlib import Path
from typing import Optional, Dict, Any
import socketio
from PySide6.QtCore import QObject, Signal, Slot, QUrl, Property
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine

try:
    from .crypto_utils import (
        generate_aes_key,
        rsa_encrypt_with_server_public_key,
        aes_encrypt,
    )
except ImportError:
    from crypto_utils import (
        generate_aes_key,
        rsa_encrypt_with_server_public_key,
        aes_encrypt,
    )


MAX_FILE_BYTES = 5 * 1024 * 1024  # 5 MB safety cap


class ChatClient(QObject):
    messageReceived = Signal(str, str, "QVariant")  # username, message, file payload
    messageReceivedEx = Signal(str, str, "QVariant", str)  # username, message, file payload, timestamp
    privateMessageReceived = Signal(
        str, str, str, int, str, "QVariant"
    )  # sender, recipient, message, message_id, status, file payload
    privateMessageSent = Signal(
        str, str, str, int, str, "QVariant"
    )  # sender, recipient, message, message_id, status, file payload
    privateMessageReceivedEx = Signal(
        str, str, str, int, str, "QVariant", str
    )  # + timestamp
    privateMessageSentEx = Signal(
        str, str, str, int, str, "QVariant", str
    )  # + timestamp
    privateMessageRead = Signal(int)  # message_id
    publicTypingReceived = Signal(str, bool)  # username, is typing
    privateTypingReceived = Signal(str, bool)  # username, is typing
    usersUpdated = Signal("QVariant")  # list of usernames
    disconnected = Signal()  # Signal to notify QML of disconnection
    errorReceived = Signal(str)  # Notify UI about errors
    usernameChanged = Signal(str)  # Notify UI when username changes
    generalHistoryReceived = Signal("QVariant")  # Provide public chat history snapshot
    fileTransferProgress = Signal(str, int, int)  # transfer_id, current_chunk, total_chunks
    fileTransferComplete = Signal(str, str)  # transfer_id, filename
    fileTransferError = Signal(str, str)  # transfer_id, error_message

    def __init__(self, url="http://localhost:5000"):
        super().__init__()
        self._url = url
        self._username = ""
        self._desired_username = ""
        self._sio = socketio.Client()
        self._connected = False
        self._connecting = False
        self._users = []
        self._connect_lock = threading.Lock()
        self._pending_lock = threading.Lock()
        self._pending_events = []
        self._history_synced = False
        self._public_typing_flag = False
        self._private_typing_flags = {}
        
        # Session AES key (exchanged with server via RSA)
        self._session_aes_key: Optional[bytes] = None
        self._session_ready: bool = False
        self._post_key_queue = []  # events queued until session key ack
        self._active_transfers = {}  # transfer_id -> transfer_data
        self._received_chunks = {}  # transfer_id -> {chunk_index: data}
        self._transfer_lock = threading.Lock()
        self._completed_transfers = set()
        self._debug_enabled = True
        
        self._setup_handlers()

    def _dbg(self, *args):
        try:
            if self._debug_enabled:
                print("[CLIENT]", *args)
        except Exception:
            pass

    def _setup_handlers(self):
        @self._sio.event
        def connect():
            print("Connected")
            self._connected = True
            self._connecting = False
            # Establish session AES key with server
            try:
                self._session_aes_key = generate_aes_key()
                encrypted = rsa_encrypt_with_server_public_key(self._session_aes_key)
                self._sio.emit("session_key", {"encrypted_aes_key": encrypted})
            except Exception as e:
                self._notify_error(f"Failed to exchange session key: {e}")
            queued_register = False
            with self._pending_lock:
                queued_register = any(
                    evt == "register" for evt, _ in self._pending_events
                )
                pending = list(self._pending_events)
                self._pending_events.clear()
            if self._desired_username and not queued_register:
                pending.insert(0, ("register", {"username": self._desired_username}))
            for event, payload in pending:
                try:
                    self._sio.emit(event, payload)
                except Exception as exc:
                    self._notify_error(f"Failed to send '{event}': {exc}")

        @self._sio.on("session_key_ok")
        def on_session_key_ok(data):
            self._session_ready = True
            # Flush queued events that require session key
            with self._pending_lock:
                queued = list(self._post_key_queue)
                self._post_key_queue.clear()
            for event, payload in queued:
                try:
                    self._sio.emit(event, payload)
                except Exception as exc:
                    self._notify_error(f"Failed to send '{event}': {exc}")

        @self._sio.event
        def disconnect():
            print("Disconnected from server")
            self._connected = False
            self._connecting = False
            self._users = []
            self._pending_events.clear()
            self._public_typing_flag = False
            self._private_typing_flags.clear()
            self.usersUpdated.emit([])
            self._set_username("")
            self.disconnected.emit()  # Notify the UI
            self._history_synced = False

        @self._sio.on("message")
        def on_message(data):
            username = data.get("username", "Unknown")
            message = data.get("message", "")
            timestamp = data.get("timestamp", "")
            file_payload = (
                data.get("file") if isinstance(data.get("file"), dict) else {}
            )
            self.messageReceived.emit(username, message, file_payload)
            self.messageReceivedEx.emit(username, message, file_payload, timestamp)

        @self._sio.on("private_message_received")
        def on_private_message(data):
            sender = data.get("sender", "Unknown")
            recipient = data.get("recipient", "Unknown")
            message = data.get("message", "")
            message_id = data.get("message_id")
            status = data.get("status", "")
            timestamp = data.get("timestamp", "")
            try:
                message_id = int(message_id)
            except (TypeError, ValueError):
                message_id = 0
            file_payload = (
                data.get("file") if isinstance(data.get("file"), dict) else {}
            )
            self.privateMessageReceivedEx.emit(
                sender, recipient, message, message_id, status, file_payload, timestamp
            )

        @self._sio.on("private_message_sent")
        def on_private_message_sent(data):
            sender = data.get("sender", "Unknown")
            recipient = data.get("recipient", "Unknown")
            message = data.get("message", "")
            status = data.get("status", "")
            message_id = data.get("message_id")
            timestamp = data.get("timestamp", "")
            try:
                message_id = int(message_id)
            except (TypeError, ValueError):
                message_id = 0
            file_payload = (
                data.get("file") if isinstance(data.get("file"), dict) else {}
            )
            self.privateMessageSentEx.emit(
                sender, recipient, message, message_id, status, file_payload, timestamp
            )

        @self._sio.on("private_message_read")
        def on_private_message_read(data):
            message_id = data.get("message_id")
            try:
                message_id = int(message_id)
            except (TypeError, ValueError):
                return
            self.privateMessageRead.emit(message_id)

        @self._sio.on("public_typing")
        def on_public_typing(data):
            username = data.get("username")
            is_typing = bool(data.get("is_typing"))
            if username:
                self.publicTypingReceived.emit(username, is_typing)

        @self._sio.on("private_typing")
        def on_private_typing(data):
            username = data.get("username")
            is_typing = bool(data.get("is_typing"))
            if username:
                self.privateTypingReceived.emit(username, is_typing)

        # Replaced user_joined with update_user_list to sync with server
        @self._sio.on("update_user_list")
        def on_update_user_list(data):
            users = data.get("users", [])
            self._users = users
            if self._desired_username and self._desired_username in users:
                self._set_username(self._desired_username)
            elif self._username and self._username not in users:
                self._set_username("")
            
            # No per-user key exchange under server-managed encryption
            
            self.usersUpdated.emit(self._users.copy())

        @self._sio.on("chat_history")
        def on_chat_history(data):
            messages = data.get("messages", [])
            self.generalHistoryReceived.emit(messages)

        @self._sio.on("error")
        def on_error(data):
            message = data.get("message", "An unknown error occurred.")
            self._notify_error(message)
            lowered = message.lower()
            is_username_error = "username" in lowered or "name" in lowered
            if (
                is_username_error
                and self._desired_username
                and self._desired_username == self._username
            ):
                # Preserve desired username for reconnection attempts but allow UI edits
                self._set_username("")
            if self._desired_username and is_username_error:
                self._desired_username = ""

        # No per-user public key exchange in the new scheme

        @self._sio.on("file_chunk")
        def on_file_chunk(data):
            """Handle incoming file chunks."""
            transfer_id = data.get("transfer_id")
            chunk_index = data.get("chunk_index")
            chunk_data = data.get("chunk_data")
            is_last_chunk = data.get("is_last_chunk", False)
            metadata = data.get("metadata")
            
            if not all([transfer_id, chunk_index is not None, chunk_data]):
                self._notify_error("Invalid file chunk received")
                return
            
            self._dbg("file_chunk received:",
                      "id=", transfer_id,
                      "idx=", chunk_index,
                      "last=", is_last_chunk,
                      "meta?=", bool(metadata))

            ready_to_reassemble = False
            with self._transfer_lock:
                if transfer_id in self._completed_transfers:
                    # Ignore any late chunks after completion
                    return
                if transfer_id not in self._received_chunks:
                    self._received_chunks[transfer_id] = {}
                
                # Store chunk data
                self._received_chunks[transfer_id][chunk_index] = base64.b64decode(chunk_data)
                
                # Store metadata from first chunk
                if metadata and chunk_index == 0:
                    self._active_transfers[transfer_id] = metadata
                
                # Check if all chunks received (use stored metadata if not provided on this chunk)
                stored_meta = self._active_transfers.get(transfer_id, {})
                expected_chunks = 0
                if metadata and metadata.get("total_chunks"):
                    expected_chunks = int(metadata.get("total_chunks", 0))
                elif stored_meta and stored_meta.get("total_chunks"):
                    expected_chunks = int(stored_meta.get("total_chunks", 0))
                # Fallback to last-chunk index if server did not include total
                if expected_chunks == 0 and bool(is_last_chunk) and isinstance(chunk_index, int):
                    expected_chunks = int(chunk_index) + 1
                received_count = len(self._received_chunks[transfer_id])
                self._dbg("file_chunk progress:",
                          "id=", transfer_id,
                          "received=", received_count,
                          "expected=", expected_chunks)
                
                # Emit progress
                self.fileTransferProgress.emit(transfer_id, received_count, expected_chunks)
                
                if received_count >= expected_chunks and expected_chunks > 0:
                    # All chunks received, reassemble file (outside lock to avoid deadlock)
                    ready_to_reassemble = True

            if ready_to_reassemble:
                self._dbg("reassembling file:", transfer_id)
                self._reassemble_file(transfer_id)

        @self._sio.on("file_transfer_ack")
        def on_file_transfer_ack(data):
            """Handle file transfer acknowledgment."""
            transfer_id = data.get("transfer_id")
            success = data.get("success", False)
            error_msg = data.get("error", "")
            
            if success:
                self.fileTransferComplete.emit(transfer_id, "")
            else:
                self.fileTransferError.emit(transfer_id, error_msg)
            
            # Clean up transfer data
            with self._transfer_lock:
                self._active_transfers.pop(transfer_id, None)
                self._received_chunks.pop(transfer_id, None)

        # Messages from server are plaintext after server-side decryption

        # Private messages are also plaintext after server-side decryption

        # Sent confirmations are plaintext

    def _ensure_connected(self):
        # use a lock to prevent race conditions on state flags
        with self._connect_lock:
            if self._connected or self._connecting:
                return
            self._connecting = True

        def _connect():
            try:
                # blocking connect in background thread
                self._sio.connect(self._url)
            except Exception as e:
                print("Connection error:", e)
                # if connect fails, reset the flag so we can try again
                self._connecting = False
                self._notify_error(f"Connection error: {e}")

        t = threading.Thread(target=_connect, daemon=True)
        t.start()

    def _emit_when_connected(self, event, data):
        send_immediately = False
        with self._pending_lock:
            if self._connected:
                send_immediately = True
            else:
                self._pending_events.append((event, data))
        if send_immediately:
            try:
                self._sio.emit(event, data)
            except Exception as exc:
                self._notify_error(f"Failed to send '{event}': {exc}")
        else:
            self._ensure_connected()

    def _emit_post_key(self, event: str, payload: dict):
        # If session key is confirmed, emit; otherwise queue until ack
        if self._session_ready:
            self._emit_when_connected(event, payload)
        else:
            with self._pending_lock:
                self._post_key_queue.append((event, payload))

    def _normalize_file_path(self, file_url: str) -> Optional[Path]:
        candidate = (file_url or "").strip()
        if not candidate:
            return None
        qurl = QUrl(candidate)
        if qurl.isValid() and qurl.scheme().lower() == "file":
            if qurl.isLocalFile():
                candidate = qurl.toLocalFile()
            else:
                return None
        path = Path(candidate)
        return path

    def _prepare_file_payload(self, file_path: Path) -> Optional[dict]:
        try:
            resolved = file_path.resolve(strict=True)
        except (OSError, RuntimeError):
            self._notify_error("Selected file could not be accessed.")
            return None

        if not resolved.is_file():
            self._notify_error("Selected file is not a regular file.")
            return None

        try:
            size = resolved.stat().st_size
        except OSError:
            self._notify_error("Unable to determine file size.")
            return None

        if size <= 0:
            self._notify_error("Cannot send empty files.")
            return None

        if size > MAX_FILE_BYTES:
            self._notify_error("File exceeds the 5 MB limit.")
            return None

        try:
            raw = resolved.read_bytes()
        except OSError:
            self._notify_error("Failed to read the selected file.")
            return None

        encoded = base64.b64encode(raw).decode("ascii")
        mime, _ = mimetypes.guess_type(str(resolved))
        mime = mime or "application/octet-stream"

        return {
            "name": resolved.name,
            "size": size,
            "mime": mime,
            "data": encoded,
        }

    def _notify_error(self, message: str):
        print("Error:", message)
        self.errorReceived.emit(message)

    def _set_username(self, value: str):
        value = value or ""
        if self._username != value:
            self._username = value
            self.usernameChanged.emit(self._username)
            if self._username:
                self._ensure_history_synced()
            else:
                self._history_synced = False

    # Key exchange with users removed

    def _ensure_history_synced(self):
        if self._history_synced:
            return
        self._emit_when_connected("request_history", {})
        self._history_synced = True

    def _reassemble_file(self, transfer_id: str):
        """Reassemble and decrypt received file chunks."""
        try:
            with self._transfer_lock:
                if transfer_id not in self._active_transfers or transfer_id not in self._received_chunks:
                    return
                
                metadata = self._active_transfers[transfer_id]
                chunks_dict = self._received_chunks[transfer_id]
                
                # Sort chunks by index
                sorted_chunks = [chunks_dict[i] for i in sorted(chunks_dict.keys())]
                
                # If metadata lacks encryption fields, treat as plaintext
                if not metadata or metadata.get("encrypted_aes_key") or metadata.get("iv"):
                    # Backward compatibility (old encrypted flow not expected now)
                    data_bytes = b"".join(sorted_chunks)
                else:
                    data_bytes = b"".join(sorted_chunks)

                # Save file to temp directory
                filename = metadata.get("filename", "received_file")
                temp_path = self.saveFileToTemp(filename, base64.b64encode(data_bytes).decode('utf-8'), "application/octet-stream")
                
                self.fileTransferComplete.emit(transfer_id, filename)
                print(f"File {filename} received and saved to {temp_path}")

                # Emit a message entry so the UI shows the received file in the chat feed
                try:
                    import mimetypes as _m
                    mime, _ = _m.guess_type(filename)
                    mime = mime or "application/octet-stream"
                except Exception:
                    mime = "application/octet-stream"
                is_private = False
                recipient = ""
                if isinstance(metadata, dict):
                    recipient = metadata.get("recipient", "") or ""
                    # Treat as private if server tagged it or if a recipient is specified
                    is_private = bool(metadata.get("is_private")) or (len(recipient) > 0)
                file_payload = {
                    "name": filename,
                    "size": len(data_bytes),
                    "mime": mime,
                    "data": base64.b64encode(data_bytes).decode('ascii'),
                    "is_private": is_private,
                    "recipient": recipient,
                    "transfer_id": transfer_id,
                }
                username = metadata.get("username", "Unknown")
                # Emit directly; Qt will queue the signal to the GUI thread.
                self._dbg("emitting file message:", username, file_payload.get("name", ""), file_payload.get("size", 0))
                ts = metadata.get("timestamp") if isinstance(metadata, dict) else ""
                if not ts:
                    from datetime import datetime, timezone
                    ts = datetime.now(timezone.utc).isoformat()
                if is_private:
                    # Route to private conversation
                    sender = username
                    # Determine outgoing vs incoming
                    is_outgoing = (self._username and sender == self._username)
                    if is_outgoing:
                        # Sent by us: we already appended optimistically when sending; avoid duplicate
                        self._dbg("skip duplicate append for sender private file:", transfer_id)
                    else:
                        # Received by us
                        self.privateMessageReceivedEx.emit(sender, recipient or self._username, "", 0, "delivered", file_payload, ts)
                else:
                    # Public feed
                    self.messageReceived.emit(username, "", file_payload)
                    self.messageReceivedEx.emit(username, "", file_payload, ts)

                # Mark completed and cleanup to avoid duplicate reassembly
                with self._transfer_lock:
                    self._completed_transfers.add(transfer_id)
                    self._active_transfers.pop(transfer_id, None)
                    self._received_chunks.pop(transfer_id, None)
                
        except Exception as e:
            self.fileTransferError.emit(transfer_id, f"Failed to reassemble file: {e}")
            print(f"Error reassembling file {transfer_id}: {e}")

    def _send_encrypted_file_chunks(self, file_data: bytes, filename: str, recipient: str = None):
        """Send file in encrypted chunks."""
        try:
            if not self._session_aes_key:
                self._notify_error("Session key not established; cannot send file securely.")
                return None

            # Encrypt entire file with session AES key
            ciphertext, iv = aes_encrypt(file_data, self._session_aes_key)
            iv_b64 = base64.b64encode(iv).decode("utf-8")

            # Chunk ciphertext
            chunk_size = 64 * 1024
            chunks = [ciphertext[i:i+chunk_size] for i in range(0, len(ciphertext), chunk_size)]
            total_chunks = len(chunks)
            transfer_id = uuid.uuid4().hex

            # Prepare first chunk payload with metadata
            first_chunk = {
                "transfer_id": transfer_id,
                "chunk_index": 0,
                "chunk_data": base64.b64encode(chunks[0]).decode("utf-8"),
                "is_last_chunk": total_chunks == 1,
                "metadata": {
                    "filename": filename,
                    "total_size": len(file_data),
                    "total_chunks": total_chunks,
                    "chunk_size": chunk_size,
                    "iv": iv_b64,
                },
            }
            self._dbg("sending file first chunk:", transfer_id, "chunks=", total_chunks)

            # For private sends, optimistically show the file in the sender's private thread immediately
            if recipient:
                try:
                    import mimetypes as _m
                    mime, _ = _m.guess_type(filename)
                    mime = mime or "application/octet-stream"
                except Exception:
                    mime = "application/octet-stream"
                sender_username = self._username or "You"
                file_payload = {
                    "name": filename,
                    "size": len(file_data),
                    "mime": mime,
                    "data": base64.b64encode(file_data).decode('ascii'),
                    "is_private": True,
                    "recipient": recipient,
                    "transfer_id": transfer_id,
                }
                from datetime import datetime, timezone
                ts = datetime.now(timezone.utc).isoformat()
                self.privateMessageSent.emit(sender_username, recipient, "", 0, "sent", file_payload)
                self.privateMessageSentEx.emit(sender_username, recipient, "", 0, "sent", file_payload, ts)
            
            if recipient:
                first_chunk["recipient"] = recipient
                self._emit_post_key("private_file_chunk", first_chunk)
            else:
                self._emit_post_key("public_file_chunk", first_chunk)
            
            # Send remaining chunks
            for i, chunk_data in enumerate(chunks[1:], 1):
                chunk = {
                    "transfer_id": transfer_id,
                    "chunk_index": i,
                    "chunk_data": base64.b64encode(chunk_data).decode("utf-8"),
                    "is_last_chunk": i == total_chunks - 1,
                }
                self._dbg("sending file chunk:", transfer_id, "idx=", i, "last=", i == total_chunks - 1)
                if recipient:
                    chunk["recipient"] = recipient
                    self._emit_post_key("private_file_chunk", chunk)
                else:
                    self._emit_post_key("public_file_chunk", chunk)
                
                # Small delay to prevent overwhelming the server
                time.sleep(0.01)
            
            return transfer_id
            
        except Exception as e:
            self._notify_error(f"Secure transfer failed: {e}")
            return None

    def _send_unencrypted_file(self, file_data: bytes, filename: str, recipient: str = None):
        """Send file without encryption as fallback."""
        try:
            # Use the original file payload method for unencrypted transfer
            file_payload = {
                "name": filename,
                "size": len(file_data),
                "mime": "application/octet-stream",
                "data": base64.b64encode(file_data).decode('ascii'),
            }
            
            if recipient:
                self._emit_when_connected("private_message", {
                    "recipient": recipient,
                    "file": file_payload
                })
            else:
                self._emit_when_connected("message", {
                    "file": file_payload
                })
            
            print(f"Sent unencrypted file: {filename}")
            return f"unencrypted_{uuid.uuid4().hex[:8]}"
            
        except Exception as e:
            self._notify_error(f"Failed to send unencrypted file: {e}")
            return None

    def _send_secure_text_message(self, text: str):
        try:
            if not self._session_aes_key:
                self._notify_error("Session key not established; cannot send message securely.")
                return
            ciphertext, iv = aes_encrypt(text.encode("utf-8"), self._session_aes_key)
            payload = {
                "enc": True,
                "ciphertext": base64.b64encode(ciphertext).decode("utf-8"),
                "iv": base64.b64encode(iv).decode("utf-8"),
            }
            self._emit_post_key("message", payload)
        except Exception as e:
            self._notify_error(f"Failed to send secure message: {e}")

    def _send_secure_private_message(self, recipient: str, text: str):
        try:
            if not self._session_aes_key:
                self._notify_error("Session key not established; cannot send message securely.")
                return
            ciphertext, iv = aes_encrypt(text.encode("utf-8"), self._session_aes_key)
            payload = {
                "recipient": recipient,
                "enc": True,
                "ciphertext": base64.b64encode(ciphertext).decode("utf-8"),
                "iv": base64.b64encode(iv).decode("utf-8"),
            }
            self._emit_post_key("private_message", payload)
        except Exception as e:
            self._notify_error(f"Failed to send secure private message: {e}")

    @Slot(str, result="QVariant")
    def inspectFile(self, file_url: str):
        file_path = self._normalize_file_path(file_url)
        if not file_path:
            self._notify_error("Invalid file selection.")
            return {}
        try:
            resolved = file_path.resolve(strict=True)
        except (OSError, RuntimeError):
            self._notify_error("Selected file could not be accessed.")
            return {}

        if not resolved.is_file():
            self._notify_error("Selected file is not a regular file.")
            return {}

        try:
            size = resolved.stat().st_size
        except OSError:
            self._notify_error("Unable to determine file size.")
            return {}

        if size <= 0:
            self._notify_error("Cannot send empty files.")
            return {}

        if size > MAX_FILE_BYTES:
            self._notify_error("File exceeds the 5 MB limit.")
            return {}

        mime, _ = mimetypes.guess_type(str(resolved))
        mime = mime or "application/octet-stream"

        return {
            "path": str(resolved),
            "name": resolved.name,
            "size": size,
            "mime": mime,
        }

    @Slot(str)
    def register(self, username: str):
        desired = (username or "").strip()
        if not desired:
            self._notify_error("Username cannot be empty.")
            return
        self._desired_username = desired
        self._emit_when_connected("register", {"username": desired})

    @Slot(str)
    def sendMessage(self, message: str):
        self.sendMessageWithAttachment(message, "")

    @Slot(str, str)
    def sendMessageWithAttachment(self, message: str, file_url: str):
        text = (message or "").strip()
        file_url = (file_url or "").strip()
        
        if file_url:
            # Use encrypted file transfer for file attachments
            file_path = self._normalize_file_path(file_url)
            if not file_path:
                self._notify_error("Invalid file selection.")
                return
            
            try:
                file_data = file_path.read_bytes()
                filename = file_path.name
                
                # Send encrypted text message first if any
                if text:
                    self._send_secure_text_message(text)
                
                # Send encrypted file
                transfer_id = self._send_encrypted_file_chunks(file_data, filename)
                if transfer_id:
                    print(f"Started encrypted file transfer: {transfer_id}")
                else:
                    self._notify_error("Failed to start encrypted file transfer")
                    
            except Exception as e:
                self._notify_error(f"Failed to read file: {e}")
        else:
            # Encrypted text message
            if not text:
                self._notify_error("Cannot send an empty message.")
                return
            self._send_secure_text_message(text)

    @Slot(str)
    def sendPublicFile(self, file_url: str):
        self.sendMessageWithAttachment("", file_url)

    @Slot(str, str)
    def sendPrivateMessage(self, recipient: str, message: str):
        self.sendPrivateMessageWithAttachment(recipient, message, "")

    @Slot(str, str, str)
    def sendPrivateMessageWithAttachment(
        self, recipient: str, message: str, file_url: str
    ):
        recip = (recipient or "").strip()
        text = (message or "").strip()
        file_url = (file_url or "").strip()
        if not recip:
            self._notify_error("Recipient is required for private messages.")
            return
        
        if file_url:
            # Use encrypted file transfer for private file attachments
            file_path = self._normalize_file_path(file_url)
            if not file_path:
                self._notify_error("Invalid file selection.")
                return
            
            try:
                file_data = file_path.read_bytes()
                filename = file_path.name
                
                # Send encrypted text message first if any
                if text:
                    self._send_secure_private_message(recip, text)
                
                # Send encrypted file
                transfer_id = self._send_encrypted_file_chunks(file_data, filename, recip)
                if transfer_id:
                    print(f"Started encrypted private file transfer: {transfer_id}")
                else:
                    self._notify_error("Failed to start encrypted file transfer")
                    
            except Exception as e:
                self._notify_error(f"Failed to read file: {e}")
        else:
            # Encrypted private text message
            if not text:
                self._notify_error(
                    "Cannot send an empty private message. Attach a file or include text."
                )
                return
            self._send_secure_private_message(recip, text)

    @Slot(str, str)
    def sendPrivateFile(self, recipient: str, file_url: str):
        self.sendPrivateMessageWithAttachment(recipient, "", file_url)

    @Slot()
    def disconnect(self):
        try:
            if self._connected:
                self._sio.disconnect()
        except Exception:
            pass
        finally:
            self._desired_username = ""

    def _send_typing_state(
        self, context: str, is_typing: bool, recipient: Optional[str] = None
    ):
        payload = {"context": context, "is_typing": bool(is_typing)}
        if recipient:
            payload["recipient"] = recipient
        self._emit_when_connected("typing", payload)

    @Slot(bool)
    def indicatePublicTyping(self, is_typing: bool):
        state = bool(is_typing)
        if self._public_typing_flag == state:
            return
        self._public_typing_flag = state
        self._send_typing_state("public", state)

    @Slot(str, bool)
    def indicatePrivateTyping(self, recipient: str, is_typing: bool):
        recip = (recipient or "").strip()
        if not recip:
            return
        state = bool(is_typing)
        previous = self._private_typing_flags.get(recip)
        if previous == state:
            return
        if state:
            self._private_typing_flags[recip] = True
        else:
            self._private_typing_flags.pop(recip, None)
        self._send_typing_state("private", state, recip)

    @Slot(str, "QVariantList")
    def markPrivateMessagesRead(self, recipient: str, message_ids):
        recip = (recipient or "").strip()
        if not recip or not message_ids:
            return
        sanitized = []
        for mid in message_ids:
            try:
                sanitized.append(int(mid))
            except (TypeError, ValueError):
                continue
        if not sanitized:
            return
        payload = {"recipient": recip, "message_ids": sanitized}
        self._emit_when_connected("private_message_read", payload)

    @Slot(str, str, str, result=str)
    def saveFileToTemp(self, filename: str, data: str, mime: str):
        safe_name = Path(filename or "attachment").name
        if not data:
            self._notify_error("Attachment data is missing.")
            return ""
        try:
            binary = base64.b64decode(data)
        except (ValueError, TypeError):
            self._notify_error("Attachment could not be decoded.")
            return ""

        suffix = Path(safe_name).suffix
        unique_name = f"chatroom_{uuid.uuid4().hex}{suffix}"
        target = Path(tempfile.gettempdir()) / unique_name
        try:
            target.write_bytes(binary)
        except OSError as exc:
            self._notify_error(f"Failed to save file: {exc}")
            return ""

        return QUrl.fromLocalFile(str(target)).toString()

    @Slot(str, str, str, result=str)
    def saveFileToDownloads(self, filename: str, data: str, mime: str):
        safe_name = Path(filename or "download").name
        if not data:
            self._notify_error("Attachment data is missing.")
            return ""
        try:
            binary = base64.b64decode(data)
        except (ValueError, TypeError):
            self._notify_error("Attachment could not be decoded.")
            return ""

        # Use OS Downloads directory
        try:
            from PySide6.QtCore import QStandardPaths
            downloads_path = QStandardPaths.writableLocation(QStandardPaths.DownloadLocation)
            downloads = Path(downloads_path) if downloads_path else (Path.home() / "Downloads")
        except Exception:
            downloads = Path.home() / "Downloads"
        try:
            downloads.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass

        target = downloads / safe_name
        # Ensure unique filename if exists
        if target.exists():
            base = target.stem
            ext = target.suffix
            counter = 1
            while True:
                candidate = downloads / f"{base} ({counter}){ext}"
                if not candidate.exists():
                    target = candidate
                    break
                counter += 1
        try:
            target.write_bytes(binary)
        except OSError as exc:
            self._notify_error(f"Failed to save file: {exc}")
            return ""

        return QUrl.fromLocalFile(str(target)).toString()

    @Slot(str, str, result=str)
    def saveFileToPath(self, target_url: str, data: str):
        if not data:
            self._notify_error("Attachment data is missing.")
            return ""
        try:
            binary = base64.b64decode(data)
        except (ValueError, TypeError):
            self._notify_error("Attachment could not be decoded.")
            return ""
        try:
            qurl = QUrl(target_url)
            if qurl.isValid():
                path = qurl.toLocalFile() if qurl.isLocalFile() else qurl.toString()
            else:
                path = target_url
            target = Path(path)
            if target.parent:
                try:
                    Path(target.parent).mkdir(parents=True, exist_ok=True)
                except OSError:
                    pass
            target.write_bytes(binary)
            return QUrl.fromLocalFile(str(target)).toString()
        except OSError as exc:
            self._notify_error(f"Failed to save file: {exc}")
            return ""

    def _get_username(self):
        return self._username

    username = Property(str, _get_username, notify=usernameChanged)

    # Deprecated key exchange/UI helpers removed under server-managed scheme


def main():
    os.environ.setdefault("QT_QUICK_CONTROLS_STYLE", "Basic")
    app = QGuiApplication(sys.argv)
    engine = QQmlApplicationEngine()

    # create client and expose to QML
    chat = ChatClient("http://localhost:5000")
    engine.rootContext().setContextProperty("chatClient", chat)

    # load QML relative to this file
    base = os.path.dirname(__file__)
    qml_path = os.path.join(base, "qml", "Main.qml")
    engine.load(QUrl.fromLocalFile(qml_path))

    # graceful shutdown
    app.aboutToQuit.connect(chat.disconnect)

    if not engine.rootObjects():
        return -1
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
