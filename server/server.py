import socketio
from flask import Flask
import eventlet
import logging
import os
import threading
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)


class ChatServer:
    def __init__(self, test=False):
        self.sio = socketio.Server(cors_allowed_origins="*")
        self.app = Flask(__name__)
        self.app.wsgi_app = socketio.WSGIApp(self.sio, self.app.wsgi_app)
        self.clients = {}
        self.test = test
        self.lock = threading.Lock()  # Lock for thread-safe operations on clients dict

        self.register_events()

    def register_events(self):

        @self.sio.event
        def connect(sid, environ):
            logging.info(f"Client connected: {sid}")

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
                # Notify all clients (including the new one) with the updated user list
                self.sio.emit(
                    "update_user_list", {"users": list(self.clients.values())}
                )
                logging.info(f"User registered: {username} with SID: {sid}")

        @self.sio.event
        def message(sid, data):
            """Handle incoming messages from a client and broadcast them."""
            with self.lock:
                sender_username = self.clients.get(sid, "Unknown")

            message_text = data.get("message")

            # Input validation
            if (
                not message_text
                or not isinstance(message_text, str)
                or not message_text.strip()
            ):
                logging.warning(
                    f"Empty message from {sender_username} ({sid}) ignored."
                )
                return

            # The data from the test client is the entire payload.
            # We need to add the sender's username and broadcast it.
            if data and "message" in data:
                # Prepare the payload to be sent to all clients
                broadcast_data = {
                    "username": sender_username,
                    "message": message_text,
                    "timestamp": data.get(
                        "timestamp"
                    ),  # Forward the timestamp for latency calculation
                }
                # Emit to all clients. By removing `skip_sid`, the sender will also receive their own message.
                self.sio.emit("message", broadcast_data)

        @self.sio.event
        def private_message(sid, data):
            """Handle private messages between users."""
            with self.lock:
                sender_username = self.clients.get(sid, "Unknown")

            recipient_username = data.get("recipient")
            message = data.get("message")

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
            if not message or not isinstance(message, str) or not message.strip():
                self.sio.emit(
                    "error", {"message": "Cannot send an empty message."}, to=sid
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
                    # Send private message to recipient only
                    payload = {
                        "sender": sender_username,
                        "recipient": recipient_username,
                        "message": message,
                    }
                    # Forward timestamp if present for latency calculation
                    if "timestamp" in data:
                        payload["timestamp"] = data["timestamp"]

                    self.sio.emit("private_message_received", payload, to=recipient_sid)
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


if __name__ == "__main__":
    server = ChatServer()
    HOST = os.environ.get("CHAT_HOST", "localhost")
    PORT = int(os.environ.get("CHAT_PORT", 5000))
    logging.info(f"Starting server on {HOST}:{PORT}")
    eventlet.wsgi.server(eventlet.listen((HOST, PORT)), server.app)
