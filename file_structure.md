chat-app/
│
├── docker-compose.yml        # Compose file to orchestrate services
├── .env                      # Environment variables (DB creds, secrets)
│
├── services/
│   ├── auth-service/          # Handles login/register + JWT
│   │   ├── app.py             # Flask app (auth endpoints)
│   │   ├── models.py          # SQLAlchemy models (User)
│   │   ├── routes.py          # Login/Register routes
│   │   ├── requirements.txt
│   │   └── Dockerfile
│   │
│   ├── chat-service/          # Core chat (SocketIO + rooms)
│   │   ├── app.py             # Flask-SocketIO server
│   │   ├── events.py          # Socket.IO event handlers
│   │   ├── models.py          # SQLAlchemy models (Room, Message)
│   │   ├── utils.py           # Redis pub/sub helpers, room mgmt
│   │   ├── requirements.txt
│   │   └── Dockerfile
│   │
│   ├── file-service/          # File storage + RSA encryption
│   │   ├── app.py             # Flask REST API (upload/download)
│   │   ├── encryption.py      # RSA key gen + AES hybrid encryption
│   │   ├── storage/           # Saved encrypted files
│   │   ├── requirements.txt
│   │   └── Dockerfile
│   │
│   ├── user-service/          # User profile + active user tracking
│   │   ├── app.py             # Flask app (user info API)
│   │   ├── models.py          # SQLAlchemy (UserStatus, ActiveUser)
│   │   ├── routes.py
│   │   ├── requirements.txt
│   │   └── Dockerfile
│   │
│   ├── db/                    # MySQL database container
│   │   └── init.sql           # Schema initialization
│   │
│   └── redis/                 # Redis for pub/sub (optional, future week)
│
├── client/                    # PySide6 + QML client app
│   ├── main.py                # App entrypoint
│   ├── qml/                   # QML UI files
│   │   ├── LoginPage.qml
│   │   ├── ChatRoom.qml
│   │   └── FileUpload.qml
│   ├── services/              # Client service wrappers
│   │   ├── auth_client.py     # Talks to auth-service
│   │   ├── chat_client.py     # Handles SocketIO client
│   │   ├── file_client.py     # Upload/download API
│   │   └── user_client.py     # Fetch active users
│   ├── utils/
│   │   ├── encryption.py      # Client-side RSA
│   │   └── config.py
│   └── requirements.txt
│
└── docs/
    ├── architecture.md        # Explain microservices design
    ├── api-spec.md            # REST + Socket.IO event contracts
    └── schema.sql             # DB schema reference
