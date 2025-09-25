import os
import sys
from typing import Any, Callable, Dict, List, Optional, Tuple

# Ensure the project root is on the path so we can import the client package
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest
from PySide6.QtCore import QCoreApplication

from client.client import ChatClient


class FakeSocketIOClient:
    """Minimal stand-in for python-socketio's Client used for unit testing."""

    def __init__(self) -> None:
        self.handlers: Dict[str, Callable[..., Any]] = {}
        self.emitted: List[Tuple[str, Dict[str, Any]]] = []
        self.connected: bool = False

    # Decorator used as ``@client.event``
    def event(self, func: Callable[..., Any]) -> Callable[..., Any]:
        self.handlers[func.__name__] = func
        return func

    # Decorator used as ``@client.on('event_name')``
    def on(self, event_name: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            self.handlers[event_name] = func
            return func

        return decorator

    def emit(self, event: str, data: Dict[str, Any]) -> None:
        self.emitted.append((event, data))

    def connect(self, url: str) -> None:  # pragma: no cover - behaviour verified indirectly
        self.connected = True
        handler = self.handlers.get("connect")
        if handler:
            handler()

    def disconnect(self) -> None:
        self.connected = False
        handler = self.handlers.get("disconnect")
        if handler:
            handler()


@pytest.fixture(scope="session", autouse=True)
def qt_app() -> QCoreApplication:
    """Ensure a Qt application instance exists for signal delivery."""
    app = QCoreApplication.instance()
    if app is None:
        app = QCoreApplication([])
    return app


@pytest.fixture()
def chat_client(monkeypatch: pytest.MonkeyPatch) -> Tuple[ChatClient, FakeSocketIOClient]:
    fake_client = FakeSocketIOClient()
    monkeypatch.setattr("client.client.socketio.Client", lambda: fake_client)

    chat = ChatClient("http://localhost:5001")
    # Avoid background threads in the tests; connect synchronously against the fake client.
    chat._ensure_connected = lambda: fake_client.connect(chat._url)  # type: ignore[attr-defined]

    yield chat, fake_client

    chat.disconnect()


def test_register_sends_event_and_updates_username(chat_client: Tuple[ChatClient, FakeSocketIOClient]) -> None:
    chat, fake = chat_client
    name_updates: List[str] = []
    chat.usernameChanged.connect(name_updates.append)

    chat.register("alice")

    assert ("register", {"username": "alice"}) in fake.emitted
    assert "update_user_list" in fake.handlers

    fake.handlers["update_user_list"]({"users": ["alice"]})

    assert chat.username == "alice"
    assert name_updates[-1] == "alice"


def test_error_event_emits_signal_and_resets_desired_username(
    chat_client: Tuple[ChatClient, FakeSocketIOClient]
) -> None:
    chat, fake = chat_client
    errors: List[str] = []
    chat.errorReceived.connect(errors.append)

    chat.register("bob")
    fake.handlers["error"]({"message": "username already taken"})

    assert errors[-1] == "username already taken"
    assert chat.username == ""
    assert chat._desired_username == ""  # type: ignore[attr-defined]


def test_send_message_rejects_empty_payload(chat_client: Tuple[ChatClient, FakeSocketIOClient]) -> None:
    chat, fake = chat_client
    errors: List[str] = []
    chat.errorReceived.connect(errors.append)

    fake.emitted.clear()
    chat.sendMessage("   ")

    assert fake.emitted == []
    assert errors[-1] == "Cannot send an empty message."


def test_send_private_message_requires_recipient(chat_client: Tuple[ChatClient, FakeSocketIOClient]) -> None:
    chat, fake = chat_client
    errors: List[str] = []
    chat.errorReceived.connect(errors.append)

    fake.emitted.clear()
    chat.sendPrivateMessage("", "hello")

    assert fake.emitted == []
    assert errors[-1] == "Recipient is required for private messages."


def test_send_private_message_requires_body(chat_client: Tuple[ChatClient, FakeSocketIOClient]) -> None:
    chat, fake = chat_client
    errors: List[str] = []
    chat.errorReceived.connect(errors.append)

    fake.emitted.clear()
    chat.sendPrivateMessage("friend", "  ")

    assert fake.emitted == []
    assert errors[-1] == "Cannot send an empty private message."
