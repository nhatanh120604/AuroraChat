FROM python:3.10-slim

# Install Qt dependencies for PySide6
RUN apt-get update && apt-get install -y \
    libxcb-xinerama0 \
    libxcb-cursor0 \
    libxkbcommon-x11-0 \
    libxkbcommon-x11-dev \
    libxkbcommon-dev \
    libxkbcommon-x11-0 \
    libxcomposite1 \
    libxrandr2 \
    libasound2 \
    libfreetype6 \
    libxss1 \
    libgtk-3-0 \
    fonts-liberation \
    libappindicator3-1 \
    libnss3 \
    xdg-utils \
    dbus \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies for client
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r /app/requirements.txt

# Copy client application (build context is project root)
COPY . .

# Set display environment for GUI (will be overridden in docker-compose if needed)
ENV DISPLAY=:0
ENV QT_QPA_PLATFORM=xcb
ENV QT_X11_NO_MITSHM=1

# Change to client directory and run
# WORKDIR /app/client

# Default command
CMD ["python", "client.py"]

