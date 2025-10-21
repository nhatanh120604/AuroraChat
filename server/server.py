import socketio
from flask import Flask
import eventlet
import logging
import os
import threading
from dotenv import load_dotenv


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
load_dotenv()

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


if __name__ == "__main__":
    server = ChatServer()
    HOST = os.environ.get("CHAT_HOST", "localhost")
    PORT = int(os.environ.get("CHAT_PORT", 5000))
    logging.info(f"Starting server on {HOST}:{PORT}")
    eventlet.wsgi.server(eventlet.listen((HOST, PORT)), server.app)
