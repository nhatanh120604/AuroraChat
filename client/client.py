import sys
import os
import threading
import socketio
from PySide6.QtCore import QObject, Signal, Slot, QUrl, Property
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine


class ChatClient(QObject):
    messageReceived = Signal(str, str)  # username, message
    privateMessageReceived = Signal(str, str, str)  # sender, recipient, message
    usersUpdated = Signal("QVariant")  # list of usernames
    disconnected = Signal()  # Signal to notify QML of disconnection
    errorReceived = Signal(str)  # Notify UI about errors
    usernameChanged = Signal(str)  # Notify UI when username changes

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
            self.usersUpdated.emit([])
            self._set_username("")
            self.disconnected.emit()  # Notify the UI

        @self._sio.on("message")
        def on_message(data):
            username = data.get("username", "Unknown")
            message = data.get("message", "")
            self.messageReceived.emit(username, message)

        @self._sio.on("private_message_received")
        def on_private_message(data):
            sender = data.get("sender", "Unknown")
            recipient = data.get("recipient", "Unknown")
            message = data.get("message", "")
            self.privateMessageReceived.emit(sender, recipient, message)

        # Replaced user_joined with update_user_list to sync with server
        @self._sio.on("update_user_list")
        def on_update_user_list(data):
            users = data.get("users", [])
            self._users = users
            if self._desired_username and self._desired_username in users:
                self._set_username(self._desired_username)
            elif self._username and self._username not in users:
                self._set_username("")
            self.usersUpdated.emit(self._users.copy())

        @self._sio.on("error")
        def on_error(data):
            message = data.get("message", "An unknown error occurred.")
            self._notify_error(message)
            if self._desired_username and self._desired_username == self._username:
                # Preserve desired username for reconnection attempts but allow UI edits
                self._set_username("")
            lowered = message.lower()
            if self._desired_username and "username" in lowered:
                self._desired_username = ""

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

    def _notify_error(self, message: str):
        print("Error:", message)
        self.errorReceived.emit(message)

    def _set_username(self, value: str):
        value = value or ""
        if self._username != value:
            self._username = value
            self.usernameChanged.emit(self._username)

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
        text = (message or "").strip()
        if not text:
            self._notify_error("Cannot send an empty message.")
            return
        self._emit_when_connected("message", {"message": text})

    @Slot(str, str)
    def sendPrivateMessage(self, recipient: str, message: str):
        """Sends a message to a specific user."""
        recip = (recipient or "").strip()
        text = (message or "").strip()
        if not recip:
            self._notify_error("Recipient is required for private messages.")
            return
        if not text:
            self._notify_error("Cannot send an empty private message.")
            return
        payload = {"recipient": recip, "message": text}
        self._emit_when_connected("private_message", payload)

    @Slot()
    def disconnect(self):
        try:
            if self._connected:
                self._sio.disconnect()
        except Exception:
            pass
        finally:
            self._desired_username = ""

    def _get_username(self):
        return self._username

    username = Property(str, _get_username, notify=usernameChanged)


def main():
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
