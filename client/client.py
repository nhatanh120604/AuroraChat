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
    from .crypto_utils import create_crypto_manager, create_file_transfer
except ImportError:
    from crypto_utils import create_crypto_manager, create_file_transfer


MAX_FILE_BYTES = 5 * 1024 * 1024  # 5 MB safety cap


class ChatClient(QObject):
    messageReceived = Signal(str, str, "QVariant")  # username, message, file payload
    privateMessageReceived = Signal(
        str, str, str, int, str, "QVariant"
    )  # sender, recipient, message, message_id, status, file payload
    privateMessageSent = Signal(
        str, str, str, int, str, "QVariant"
    )  # sender, recipient, message, message_id, status, file payload
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
        
        # Crypto and file transfer
        self._crypto_manager = create_crypto_manager()
        self._active_transfers = {}  # transfer_id -> transfer_data
        self._received_chunks = {}  # transfer_id -> {chunk_index: data}
        self._transfer_lock = threading.Lock()
        
        self._setup_handlers()

    def _setup_handlers(self):
        @self._sio.event
        def connect():
            print("Connected")
            self._connected = True
            self._connecting = False
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
            file_payload = (
                data.get("file") if isinstance(data.get("file"), dict) else {}
            )
            self.messageReceived.emit(username, message, file_payload)

        @self._sio.on("private_message_received")
        def on_private_message(data):
            sender = data.get("sender", "Unknown")
            recipient = data.get("recipient", "Unknown")
            message = data.get("message", "")
            message_id = data.get("message_id")
            status = data.get("status", "")
            try:
                message_id = int(message_id)
            except (TypeError, ValueError):
                message_id = 0
            file_payload = (
                data.get("file") if isinstance(data.get("file"), dict) else {}
            )
            self.privateMessageReceived.emit(
                sender, recipient, message, message_id, status, file_payload
            )

        @self._sio.on("private_message_sent")
        def on_private_message_sent(data):
            sender = data.get("sender", "Unknown")
            recipient = data.get("recipient", "Unknown")
            message = data.get("message", "")
            status = data.get("status", "")
            message_id = data.get("message_id")
            try:
                message_id = int(message_id)
            except (TypeError, ValueError):
                message_id = 0
            file_payload = (
                data.get("file") if isinstance(data.get("file"), dict) else {}
            )
            self.privateMessageSent.emit(
                sender, recipient, message, message_id, status, file_payload
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
            
            # Automatically exchange public keys with all other users
            if self._username and self._username in users:
                self._exchange_keys_with_all_users(users)
            
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

        @self._sio.on("public_key_exchange")
        def on_public_key_exchange(data):
            """Handle public key exchange for encryption."""
            username = data.get("username")
            public_key_pem = data.get("public_key")
            if username and public_key_pem:
                try:
                    # Store peer's public key for encryption
                    self._crypto_manager.set_peer_public_key(public_key_pem)
                    print(f"Received public key from {username}")
                except Exception as e:
                    self._notify_error(f"Invalid public key from {username}: {e}")

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
            
            with self._transfer_lock:
                if transfer_id not in self._received_chunks:
                    self._received_chunks[transfer_id] = {}
                
                # Store chunk data
                self._received_chunks[transfer_id][chunk_index] = base64.b64decode(chunk_data)
                
                # Store metadata from first chunk
                if metadata and chunk_index == 0:
                    self._active_transfers[transfer_id] = metadata
                
                # Check if all chunks received
                expected_chunks = metadata.get("total_chunks", 0) if metadata else 0
                received_count = len(self._received_chunks[transfer_id])
                
                # Emit progress
                self.fileTransferProgress.emit(transfer_id, received_count, expected_chunks)
                
                if received_count >= expected_chunks and expected_chunks > 0:
                    # All chunks received, reassemble file
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
                # Exchange keys with all existing users
                if self._users:
                    self._exchange_keys_with_all_users(self._users)
            else:
                self._history_synced = False

    def _exchange_keys_with_all_users(self, users):
        """Automatically exchange public keys with all other users."""
        if not self._username or not users:
            return
        
        public_key = self.getPublicKey()
        for user in users:
            if user != self._username:
                try:
                    self._emit_when_connected("public_key_exchange", {
                        "target_username": user,
                        "public_key": public_key
                    })
                    print(f"Exchanged public key with {user}")
                except Exception as e:
                    print(f"Failed to exchange key with {user}: {e}")

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
                
                # Reassemble file
                file_transfer = create_file_transfer()
                decrypted_data, actual_hash = file_transfer.reassemble_file(
                    sorted_chunks,
                    metadata.get("encrypted_aes_key", ""),
                    metadata.get("iv", ""),
                    metadata.get("file_hash", "")
                )
                
                # Save file to temp directory
                filename = metadata.get("filename", "received_file")
                temp_path = self.saveFileToTemp(filename, base64.b64encode(decrypted_data).decode('utf-8'), "application/octet-stream")
                
                self.fileTransferComplete.emit(transfer_id, filename)
                print(f"File {filename} received and saved to {temp_path}")
                
        except Exception as e:
            self.fileTransferError.emit(transfer_id, f"Failed to reassemble file: {e}")
            print(f"Error reassembling file {transfer_id}: {e}")

    def _send_encrypted_file_chunks(self, file_data: bytes, filename: str, recipient: str = None):
        """Send file in encrypted chunks."""
        try:
            # Check if we have peer public key for encryption
            if not self._crypto_manager.peer_public_key:
                print("No peer public key available, using unencrypted fallback")
                return self._send_unencrypted_file(file_data, filename, recipient)
            
            # Prepare encrypted file
            file_transfer = create_file_transfer()
            transfer_data = file_transfer.prepare_file_for_sending(file_data, filename)
            
            transfer_id = transfer_data["transfer_id"]
            
            # Send metadata first (with first chunk)
            first_chunk = file_transfer.prepare_chunk(
                transfer_data["chunks"][0], 0, transfer_data
            )
            first_chunk["encrypted_aes_key"] = transfer_data["encrypted_aes_key"]
            first_chunk["iv"] = transfer_data["iv"]
            
            if recipient:
                first_chunk["recipient"] = recipient
                self._emit_when_connected("private_file_chunk", first_chunk)
            else:
                self._emit_when_connected("public_file_chunk", first_chunk)
            
            # Send remaining chunks
            for i, chunk_data in enumerate(transfer_data["chunks"][1:], 1):
                chunk = file_transfer.prepare_chunk(chunk_data, i, transfer_data)
                if recipient:
                    chunk["recipient"] = recipient
                    self._emit_when_connected("private_file_chunk", chunk)
                else:
                    self._emit_when_connected("public_file_chunk", chunk)
                
                # Small delay to prevent overwhelming the server
                time.sleep(0.01)
            
            return transfer_id
            
        except Exception as e:
            print(f"Encrypted transfer failed: {e}, trying unencrypted fallback")
            return self._send_unencrypted_file(file_data, filename, recipient)

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
                
                # Send text message first if any
                if text:
                    self._emit_when_connected("message", {"message": text})
                
                # Send encrypted file
                transfer_id = self._send_encrypted_file_chunks(file_data, filename)
                if transfer_id:
                    print(f"Started encrypted file transfer: {transfer_id}")
                else:
                    self._notify_error("Failed to start encrypted file transfer")
                    
            except Exception as e:
                self._notify_error(f"Failed to read file: {e}")
        else:
            # Regular text message
            if not text:
                self._notify_error("Cannot send an empty message.")
                return
            self._emit_when_connected("message", {"message": text})

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
                
                # Send text message first if any
                if text:
                    self._emit_when_connected("private_message", {"recipient": recip, "message": text})
                
                # Send encrypted file
                transfer_id = self._send_encrypted_file_chunks(file_data, filename, recip)
                if transfer_id:
                    print(f"Started encrypted private file transfer: {transfer_id}")
                else:
                    self._notify_error("Failed to start encrypted file transfer")
                    
            except Exception as e:
                self._notify_error(f"Failed to read file: {e}")
        else:
            # Regular private text message
            if not text:
                self._notify_error(
                    "Cannot send an empty private message. Attach a file or include text."
                )
                return
            self._emit_when_connected("private_message", {"recipient": recip, "message": text})

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

    def _get_username(self):
        return self._username

    username = Property(str, _get_username, notify=usernameChanged)

    @Slot(result=str)
    def getPublicKey(self):
        """Get this client's public key for exchange."""
        return self._crypto_manager.get_public_key_pem()

    @Slot(str)
    def exchangePublicKey(self, username: str):
        """Initiate public key exchange with a user."""
        if not username:
            self._notify_error("Username required for key exchange")
            return
        
        public_key = self.getPublicKey()
        self._emit_when_connected("public_key_exchange", {
            "target_username": username,
            "public_key": public_key
        })

    @Slot(str, result=bool)
    def hasPeerPublicKey(self, username: str):
        """Check if we have a public key for the given user."""
        # For now, return True if we have any peer public key
        # In a real implementation, you'd track keys per user
        return self._crypto_manager.peer_public_key is not None

    @Slot(result=str)
    def getEncryptionStatus(self):
        """Get current encryption status."""
        if self._crypto_manager.peer_public_key is not None:
            return "Encrypted"
        return "Unencrypted"


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
