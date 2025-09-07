import socketio
from flask import Flask, request
import eventlet

class ChatServer():
    def __init__(self):
        self.sio = socketio.Server(cors_allowed_origins='*')
        self.app = Flask(__name__)
        self.app.wsgi_app = socketio.WSGIApp(self.sio, self.app.wsgi_app)
        self.clients = {}

        self.register_events()

    def register_events(self):

        @self.sio.event
        def connect(sid, environ):
            print(f"Client connected: {sid}")

        @self.sio.event
        def disconnect(sid):
            print(f"Client disconnected: {sid}")
            username = self.clients.pop(sid, None)
            if username:
                # Notify remaining clients that a user has left
                self.sio.emit('user_left', {'username': username})
                print(f"User left: {username}")


        @self.sio.event
        def register(sid, data):
            username = data.get('username')
            if username:
                self.clients[sid] = username
                # Notify other clients that a new user has joined
                self.sio.emit('user_joined', {'username': username}, skip_sid=sid)
                # Send the full user list to the newly registered client
                self.sio.emit('update_user_list', {'users': list(self.clients.values())}, to=sid)
                print(f"User registered: {username}")

        @self.sio.event
        def message(sid, data):
            username = self.clients.get(sid, 'Unknown')
            msg = data.get('message')
            if msg:
                self.sio.emit('message', {'username': username, 'message': msg})
                print(f"Message from {username}: {msg}")

if __name__ == '__main__':
    server = ChatServer()
    #server.app.run(port=5000, debug=True)
    eventlet.wsgi.server(eventlet.listen(('localhost', 5000)), server.app)