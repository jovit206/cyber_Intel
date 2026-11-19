# AI Threat Assistant - standalone Docker application (`jovit206/chatbot`)

The AI Threat Assistant is the LLM chatbot of the AEGIS threat-intelligence platform (Jovit Chawla's NoSQL
course project). This folder packages it as a separate, runnable web application and Docker image. The chatbot
code in `backend/` and `public/` is copied **unchanged** from the original platform (SHA-256 verified, see
`MANIFEST.json`); only the launcher (`app.py`), the host page (`public/assistant.html`) and the Docker/config files
are new.

## What it does

A signed-in user chats with a cybersecurity assistant. Every question is classified and answered differently:

| Route | Example | Answered from |
|---|---|---|
| threat | "Analyse CVE-2024-8190", "Tell me about Lazarus Group" | the threat-intelligence records in your MongoDB, cited `[S1]`... |
| mitigation | "How do I mitigate CVE-2024-8190?" | matching records + clearly labelled general guidance |
| platform | "How does the assistant keep conversation history?" | a BM25 index over the packaged docs, source code, API routes and live MongoDB status |
| general security | "What is an IOC?" | the model's knowledge, labelled "not verified platform threat intelligence" |
| unrelated | questions outside cybersecurity and this platform | a fixed refusal |

Answers stream token by token; each user has private conversation history.

## Requirements outside the container

| Dependency | Required | Notes |
|---|---|---|
| **Ollama** + model **`llama3.2:3b`** | yes | runs on the host; the container calls `http://host.docker.internal:11434` |
| **MongoDB replica set** | yes | account registration/sign-in use multi-document transactions, which a standalone `mongod` does not support |
| Threat-intelligence collections (`events`, `attributes`, `threats`, `reports`, `passages` in `MONGO_DATABASE`) | for threat answers | **not included** - filled by the original platform's collectors (MISP feeds, Galaxies, reports, VirusTotal/ThreatFox). With an empty database threat questions answer "no evidence in the records" |
| Docker Desktop / Docker Engine | yes | |

Nothing else from the original project is needed: no files, APIs or containers of the original platform.

### Install Ollama and the model (host)

```powershell
winget install Ollama.Ollama          # or https://ollama.com/download
ollama pull llama3.2:3b               # ~2 GB
curl http://localhost:11434/api/tags  # lists llama3.2:3b
```

On Linux, make Ollama listen on the Docker bridge (`OLLAMA_HOST=0.0.0.0 ollama serve`); compose maps
`host.docker.internal` to the host gateway.

## Quick start

```powershell
docker pull jovit206/chatbot:latest
copy .env.example .env        # from this folder; set MONGO_URI for your MongoDB

# A) your own MongoDB replica set
docker compose up -d

# B) no MongoDB yet: start an empty single-node replica set together with the chatbot
docker compose --profile local-mongo up -d
```

Open **http://localhost:8000** (or `http://localhost:<CHATBOT_PORT>`), register an account on the sign-in page,
sign in, and chat. Without compose:

```powershell
docker run -d --name standalone-ai-chatbot -p 127.0.0.1:8000:8000 --env-file .env jovit206/chatbot:latest
```

## Configuration (environment variables)

| Variable | Default in image | Purpose |
|---|---|---|
| `MONGO_URI` | (compose: `mongodb://chatbot-mongo:27017/?replicaSet=rs0`) | MongoDB connection string - **required** |
| `MONGO_DATABASE` | `chapter07` | accounts, sessions, threat-intelligence collections |
| `LLM_PROVIDER` | `ollama` | or `openai-compatible` with `RAG_LLM_API_URL`, `RAG_LLM_MODEL`, `RAG_LLM_API_KEY` |
| `OLLAMA_BASE_URL` | `http://host.docker.internal:11434` | Ollama endpoint |
| `OLLAMA_CHAT_MODEL` | `llama3.2:3b` | model name |
| `OLLAMA_KEEP_ALIVE` | `30m` | keep the model loaded between questions |
| `LLM_TIMEOUT_SECONDS` | `180` | generation timeout |
| `FRONTEND_ORIGIN` | `http://localhost:8000` | origin of the web page |
| `SESSION_COOKIE_SECURE` | `false` | `true` when served over HTTPS |
| `CHATBOT_PORT` | `8000` | host port (compose only) |
| `LOCAL_AGENT_URL`, `LOCAL_AGENT_TOKEN`, `LOCAL_AGENT_ALLOWED_EMAILS` | unset | optional Windows security agent of the original project |

