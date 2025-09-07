import sys
import os
import threading
import time
import socketio
from PySide6.QtCore import QObject, Signal, Slot, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine

class ChatClient(QObject):
    messageReceived = Signal(str, str)    # username, message
    usersUpdated = Signal('QVariant')     # list of usernames
    disconnected = Signal()               # Signal to notify QML of disconnection

    def __init__(self, url="http://localhost:5000"):
        super().__init__()
        self._url = url
        self._username = ""
        self._sio = socketio.Client()
        self._connected = False
        self._connecting = False
        self._users = []
        self._connect_lock = threading.Lock()
        self._setup_handlers()

    def _setup_handlers(self):
        @self._sio.event
        def connect():
            print("Connected")
            self._connected = True
            self._connecting = False
            if self._username:
                try:
                    self._sio.emit('register', {'username': self._username})
                except Exception:
                    pass

        @self._sio.event
        def disconnect():
            print("Disconnected from server")
            self._connected = False
            self._connecting = False
            self.disconnected.emit() # Notify the UI

        @self._sio.on('message')
        def on_message(data):
            username = data.get('username', 'Unknown')
            message = data.get('message', '')
            self.messageReceived.emit(username, message)

        # Replaced user_joined with update_user_list to sync with server
        @self._sio.on('update_user_list')
        def on_update_user_list(data):
            users = data.get('users', [])
            self._users = users
            self.usersUpdated.emit(self._users.copy())

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
        
        t = threading.Thread(target=_connect, daemon=True)
        t.start()

    def _emit_when_connected(self, event, data, timeout=5.0):
        # emit in background once connected (non-blocking for UI)
        def _worker():
            waited = 0.0
            interval = 0.05
            while waited < timeout:
                if self._connected:
                    try:
                        self._sio.emit(event, data)
                        return
                    except Exception as e:
                        print(f"Emit {event} failed:", e)
                        return
                time.sleep(interval)
                waited += interval
            print(f"Emit {event} timeout (not connected).")
        threading.Thread(target=_worker, daemon=True).start()

    @Slot(str)
    def register(self, username: str):
        self._username = username
        self._ensure_connected()
        # emit once connected (non-blocking)
        self._emit_when_connected('register', {'username': username})

    @Slot(str)
    def sendMessage(self, message: str):
        self._ensure_connected()
        self._emit_when_connected('message', {'message': message})

    @Slot()
    def disconnect(self):
        try:
            if self._connected:
                self._sio.disconnect()
        except Exception:
            pass

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