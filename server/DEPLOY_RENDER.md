# Deploying Chat Backend to Render

## Overview

The client now **automatically fetches the server's public key** from the `/public_key` endpoint when connecting. This means:
- ✅ Server can generate new keys on deployment
- ✅ Client automatically stays in sync
- ✅ No manual key distribution needed

## Quick Start

### Option 1: Using render.yaml (Recommended)

1. **Push your code to GitHub** (ensure `render.yaml` is in the repo root)

2. **Connect to Render:**
   - Go to https://dashboard.render.com
   - Click "New +" → "Blueprint"
   - Connect your GitHub repository
   - Render will automatically detect `render.yaml` and configure the service

3. **Deploy:**
   - Render will automatically build and deploy
   - Your service will be available at `https://your-service-name.onrender.com`

### Option 2: Manual Setup

1. **Create a new Web Service:**
   - Go to https://dashboard.render.com
   - Click "New +" → "Web Service"
   - Connect your GitHub repository

2. **Build Settings:**
   - **Environment:** `Python 3`
   - **Build Command:** `pip install -r server/requirements.txt`
   - **Start Command:** `python server/server.py`

3. **Environment Variables:**
   - `CHAT_HOST` = `0.0.0.0` (required for Render)
   - (Do NOT set `PORT` - Render provides it automatically)

## After Deployment

1. **Get your Render URL:**
   - It will be something like: `https://your-service-name.onrender.com`

2. **Test the server:**
   - Visit `https://your-service-name.onrender.com/health` - should return "ok"
   - Visit `https://your-service-name.onrender.com/public_key` - should return the RSA public key (PEM format)

3. **Update your client:**
   - Set environment variable: `CHAT_SERVER_URL=https://your-service-name.onrender.com`
   - Or create `client/.env` with:
     ```
     CHAT_SERVER_URL=https://your-service-name.onrender.com
     ```
   - **No need to manually copy public keys!** The client will fetch it automatically.

## How It Works

1. **Server startup:**
   - Checks for `server/private_key.pem`
   - If missing, generates a new 2048-bit RSA keypair
   - Saves private key to disk
   - Logs public key and exposes it via `/public_key` endpoint

2. **Client connection:**
   - Client reads `CHAT_SERVER_URL` from environment
   - Fetches public key from `{CHAT_SERVER_URL}/public_key`
   - Falls back to `client/public_key.pem` if fetch fails (for local dev)
   - Uses fetched key to encrypt session AES key

3. **Session key exchange:**
   - Client generates random AES key
   - Encrypts it with server's public key (fetched dynamically)
   - Sends encrypted key to server
   - Server decrypts with its private key
   - All messages encrypted with session AES key

## Important Notes

- **Free Tier:** Render free tier spins down after 15 minutes of inactivity. First request may be slow (~30 seconds).
- **HTTPS:** Render automatically provides HTTPS - use `https://` URLs, not `http://`
- **RSA Keys:** Server auto-generates on first boot. Public key available at `/public_key` endpoint
- **WebSockets:** Render supports WebSockets - your Socket.IO connections will work fine
- **No Manual Key Distribution:** Client automatically fetches the correct public key on every connection

## Troubleshooting

- **502 Bad Gateway:** Wait a few seconds if the service just started (free tier cold start)
- **Connection refused:** Ensure `CHAT_HOST=0.0.0.0` is set
- **Port errors:** Don't manually set `PORT` - Render provides it automatically
- **Key mismatch errors:** Ensure client has `CHAT_SERVER_URL` set correctly (client will fetch public key automatically)

