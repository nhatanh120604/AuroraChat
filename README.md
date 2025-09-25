# Chat Application README

This document provides a comprehensive overview of the current state of the chat application, including existing bugs, potential issues, and a roadmap for future development.

---

## 1. Current Bugs & Unprofessional Implementations

This section details issues that currently exist in the codebase, ranging from outright bugs to implementations that are not robust or scalable.

### Server-Side (`server/server.py`)

1.  **Username Not Unique**: The server does not enforce unique usernames. Two different clients can register with the exact same username. This breaks the private messaging feature, as the server will only send a message to the first client it finds with that name.
2.  **No Input Validation**: The server completely trusts client input. A malicious client could bypass the UI and send empty or improperly formatted data, which could cause unexpected behavior or crashes.
3.  **State Management is Not Thread-Safe**: The `self.clients` dictionary is modified directly in event handlers. While `eventlet` is single-threaded, this is not a safe practice for concurrent environments. If the server were ever run with multiple worker processes, this would lead to immediate race conditions. A `threading.Lock` should be used for all reads and writes to `self.clients`.
4.  **No Formal Logging**: The server uses `print()` for logging. For any real application, a proper logging library (like Python's `logging` module) should be configured to control log levels, format output, and direct logs to files or other services.
5.  **Hardcoded Configuration**: The server address and port (`localhost:5000`) are hardcoded. This should be loaded from environment variables or a configuration file to allow for flexible deployment.

### Client-Side (`client.py` & `Main.qml`)

1.  **Server Errors Are Ignored**: The server sends an `error` event when a private message fails (e.g., user not found), but the client (`client.py`) has no handler for this event. The user is never notified of the failure.
2.  **Inefficient Message Sending**: The `_emit_when_connected` function in `client.py` uses a `time.sleep()` loop to wait for a connection before sending a message. A more professional approach would be to queue the messages and send them immediately upon receiving the `connect` event from the server.
3.  **Duplicate State**: The client's username is stored in both the `_username` property in `client.py` and the `usernameField.text` property in `Main.qml`. The Python object should be the single source of truth.
4.  **No Visual Feedback for Send Failures**: If a message fails to send because the client is not connected (the `_emit_when_connected` timeout is reached), the message text is cleared, but the user receives no error. The message simply vanishes.

---

## 2. Potential Future Bugs & Issues

These are issues that will likely arise as the application grows in complexity or user base.

1.  **Scalability Collapse**: The server is a single instance and manages its state in memory. It **will not scale** across multiple processes or servers. If you were to run two instances of this server behind a load balancer, a user connected to Server A would not be able to see or message a user connected to Server B.
2.  **Security Vulnerabilities**:
    - **No Authentication**: Usernames are claimed without any form of authentication. Anyone can impersonate anyone else.
    - **No Encryption for Payloads**: The content of messages is sent in plain text.
    - **Denial-of-Service (DoS)**: A malicious client could flood the server with connection requests or messages, overwhelming the single-threaded `eventlet` server.
3.  **No Message Persistence**: If the server restarts, all chat history is lost forever. There is no database or persistent storage.
4.  **Unbounded Memory Usage**: The server stores the list of all connected clients in memory. With a very large number of clients, this could lead to high memory consumption. Chat history is also stored indefinitely in the client's UI, which could cause performance degradation over a long session.

---

## 3. Compulsory Next Functions & Implementations

These are the most critical features needed to make the application robust and usable.

1.  **Implement Unique Usernames**: The `register` function on the server must check if a username is already taken. If it is, it should reject the registration and send an error back to the client.
2.  **Add a Server-Side Database**: Integrate a database (like SQLite for simplicity or PostgreSQL for production) to store:
    - User accounts (for authentication).
    - Chat history, so that messages are not lost on restart.
3.  **Implement User Authentication**:
    - Replace the simple `register` flow with a proper login system (e.g., username and password).
    - The server should issue a token (like a JWT) upon successful login, which the client must then include with every subsequent request to authenticate itself.
4.  **Create a Client-Side Error Display**: Implement a handler for the `error` event from the server and display these messages to the user in the UI (e.g., in the chat view as a "System" message).
5.  **Message Persistence and History**: When a client connects, the server should send them a brief history of the most recent public messages.

---

## 4. Good-to-Have Next Functions

These features would significantly improve the user experience and overall quality of the application.

1.  **Dedicated Private Message UI**: Instead of using the `/msg` command, create a UI where clicking a user opens a separate, dedicated chat tab or window for that private conversation.
2.  **"User is Typing..." Indicator**: Show when a user is typing a message in a private or public chat, which makes the conversation feel more alive.
3.  **Read Receipts**: Show when a user has seen a private message (e.g., with a double-check icon).
4.  **Support for Multiple Chat Rooms**: Allow users to create or join different public chat rooms instead of having a single global one.
5.  **Configuration Management**: Use a library like `python-dotenv` to manage server configuration (ports, database URLs, secret keys) through environment variables instead of hardcoding them.

