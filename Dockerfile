FROM python:3.10-slim

WORKDIR /
# Install only server dependencies to keep image small and avoid GUI libs
COPY server/requirements.txt server/requirements.txt
RUN pip install --no-cache-dir -r server/requirements.txt

COPY server/ server/
ENV CHAT_HOST=0.0.0.0 CHAT_PORT=5000
EXPOSE 5000

CMD ["python", "server/server.py"]