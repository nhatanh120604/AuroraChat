import sys
import os

# Add the project root to the Python path to allow for module imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import unittest
import threading
import time
import socketio
import eventlet
from server.server import ChatServer


class TestChatServer(unittest.TestCase):

    server_thread = None
    server = None
    sio_client = None

    @classmethod
    def setUpClass(cls):
        """Set up the server once for all tests."""
        cls.server = ChatServer(test=True)
        # Run the server in a background thread
        cls.server_thread = threading.Thread(
            target=eventlet.wsgi.server,
            args=(eventlet.listen(("", 5001)), cls.server.app),
            daemon=True,
        )
        cls.server_thread.start()
        # Give the server a moment to start
        time.sleep(0.1)

    @classmethod
    def tearDownClass(cls):
        """This might not be called if tests hang, but it's good practice."""
        # The server thread is a daemon, so it will exit with the main thread.
        # No explicit stop needed for eventlet in this setup.
        pass

    def setUp(self):
        """Set up a new client for each test."""
        self.sio_client = socketio.Client()
        self.received_events = {}
        self.event_received = threading.Event()

        # Generic event handler
        def on_event(event, *args):
            self.received_events.setdefault(event, []).append(args)
            self.event_received.set()

        self.sio_client.on("*", on_event)
        self.sio_client.connect("http://localhost:5001")

    def tearDown(self):
        """Disconnect the client after each test."""
        if self.sio_client.connected:
            self.sio_client.disconnect()
        self.received_events.clear()
        self.event_received.clear()

    def wait_for_event(self, event_name, timeout=1.0):
        """Helper to wait for a specific event to be received."""
        start_time = time.time()
        while time.time() - start_time < timeout:
            if event_name in self.received_events:
                return self.received_events[event_name]
            time.sleep(0.01)
        return None

    def test_01_register_user(self):
        """Test if a user can register successfully."""
        self.sio_client.emit("register", {"username": "tester"})
        user_list_events = self.wait_for_event("update_user_list")
        self.assertIsNotNone(
            user_list_events, "Did not receive update_user_list event."
        )
        self.assertEqual(len(user_list_events), 1)
        self.assertIn("tester", user_list_events[0][0]["users"])

    def test_02_unique_username_enforced(self):
        """Test that the server rejects a duplicate username."""
        # Client 1 registers a username
        client1 = socketio.Client()
        client1.connect("http://localhost:5001")
        client1.emit("register", {"username": "duplicate_user"})
        time.sleep(0.1)  # Allow server to process

        # Client 2 attempts to register the same username
        error_event = threading.Event()
        error_data = None

        @self.sio_client.on("error")
        def on_error(data):
            nonlocal error_data
            error_data = data
            error_event.set()

        self.sio_client.emit("register", {"username": "duplicate_user"})

        # Wait for the error event
        event_was_set = error_event.wait(timeout=1.0)

        self.assertTrue(
            event_was_set, "Did not receive error event for duplicate username."
        )
        self.assertIn("already taken", error_data["message"])

        client1.disconnect()

    def test_03_invalid_username(self):
        """Test that the server rejects an empty or whitespace username."""
        self.sio_client.emit("register", {"username": "   "})
        error_events = self.wait_for_event("error")
        self.assertIsNotNone(
            error_events, "Did not receive error for whitespace username."
        )
        self.assertIn("valid username is required", error_events[0][0]["message"])

    def test_04_empty_message_is_ignored(self):
        """Test that the server ignores empty messages."""
        # Register a listener client
        listener = socketio.Client()
        listener.connect("http://localhost:5001")

        message_received = threading.Event()

        @listener.on("message")
        def on_message(data):
            message_received.set()

        # Register our main test client
        self.sio_client.emit("register", {"username": "sender"})
        time.sleep(0.1)

        # Send an empty message
        self.sio_client.emit("message", {"message": ""})

        # If a message event is received, the test fails. We expect it to time out.
        event_was_set = message_received.wait(timeout=0.5)
        self.assertFalse(
            event_was_set, "Server should not have broadcast an empty message."
        )

        listener.disconnect()

    def test_05_user_list_updates_on_disconnect(self):
        """Test that the user list is updated when a client disconnects."""
        # Register the main client
        self.sio_client.emit("register", {"username": "staying_user"})
        self.wait_for_event("update_user_list")  # Wait for initial list

        # Connect and register a second client that will disconnect
        temp_client = socketio.Client()
        temp_client.connect("http://localhost:5001")
        temp_client.emit("register", {"username": "leaving_user"})

        # Wait for the main client to get the updated list
        self.received_events.pop("update_user_list", None)
        user_list_events = self.wait_for_event("update_user_list", timeout=2.0)
        self.assertIsNotNone(user_list_events)
        # The list should now contain both users
        self.assertIn("staying_user", user_list_events[-1][0]["users"])
        self.assertIn("leaving_user", user_list_events[-1][0]["users"])

        # Disconnect the temporary client
        temp_client.disconnect()

        # Wait for the main client to get the final list
        self.received_events.pop("update_user_list", None)
        final_list_events = self.wait_for_event("update_user_list", timeout=2.0)
        self.assertIsNotNone(
            final_list_events, "Did not receive final user list update."
        )
        # The list should now only contain the original user
        self.assertIn("staying_user", final_list_events[-1][0]["users"])
        self.assertNotIn("leaving_user", final_list_events[-1][0]["users"])


if __name__ == "__main__":
    unittest.main()
