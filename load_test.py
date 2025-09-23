"""
Professional Load Test for Chat Server with Private Messaging

This script tests both public and private messaging capabilities of the chat server.
Uses a synchronized approach to ensure proper message delivery testing.
"""

import threading
import time
import socketio
import statistics
import argparse
from typing import List, Dict

# Configuration
SERVER_URL = "http://localhost:5000"


# Metrics (thread-safe)
class Metrics:
    def __init__(self):
        self.lock = threading.Lock()
        self.public_latencies: List[float] = []
        self.private_latencies: List[float] = []
        self.successful_connections = 0
        self.failed_connections = 0
        self.public_messages_sent = 0
        self.private_messages_sent = 0
        self.private_messages_received = 0

    def add_public_latency(self, latency: float):
        with self.lock:
            self.public_latencies.append(latency)

    def add_private_latency(self, latency: float):
        with self.lock:
            self.private_latencies.append(latency)
            self.private_messages_received += 1

    def increment_connection_success(self):
        with self.lock:
            self.successful_connections += 1

    def increment_connection_failure(self):
        with self.lock:
            self.failed_connections += 1

    def increment_public_sent(self):
        with self.lock:
            self.public_messages_sent += 1

    def increment_private_sent(self):
        with self.lock:
            self.private_messages_sent += 1

    def get_stats(self) -> Dict:
        with self.lock:
            return {
                "public_latencies": self.public_latencies.copy(),
                "private_latencies": self.private_latencies.copy(),
                "successful_connections": self.successful_connections,
                "failed_connections": self.failed_connections,
                "public_messages_sent": self.public_messages_sent,
                "private_messages_sent": self.private_messages_sent,
                "private_messages_received": self.private_messages_received,
            }


# Global metrics instance
metrics = Metrics()


class TestClient:
    """Individual test client for load testing"""

    def __init__(self, client_id: int, num_clients: int):
        self.client_id = client_id
        self.num_clients = num_clients
        self.username = f"test_user_{client_id}"
        self.sio = socketio.Client()
        self._setup_handlers()

    def _setup_handlers(self):
        """Set up Socket.IO event handlers"""

        @self.sio.on("private_message_received")
        def on_private_message(data):
            if "timestamp" in data:
                latency = time.time() - data["timestamp"]
                metrics.add_private_latency(latency)

    def connect(self) -> bool:
        """Connect to server and register"""
        try:
            self.sio.connect(SERVER_URL)
            self.sio.emit("register", {"username": self.username})
            metrics.increment_connection_success()
            return True
        except Exception:
            metrics.increment_connection_failure()
            return False

    def send_public_messages(self, count: int):
        """Send public messages"""
        for i in range(count):
            payload = {
                "message": f"Public message {i} from {self.username}",
                "timestamp": time.time(),
            }
            self.sio.emit("message", payload)
            metrics.increment_public_sent()
            time.sleep(0.01)  # Small delay to prevent overwhelming

    def send_private_messages(self, count: int):
        """Send private messages to other clients"""
        for i in range(count):
            if self.num_clients <= 1:
                continue

            # Target different clients in round-robin fashion
            target_id = (self.client_id + 1 + i) % self.num_clients
            if target_id == self.client_id:  # Skip self
                target_id = (target_id + 1) % self.num_clients

            payload = {
                "recipient": f"test_user_{target_id}",
                "message": f"Private message {i} from {self.username}",
                "timestamp": time.time(),
            }
            self.sio.emit("private_message", payload)
            metrics.increment_private_sent()
            time.sleep(0.01)

    def disconnect(self):
        """Disconnect from server"""
        try:
            self.sio.disconnect()
        except Exception:
            pass


def run_client_test(
    client_id: int,
    num_clients: int,
    public_count: int,
    private_count: int,
    start_event: threading.Event,
    private_phase_event: threading.Event,
):
    """Run a complete test cycle for one client"""

    client = TestClient(client_id, num_clients)

    # Connect to server
    if not client.connect():
        return

    # Wait for all clients to be ready
    start_event.wait()

    # Phase 1: Send public messages
    client.send_public_messages(public_count)

    # Wait for private messaging phase
    private_phase_event.wait()

    # Phase 2: Send private messages
    client.send_private_messages(private_count)

    # Stay connected to receive private messages
    time.sleep(3)

    # Disconnect
    client.disconnect()


class MessageListener:
    """Dedicated listener for public messages"""

    def __init__(self):
        self.sio = socketio.Client()
        self._setup_handlers()

    def _setup_handlers(self):
        @self.sio.on("message")
        def on_message(data):
            if "timestamp" in data:
                latency = time.time() - data["timestamp"]
                metrics.add_public_latency(latency)

    def start(self):
        """Start listening"""
        try:
            self.sio.connect(SERVER_URL)
            self.sio.emit("register", {"username": "message_listener"})
            return True
        except Exception:
            return False

    def stop(self):
        """Stop listening"""
        try:
            self.sio.disconnect()
        except Exception:
            pass