Conversation history is stored in the database **`rag_demo`** on the same cluster (name fixed in the original code).
No credentials are built into the image; `.env` is excluded by `.dockerignore`.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/` | chat page (redirects to `/account` when not signed in) |
| GET | `/account` | register / sign in |
| GET | `/health` | liveness (used by the Docker `HEALTHCHECK`) |
| GET | `/health/details` | MongoDB ping, Ollama reachability, model installed (503 if degraded) |
| POST | `/api/subscriptions/register`, `/login`, `/logout`; GET `/me` | accounts and session cookie |
| POST | `/api/ask/stream` | chat, newline-delimited JSON events `meta`, `token`..., `final` or `error` |
| POST | `/api/ask` | same pipeline, whole answer at once |
| GET/DELETE | `/api/ask/history`, GET `/api/ask/history/{conversation_id}` | conversation history |
| GET/POST | `/api/agent/...` | optional local security agent |

`/api/ask*` and `/api/agent*` return 401 without a session.

## How it works

See [ARCHITECTURE.md](ARCHITECTURE.md). In short: `public/ai-threat-assistant.js` -> `POST /api/ask/stream` ->
`backend/routes/chapter17_compat.py` (session check) -> `backend/services/assistant.py` (memory: last 6 messages;
routing; retrieval via `platform_knowledge.py` or `threat_retrieval.py`; prompt) -> `llm_provider.py` -> Ollama
`/api/chat` (streamed) -> answer checks (unknown citations / unverified CVEs removed; ungrounded threat answers
replaced by the records) -> saved to `rag_demo` after the final event.

## Building

```powershell
docker build -t jovit206/chatbot:latest .
```

Python 3.12 slim base, runs as a non-root user, image ~250 MB.

## Differences from the original platform and known limitations

- The original platform's `backend/main.py` (kept in `docs/reference/`) also loads ~25 unrelated dashboards. Its
  **monthly feature-quota middleware, manager read-only rule and request telemetry are not part of this app**;
  sign-in is enforced by the chat routes themselves. Accounts registered here get the original free plan record,
  but the quota is not metered.
- **Platform answers** draw on what is packaged (this README, ARCHITECTURE.md, `backend/knowledge/PLATFORM_OVERVIEW.md`,
  the code and `public/index.html`), not on the full project's lab folders.
- **Threat answers need your own CTI data**; none is shipped and none is generated.
- Inherited from the original code (not changed here): a short follow-up question (up to 8 words) keeps the
  previous route, so e.g. "How do I mitigate it?" after a threat question stays on the threat route; and with
  `llama3.2:3b` the citation validator often rejects the model's threat answer, in which case the matching records
  are shown instead (measured 5-6 of 8 runs in both the original platform and this image).
- The laptop security agent (`local_agent/` in the original project) is a Windows host program and is not in the image.
- A replica set is required; a standalone `mongod` fails at registration/sign-in ("could not be safely committed").

## Troubleshooting

| Symptom | Fix |
|---|---|
| Container exits at start with a MongoDB error | `MONGO_URI` wrong or MongoDB unreachable from the container |
| Registration/sign-in error "could not be safely committed" | MongoDB is not a replica set |
| `/health/details` shows `ollama.ok: false` | Ollama not running, or not reachable from Docker (Linux: `OLLAMA_HOST=0.0.0.0`) |
| `model_installed: false` | `ollama pull llama3.2:3b` |
| First answer slow (~10 s) | model loading; later answers are faster |
| Port already in use | set `CHATBOT_PORT` in `.env` |
