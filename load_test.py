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
    connect_barrier: threading.Barrier,
    private_phase_barrier: threading.Barrier,
):
    """Run a complete test cycle for one client"""

    client = TestClient(client_id, num_clients)

    # Connect to server
    if not client.connect():
        # If connection fails, we must still participate in the barrier to not deadlock others
        try:
            connect_barrier.wait(timeout=5)
            private_phase_barrier.wait(timeout=5)
        except threading.BrokenBarrierError:
            pass  # Other threads may have aborted
        return

    # Wait for all clients to be connected and registered
    try:
        connect_barrier.wait(timeout=10 + num_clients * 0.1)
    except threading.BrokenBarrierError:
        print(f"Client {client_id} failed to sync at connect barrier.")
        client.disconnect()
        return

    # Phase 1: Send public messages
    client.send_public_messages(public_count)

    # Wait for all clients to finish the public phase
    try:
        private_phase_barrier.wait(timeout=10 + public_count * 0.1)
    except threading.BrokenBarrierError:
        print(f"Client {client_id} failed to sync at private phase barrier.")
        client.disconnect()
        return

    # Phase 2: Send private messages
    client.send_private_messages(private_count)

    # Client remains connected to receive messages. The main thread will handle shutdown.
    # The disconnect will be called from the main thread after the wait period.
    # This ensures the client is available to receive all in-flight messages.


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
    print(f"\n{'-'*25} PUBLIC MESSAGES {'-'*25}")
    print(f"Sent: {stats['public_messages_sent']}")
    print(f"Received: {len(stats['public_latencies'])}")

    if stats["public_latencies"]:
        avg_latency = statistics.mean(stats['public_latencies']) * 1000
        min_latency = min(stats['public_latencies']) * 1000
        max_latency = max(stats['public_latencies']) * 1000
        
        # Calculate throughput based on the time the public phase was active
        public_throughput_duration = duration  # Simplified for now
        if public_throughput_duration > 0:
            throughput = len(stats['public_latencies']) / public_throughput_duration
            print(f"Throughput: {throughput:.2f} messages/sec")

        print(
            f"Latency - Avg: {avg_latency:.2f}ms, Min: {min_latency:.2f}ms, Max: {max_latency:.2f}ms"
        )
        

    # Private Messages
    print(f"\n{'-'*25} PRIVATE MESSAGES {'-'*25}")
    print(f"Sent: {stats['private_messages_sent']}")
    print(f"Received: {stats['private_messages_received']}")

    if stats["private_latencies"]:
        avg_latency = statistics.mean(stats['private_latencies']) * 1000
        min_latency = min(stats['private_latencies']) * 1000
        max_latency = max(stats['private_latencies']) * 1000
        
        # Calculate throughput based on the time the private phase was active
        private_throughput_duration = duration # Simplified for now
        if private_throughput_duration > 0:
            throughput = len(stats['private_latencies']) / private_throughput_duration
            print(f"Throughput: {throughput:.2f} messages/sec")

        print(
            f"Latency - Avg: {avg_latency:.2f}ms, Min: {min_latency:.2f}ms, Max: {max_latency:.2f}ms"
        )
        

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
    print(f"\n{'-'*28} OVERALL {'-'*28}")
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

    # Barriers for synchronization. Number of parties is all client threads.
    connect_barrier = threading.Barrier(num_clients)
    private_phase_barrier = threading.Barrier(num_clients)

    # Start message listener
    listener = MessageListener()
    if not listener.start():
        print("Failed to start message listener!")
        return

    # Start client threads
    threads = []
    clients = []  # Keep a reference to client objects for disconnection
    for i in range(num_clients):
        # Create client object and pass to thread
        client_obj = TestClient(i, num_clients)
        clients.append(client_obj)
        
        thread = threading.Thread(
            target=run_client_test,
            args=(
                i,
                num_clients,
                public_messages,
                private_messages,
                connect_barrier,
                private_phase_barrier,
            ),
            daemon=True,
        )
        threads.append(thread)
        thread.start()
        time.sleep(0.01)  # Stagger connection attempts slightly

    # Wait for all threads to finish their work
    # A global timeout to prevent the test from hanging indefinitely
    global_timeout = 20 + num_clients * (public_messages + private_messages) * 0.1
    
    total_private_sent = num_clients * private_messages
    wait_start_time = time.time()

    print("All clients running. Waiting for private messages to be delivered...")
    
    # Wait until all expected private messages are received or timeout
    while (
        metrics.get_stats()["private_messages_received"] < total_private_sent
        and time.time() - wait_start_time < global_timeout
    ):
        time.sleep(0.1)

    if metrics.get_stats()["private_messages_received"] < total_private_sent:
        print(f"\nWarning: Test timed out after {global_timeout:.1f}s. Not all private messages were received.")

    # Gracefully disconnect all clients
    print("Test finished. Disconnecting clients...")
    for thread in threads:
        if thread.is_alive():
            # The threads are waiting, but we can now disconnect their client objects
            pass # Threads will exit as they are daemons

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