def print_results(
    duration: float, num_clients: int, public_per_client: int, private_per_client: int
):
    """Print comprehensive test results"""
    stats = metrics.get_stats()

    print(f"\n{'='*60}")
    print("LOAD TEST RESULTS")
    print(f"{'='*60}")
    print(f"Test Duration: {duration:.2f} seconds")
    print(
        f"Clients: {stats['successful_connections']}/{num_clients} connected successfully"
    )
    if stats["failed_connections"] > 0:
        print(f"Failed Connections: {stats['failed_connections']}")

    # Public Messages
    print(f"\n{'-'*30} PUBLIC MESSAGES {'-'*30}")
    print(f"Sent: {stats['public_messages_sent']}")
    print(f"Received: {len(stats['public_latencies'])}")

    if stats["public_latencies"]:
        avg_latency = statistics.mean(stats["public_latencies"]) * 1000
        min_latency = min(stats["public_latencies"]) * 1000
        max_latency = max(stats["public_latencies"]) * 1000
        throughput = len(stats["public_latencies"]) / duration

        print(
            f"Latency - Avg: {avg_latency:.2f}ms, Min: {min_latency:.2f}ms, Max: {max_latency:.2f}ms"
        )
        print(f"Throughput: {throughput:.2f} messages/sec")

    # Private Messages
    print(f"\n{'-'*30} PRIVATE MESSAGES {'-'*30}")
    print(f"Sent: {stats['private_messages_sent']}")
    print(f"Received: {stats['private_messages_received']}")

    if stats["private_latencies"]:
        avg_latency = statistics.mean(stats["private_latencies"]) * 1000
        min_latency = min(stats["private_latencies"]) * 1000
        max_latency = max(stats["private_latencies"]) * 1000
        throughput = len(stats["private_latencies"]) / duration

        print(
            f"Latency - Avg: {avg_latency:.2f}ms, Min: {min_latency:.2f}ms, Max: {max_latency:.2f}ms"
        )
        print(f"Throughput: {throughput:.2f} messages/sec")

        # Delivery rate
        delivery_rate = (
            (stats["private_messages_received"] / stats["private_messages_sent"]) * 100
            if stats["private_messages_sent"] > 0
            else 0
        )
        print(f"Delivery Rate: {delivery_rate:.1f}%")
    else:
        print("No private messages received")

    # Overall
    total_received = len(stats["public_latencies"]) + len(stats["private_latencies"])
    overall_throughput = total_received / duration if duration > 0 else 0
    print(f"\n{'-'*30} OVERALL {'-'*30}")
    print(f"Total Messages Processed: {total_received}")
    print(f"Overall Throughput: {overall_throughput:.2f} messages/sec")


def main(num_clients: int, public_messages: int, private_messages: int):
    """Main test execution"""

    print(f"Starting Load Test:")
    print(f"  - {num_clients} clients")
    print(f"  - {public_messages} public messages per client")
    print(f"  - {private_messages} private messages per client")
    print(f"  - Server: {SERVER_URL}")

    start_time = time.time()

    # Events for synchronization
    start_event = threading.Event()
    private_phase_event = threading.Event()

    # Start message listener
    listener = MessageListener()
    if not listener.start():
        print("Failed to start message listener!")
        return

    # Start client threads
    threads = []
    for i in range(num_clients):
        thread = threading.Thread(
            target=run_client_test,
            args=(
                i,
                num_clients,
                public_messages,
                private_messages,
                start_event,
                private_phase_event,
            ),
        )
        threads.append(thread)
        thread.start()
        time.sleep(0.01)  # Stagger connections

    # Wait a moment for all clients to connect
    time.sleep(2)

    # Start the test
    print("Starting public message phase...")
    start_event.set()

    # Wait for public messages to complete
    time.sleep(2)

    # Start private message phase
    print("Starting private message phase...")
    private_phase_event.set()

    # Wait for all threads to complete
    for thread in threads:
        thread.join()

    # Stop listener
    listener.stop()

    end_time = time.time()
    duration = end_time - start_time

    # Print results
    print_results(duration, num_clients, public_messages, private_messages)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Professional Chat Server Load Tester")
    parser.add_argument(
        "-c",
        "--clients",
        type=int,
        default=5,
        help="Number of concurrent clients (default: 5)",
    )
    parser.add_argument(
        "-m",
        "--messages",
        type=int,
        default=3,
        help="Public messages per client (default: 3)",
    )
    parser.add_argument(
        "-p",
        "--private-messages",
        type=int,
        default=2,
        help="Private messages per client (default: 2)",
    )

    args = parser.parse_args()

    if args.clients < 1:
        print("Error: Must have at least 1 client")
        exit(1)

    main(args.clients, args.messages, args.private_messages)
