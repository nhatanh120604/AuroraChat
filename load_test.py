import threading
import time
import socketio
import statistics
import argparse

# --- Configuration ---
SERVER_URL = "http://localhost:5000"

# --- Shared Data for Metrics ---
# These will be accessed by multiple threads
latencies = []
private_message_latencies = []
successful_connections = 0
failed_connections = 0
messages_sent = 0
private_messages_sent = 0
private_messages_received = 0
lock = threading.Lock()


def run_chat_client(
    client_id,
    messages_to_send,
    private_messages_to_send,
    listener_ready_event,
    num_clients,
):
    """Simulates a single client's behavior: connect, register, send messages."""
    global successful_connections, failed_connections, messages_sent, private_messages_sent, private_messages_received

    sio = socketio.Client()

    # Handler for private messages received by this client
    @sio.on("private_message_received")
    def on_private_message_received(data):
        if "timestamp" in data:
            latency = time.time() - data["timestamp"]
            with lock:
                private_message_latencies.append(latency)
                private_messages_received += 1

    try:
        sio.connect(SERVER_URL)
        with lock:
            successful_connections += 1
    except Exception as e:
        with lock:
            failed_connections += 1
        # print(f"Client {client_id} connection failed: {e}")
        return

    # Register with a unique username
    sio.emit("register", {"username": f"test_user_{client_id}"})

    # Wait until the listener client is ready to ensure we measure all messages
    listener_ready_event.wait()

    # Send public messages with timestamps
    for i in range(messages_to_send):
        payload = {
            "message": f"Hello from {client_id}, message {i}",
            "timestamp": time.time(),
        }
        sio.emit("message", payload)
        with lock:
            messages_sent += 1
        time.sleep(0.05)  # Stagger messages slightly

    # Send private messages with timestamps
    for i in range(private_messages_to_send):
        # Send to a different client (cycling through available clients)
        target_client = (client_id + 1 + i) % num_clients
        if target_client != client_id:  # Don't send to self
            payload = {
                "recipient": f"test_user_{target_client}",
                "message": f"Private from {client_id}, PM {i}",
                "timestamp": time.time(),
            }
            sio.emit("private_message", payload)
            with lock:
                private_messages_sent += 1
            time.sleep(0.05)  # Stagger messages slightly

    # Keep client alive a bit longer to receive any pending private messages
    time.sleep(2)
    sio.disconnect()


def listener_client(
    num_total_messages, num_total_private_messages, listener_ready_event
):
    """A dedicated client to listen for all messages and calculate latency."""
    global private_messages_received
    sio = socketio.Client()

    @sio.on("message")
    def on_message(data):
        if "timestamp" in data:
            latency = time.time() - data["timestamp"]
            with lock:
                latencies.append(latency)

    @sio.on("private_message_received")
    def on_private_message(data):
        if "timestamp" in data:
            latency = time.time() - data["timestamp"]
            with lock:
                private_message_latencies.append(latency)
                private_messages_received += 1

    try:
        sio.connect(SERVER_URL)
        sio.emit("register", {"username": "listener"})
        listener_ready_event.set()  # Signal that the listener is ready
    except Exception as e:
        print(f"Listener client failed to connect: {e}")
        listener_ready_event.set()  # Unblock other threads even if listener fails
        return

    # Keep the listener alive until all expected messages are received or timeout
    start_time = time.time()
    timeout = 60  # 60-second timeout
    total_expected = num_total_messages + num_total_private_messages
    while (
        len(latencies) + len(private_message_latencies)
    ) < total_expected and time.time() - start_time < timeout:
        sio.sleep(0.1)

    sio.disconnect()


def main(num_clients, messages_per_client, private_messages_per_client):
    threads = []
    listener_ready_event = threading.Event()
    total_messages = num_clients * messages_per_client
    total_private_messages = num_clients * private_messages_per_client

    print(f"Starting load test with {num_clients} clients:")
    print(f"  - {messages_per_client} public messages each")
    print(f"  - {private_messages_per_client} private messages each")
    start_time = time.time()

    # Start the listener client
    listener_thread = threading.Thread(
        target=listener_client,
        args=(total_messages, total_private_messages, listener_ready_event),
    )
    threads.append(listener_thread)
    listener_thread.start()

    # Start the sender clients
    for i in range(num_clients):
        thread = threading.Thread(
            target=run_chat_client,
            args=(
                i,
                messages_per_client,
                private_messages_per_client,
                listener_ready_event,
                num_clients,
            ),
        )
        threads.append(thread)
        thread.start()
        time.sleep(0.01)  # Stagger connections slightly

    # Wait for all threads to complete
    for thread in threads:
        thread.join()

    end_time = time.time()
    duration = end_time - start_time

    # --- Print Results ---
    print("\n--- Load Test Results ---")
    print(f"Test duration: {duration:.2f} seconds")
    print(f"Successful connections: {successful_connections}/{num_clients}")
    print(f"Failed connections: {failed_connections}")

    # Public messages results
    print(f"\n--- Public Messages ---")
    print(f"Total public messages sent: {messages_sent}")
    print(f"Total public messages received: {len(latencies)}")

    if latencies:
        avg_latency = statistics.mean(latencies) * 1000
        max_latency = max(latencies) * 1000
        min_latency = min(latencies) * 1000
        throughput = len(latencies) / duration if duration > 0 else 0

        print(f"Public Message Latency (ms):")
        print(f"  - Average: {avg_latency:.2f} ms")
        print(f"  - Min:     {min_latency:.2f} ms")
        print(f"  - Max:     {max_latency:.2f} ms")
        print(f"Public Message Throughput: {throughput:.2f} messages/sec")
    else:
        print("No public message latencies recorded.")

    # Private messages results
    print(f"\n--- Private Messages ---")
    print(f"Total private messages sent: {private_messages_sent}")
    print(f"Total private messages received: {private_messages_received}")

    if private_message_latencies:
        avg_pm_latency = statistics.mean(private_message_latencies) * 1000
        max_pm_latency = max(private_message_latencies) * 1000
        min_pm_latency = min(private_message_latencies) * 1000
        pm_throughput = len(private_message_latencies) / duration if duration > 0 else 0

        print(f"Private Message Latency (ms):")
        print(f"  - Average: {avg_pm_latency:.2f} ms")
        print(f"  - Min:     {min_pm_latency:.2f} ms")
        print(f"  - Max:     {max_pm_latency:.2f} ms")
        print(f"Private Message Throughput: {pm_throughput:.2f} messages/sec")
    else:
        print("No private message latencies recorded.")

    # Overall performance
    total_messages_all = len(latencies) + len(private_message_latencies)
    overall_throughput = total_messages_all / duration if duration > 0 else 0
    print(f"\n--- Overall Performance ---")
    print(f"Total messages processed: {total_messages_all}")
    print(f"Overall throughput: {overall_throughput:.2f} messages/sec")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Chat Server Load Tester with Private Messaging"
    )
    parser.add_argument(
        "-c",
        "--clients",
        type=int,
        default=10,
        help="Number of concurrent clients to simulate.",
    )
    parser.add_argument(
        "-m",
        "--messages",
        type=int,
        default=5,
        help="Number of public messages each client will send.",
    )
    parser.add_argument(
        "-p",
        "--private-messages",
        type=int,
        default=3,
        help="Number of private messages each client will send.",
    )
    args = parser.parse_args()
    print(f"Parsed arguments: {args}")
    main(args.clients, args.messages, args.private_messages)
