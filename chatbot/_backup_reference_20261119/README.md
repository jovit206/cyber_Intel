# AI Threat Assistant - reference copy

> **Reference only.** This folder is a copy of the AI Threat Assistant source for reading, learning and future
> development. It is **not used by the application**: the backend imports `backend/...`, the web server serves
> `public/...`, the Docker image copies only named folders (not `chatbot/`), and the assistant's own knowledge
> index does not read this folder. Changing files here changes nothing in the running app. The originals are listed
> in [MANIFEST.json](MANIFEST.json) (original path -> copy path, SHA-256; every copy is byte-identical).
>
> Because the files were re-organised into `backend/config/`, `frontend/js/` etc., the Python imports
> (`from backend.services...`) do **not** resolve inside this folder - it is not meant to be run. A runnable
> packaging of the same code (original import layout + a small launcher) is `G:\Projects\AI_Threat_Assistant_Standalone.zip`.

See [ARCHITECTURE.md](ARCHITECTURE.md) for the request flow in detail.

## What it does

The AI Threat Assistant is the chatbot of the AEGIS threat-intelligence platform. A signed-in user asks a
question; it is classified and answered differently depending on its type:

| Route | Example | Answered from |
|---|---|---|
| `threat` | "Analyse CVE-2024-8190", "Tell me about Lazarus Group" | the platform's MongoDB threat records, cited `[S1]` ... |
| `mitigation` | "How do I mitigate CVE-2024-8190?" | matching records + labelled general guidance |
| `platform` | "How does Network Reliability work?" | project knowledge: docs, source code, API routes, live MongoDB status |
| `general_security` | "What is an IOC?" | model knowledge, labelled "not verified platform threat intelligence" |
| local scan | "Scan my laptop for threats" | the optional Windows security agent (read-only checks) |
| `unknown` | anything unrelated | fixed refusal text |

## Which LLM it uses

- **Model:** `llama3.2:3b` (Meta Llama 3.2, 3 billion parameters), served by **Ollama** on the Windows host.
- Configured in `backend/services/llm_provider.py` (`get_llm_configuration`): `LLM_PROVIDER=ollama`,
  `OLLAMA_BASE_URL` (the deployed container uses `http://host.docker.internal:11434`), `OLLAMA_CHAT_MODEL`
  (default `llama3.2:3b`), `OLLAMA_KEEP_ALIVE=30m`, `LLM_TIMEOUT_SECONDS`.
- An OpenAI-compatible API or NVIDIA NIM can be selected with `LLM_PROVIDER` / `RAG_LLM_*` / `NVIDIA_NIM_*`, but
  the deployment does not use them.

## How Ollama works

Ollama is a local model server: it stores open-weight models (`ollama pull llama3.2:3b`) and serves them over HTTP
on port 11434, on the GPU when one is available. `stream_chat_answer` sends `POST /api/chat` with the system
prompt, the recent conversation turns and the question, `stream: true`, `keep_alive` and an output-token limit, and
yields the reply chunk by chunk. Nothing is sent to an external AI service.

## Backend and frontend architecture

- **Backend (FastAPI):** `backend/routes/chapter17_compat.py` exposes `/api/ask/stream` (NDJSON events `meta`,
  `token`..., `final` | `error`), `/api/ask` and `/api/ask/history*`; every route requires the session cookie
  (`Depends(_session_user)` from `routes/subscriptions.py`). `backend/services/assistant.py` does routing,
  retrieval, prompting, streaming, answer checks and saving. In the real application `backend/main.py`
  (copied as `backend/config/main.py`) creates the MongoDB connection (`app.state.database`), warms the knowledge
  index, and its middleware enforces sign-in and the monthly feature quota.
- **Frontend (vanilla JS):** `frontend/js/ai-threat-assistant.js` renders the chat inside the
  `<section id="view-ai-assistant">` block of `frontend/html/index.html` (lines 735-799 in the copied file), streams
  answers with `fetch` + `ReadableStream`, keeps a conversation list, shows agent status/progress and the
  confirmation dialog for remediation. Styles: `frontend/css/ai-threat-assistant.css` plus design tokens from
  `frontend/css/style.css`. Sign-in happens on `frontend/html/account.html` + `frontend/js/account.js`.

## How RAG works in this chatbot

Retrieval-augmented generation = retrieve evidence first, then make the LLM answer only from it:

1. **Platform knowledge** (`platform_knowledge.py`): an in-memory **BM25** keyword index over Markdown docs
   (including `knowledge/PLATFORM_OVERVIEW.md`), Python docstrings/source, the live API route list, the sidebar
   navigation of `index.html`, Compose files and live MongoDB statistics. Queries are expanded with project
   synonyms (e.g. "experiment" <-> "lab"); the index is rebuilt only when files change. Top 5 chunks are used.
2. **Threat intelligence** (`threat_retrieval.py`): read-only structured search over the platform's CTI
   collections (`events`, `attributes`, `threats`, `reports`, `passages`) for CVEs, indicators and names; up to 5
   records become evidence chunks.
