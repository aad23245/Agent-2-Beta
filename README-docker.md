# Docker Guide for Agent-2-Beta

This guide runs Agent-2-Beta as a Docker container.

## Build

```powershell
docker build -t agent2-beta .
```

## Run

```powershell
docker run --rm -p 1311:1311 -v "${PWD}\agent2-data:/app/data" agent2-beta
```

Open:

```text
http://localhost:1311
```

## API Keys

Agent-2-Beta stores keys in `agent2.db`. After the web UI opens, add your Gemini API key from the app settings or use the app command flow.

## If Port 1311 Is Busy

```powershell
docker run --rm -p 1312:1311 agent2-beta
```

Then open:

```text
http://localhost:1312
```

## Docker Workflow

```text
Agent-2-Beta source code
        -> Dockerfile
        -> Docker image
        -> Docker container
        -> Web UI at localhost:1311
```
