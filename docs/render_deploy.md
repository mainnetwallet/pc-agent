# Render Deployment Configuration

The FastAPI relay server can be deployed to Render.

## render.yaml

```yaml
services:
  - type: web
    name: pc-agent-relay
    env: python
    buildCommand: "pip install -r server/requirements.txt && pip install fastapi uvicorn python-multipart psycopg2-binary"
    startCommand: "cd server && uvicorn main:app --host 0.0.0.0 --port $PORT"
    envVars:
      - key: RELAY_API_KEY
        generateValue: true
      - key: DATABASE_URL
        fromDatabase:
          name: pc-agent-db
          property: connectionString
      - key: PORT
        value: 10000

databases:
  - name: pc-agent-db
    plan: free
```

## server/requirements.txt

```
fastapi==0.162.0
uvicorn==0.34.0
python-multipart==0.0.32
psycopg2-binary==2.9.9
```

## Environment on Render

Set these in the Render dashboard:

* `RELAY_API_KEY` — shared secret the Windows agent uses to connect.
* `DATABASE_URL` — provided automatically when you add a PostgreSQL database.
* `PORT` — Render sets this automatically.

## Connecting the Windows agent

On the Windows PC, set:

```env
RELAY_URL=https://pc-agent-relay.onrender.com
RELAY_API_KEY=<same as Render>
```

The agent connects outbound via authenticated WSS; no port forwarding required.

## Standalone mode

When `RELAY_URL` is empty, the Windows agent runs Telegram long-polling locally
and works without any cloud server.