3. The chunks are numbered `[S1]`, `[S2]` ... in the system prompt, and the prompt tells the model to cite them and
   not invent anything.
4. **Answer checks** (`assistant.py`, `vector_rag.validate_grounded_answer`): citations to labels that were not
   provided are removed, CVE ids not present in the evidence are removed, and a threat answer that fails
   grounding validation is replaced by a summary of the retrieved records.

The separate admin "RAG workspace" (`routes/vector_rag.py` `/api/rag/query`, embeddings, document upload) is an
older pipeline; the chatbot reuses only its helpers (database names, citation handling, grounding validation).

## How conversation memory works

- Stored in MongoDB database `rag_demo`: `conversations` (one document per message: user id, conversation id,
  role, content, route, timestamp) and `conversation_metadata` (title, owner, timestamps).
- `prepare_request` checks the conversation belongs to the signed-in user (another user's id -> 404), then loads the
  **last 6 messages**, each cut to **500 characters**, which are passed to Ollama as previous turns.
- Short or pronoun-led follow-ups ("and what about its mitigation?") keep the previous route and add the previous
  user question to the retrieval query.
- A turn is saved **only after a `final` event**, so failed or cancelled answers leave no half-written history.

## How threat-intelligence retrieval works

`retrieve_structured_threat_evidence(database, question, limit=5)` extracts search terms (CVE ids, IPs, hashes,
domains, URLs, capitalised names), searches the configured fields of each CTI collection, normalises matching rows
into evidence chunks with their source provenance, and never writes to the collections. The records are produced by
the platform's collectors (MISP feeds, Galaxies, reports, VirusTotal/ThreatFox).

## Important source files

| Copy in this folder | Original | Role |
|---|---|---|
| `backend/services/assistant.py` | `backend/services/assistant.py` | **main chatbot logic**: routing, prompts, memory, streaming, checks, saving |
| `backend/services/llm_provider.py` | same | LLM provider/model configuration, Ollama streaming |
| `backend/services/platform_knowledge.py` | same | BM25 platform-knowledge index |
| `backend/services/threat_retrieval.py` | same | CTI retrieval from MongoDB |
| `backend/services/vector_rag.py` | same | conversation collections, citation helpers, grounding validation |
| `backend/services/local_assistant.py`, `local_agent.py` | same | laptop-scan routing and the signed agent client |
| `backend/routes/chapter17_compat.py` | same | chat API endpoints |
| `backend/routes/local_agent.py` | same | agent status / jobs / cancel / confirmation endpoints |
| `backend/routes/vector_rag.py`, `subscriptions.py`, `admin.py` | same | helpers, session user, admin check used by the chat routes |
| `backend/services/subscriptions.py`, `entitlements.py` | same | accounts, sessions and plan features behind the session check |
| `backend/utils/routes.py` | same | lists API routes for the knowledge index |
| `backend/config/config.py`, `database.py` | `backend/config.py`, `backend/database.py` | settings from env / `.env`, MongoDB connection |
| `backend/config/main.py` | `backend/main.py` | how the app wires the chatbot (lifespan, middleware) - reference |
| `backend/knowledge/PLATFORM_OVERVIEW.md` | same | curated platform overview indexed for platform answers |
| `frontend/...` | `public/...` | chat UI, styles, dashboard and sign-in pages |
| `local_agent/*.py` | `local_agent/*.py` | Windows security agent (HMAC-signed, read-only checks) |
| `docs/lab-17_*.md` | `lab-17/README.md`, `lab-17/THREAT_RETRIEVAL.md` | original RAG/retrieval documentation |

## Dependencies and external infrastructure

Python: fastapi, starlette, uvicorn, pydantic, pymongo, httpx, python-dotenv, typing_extensions (plus dnspython /
certifi for Atlas URIs) - see [requirements.txt](requirements.txt). Placeholders for all variables:
[.env.example](.env.example).

External, not contained in this folder:

- **Ollama** with `llama3.2:3b` on the host (`http://localhost:11434`).
- **MongoDB replica set `rs0`** (`fresh-mongo1..3`) - database `chapter07` for users, sessions and threat records
  (filled by the platform's collectors), `rag_demo` for conversations. Sign-in needs a replica set (transactions).
- **The original application** (`backend/main.py`, the `fresh-backend` container on `http://localhost:8000`)
  supplies the app object, sign-in middleware and quota metering the chat routes run inside.
- **Optional:** the local agent process on the Windows host (port 8765) for laptop scans.

## How the original chatbot operates

The running deployment is the `fresh-backend` container (image built from the root `Dockerfile`) at
`http://localhost:8000`. A user signs in at `/account`, opens **AI Threat Assistant** in the sidebar, and the page
calls `POST /api/ask/stream`; the container reaches Ollama on the host through `host.docker.internal:11434` and
MongoDB through the `fresh-mongo-net` Docker network.
