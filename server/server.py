import socketio
from flask import Flask
import eventlet
import logging
import os
import threading
#from dotenv import load_dotenv


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
        
        # File transfer tracking
        self.active_file_transfers = {}  # transfer_id -> transfer_info
        self.file_transfer_lock = threading.Lock()

        self.register_events()

    def register_events(self):

        @self.sio.event
        def connect(sid, environ):
            logging.info(f"Client connected: {sid}")
            with self.lock:
                history_snapshot = list(self.public_history)
            if history_snapshot:
                self.sio.emit("chat_history", {"messages": history_snapshot}, to=sid)

        @self.sio.event
        def disconnect(sid):
            logging.info(f"Client disconnected: {sid}")
            with self.lock:
                username = self.clients.pop(sid, None)
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
                # Enforce unique usernames
                if username in self.clients.values():
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
        def message(sid, data):
            """Handle incoming messages from a client and broadcast them."""
            with self.lock:
                sender_username = self.clients.get(sid, "Unknown")

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

            # The data from the test client is the entire payload.
            # We need to add the sender's username and broadcast it.
            if data:
                # Prepare the payload to be sent to all clients
                broadcast_data = {
                    "username": sender_username,
                    "message": message_text,
                    "timestamp": data.get(
                        "timestamp"
                    ),  # Forward the timestamp for latency calculation
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
                    }
                    # Forward timestamp if present for latency calculation
                    if "timestamp" in data:
                        payload["timestamp"] = data["timestamp"]

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

        @self.sio.event
        def public_key_exchange(sid, data):
            """Handle public key exchange between users."""
            with self.lock:
                sender_username = self.clients.get(sid, "Unknown")
            
            target_username = data.get("target_username")
            public_key = data.get("public_key")
            
            if not target_username or not public_key:
                self.sio.emit("error", {"message": "Invalid key exchange data"}, to=sid)
                return
            
            # Find target user's SID
            target_sid = None
            with self.lock:
                for client_sid, username in self.clients.items():
                    if username == target_username:
                        target_sid = client_sid
                        break
            
            if target_sid:
                # Forward public key to target user
                self.sio.emit("public_key_exchange", {
                    "username": sender_username,
                    "public_key": public_key
                }, to=target_sid)
                logging.info(f"Public key exchange: {sender_username} -> {target_username}")
            else:
                self.sio.emit("error", {
                    "message": f"User '{target_username}' not found for key exchange"
                }, to=sid)

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
                    "metadata": None
                }
            
            transfer_info = self.active_file_transfers[transfer_id]
            
            # Store metadata from first chunk
            if metadata and chunk_index == 0:
                transfer_info["metadata"] = metadata
                transfer_info["total_chunks"] = metadata.get("total_chunks", 0)
                transfer_info["encrypted_aes_key"] = data.get("encrypted_aes_key")
                transfer_info["iv"] = data.get("iv")
            
            transfer_info["received_chunks"] += 1
            
            # Prepare chunk data for forwarding
            chunk_payload = {
                "transfer_id": transfer_id,
                "chunk_index": chunk_index,
                "chunk_data": chunk_data,
                "is_last_chunk": is_last_chunk
            }
            
            # Add metadata to first chunk
            if chunk_index == 0:
                chunk_payload["metadata"] = transfer_info.get("metadata")
                chunk_payload["encrypted_aes_key"] = transfer_info.get("encrypted_aes_key")
                chunk_payload["iv"] = transfer_info.get("iv")
            
            # Forward chunk to appropriate recipients
            if is_private and recipient:
                # Find recipient's SID
                recipient_sid = None
                with self.lock:
                    for client_sid, username in self.clients.items():
                        if username == recipient:
                            recipient_sid = client_sid
                            break
                
                if recipient_sid:
                    self.sio.emit("file_chunk", chunk_payload, to=recipient_sid)
                    logging.info(f"Private file chunk forwarded: {sender_username} -> {recipient}")
                else:
                    self.sio.emit("error", {
                        "message": f"Recipient '{recipient}' not found"
                    }, to=sid)
            else:
                # Broadcast to all clients (public file)
                self.sio.emit("file_chunk", chunk_payload)
                logging.info(f"Public file chunk broadcasted from {sender_username}")
            
            # Check if transfer is complete
            if transfer_info["received_chunks"] >= transfer_info["total_chunks"]:
                logging.info(f"File transfer complete: {transfer_id}")
                # Transfer will be cleaned up when acknowledgment is received


if __name__ == "__main__":
    server = ChatServer()
    HOST = os.environ.get("CHAT_HOST", "localhost")
    PORT = int(os.environ.get("CHAT_PORT", 5000))
    logging.info(f"Starting server on {HOST}:{PORT}")
    eventlet.wsgi.server(eventlet.listen((HOST, PORT)), server.app)
