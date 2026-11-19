# AI Threat Assistant - architecture

Describes the chatbot code in this folder. The file paths (`backend/...`, `public/...`) are the same in the original
platform and in this standalone app (`MANIFEST.json` maps them). Differences of the standalone app: it starts from
`app.py` instead of the original `backend/main.py`, so the `require_platform_session` middleware step below (quota
metering) does not run - the session check in the chat routes still does; the page is `public/assistant.html`; and
`/health` + `/health/details` are added.

## End-to-end flow

```text
User
 │  types a question in the chat panel
 ▼
Frontend  public/ai-threat-assistant.js
 │  POST /api/ask/stream   {question, conversation_id, client: "ai-threat-assistant", agent_job_id?}
 │  cookie: aegis_session (from POST /api/subscriptions/login)
 ▼
backend/main.py middleware  require_platform_session
 │  session cookie (SHA-256) -> subscription_sessions / subscription_users; reserve monthly feature quota
 ▼
Backend API  backend/routes/chapter17_compat.py  ask_stream()
 │  Depends(_session_user); assistant.prepare_request(): ownership check + last 6 messages from rag_demo.conversations
 ▼
Question classification  backend/services/assistant.py
 │  1. local_assistant.classify(question)  -> laptop-scan intent?  (CVE / IOC values never go to the agent)
 │  2. classify_question(question, previous_route)
 │        CVE or IOC value present        -> threat   (mitigation if it asks how to defend)
 │        defend/patch/mitigate + threat  -> mitigation
 │        how/why + platform words        -> platform
 │        lookup verb + threat noun/name  -> threat
 │        short follow-up                 -> previous route
 │        platform words / security words -> platform / general_security, otherwise unknown
 ▼
Retrieval
 │  platform          platform_knowledge.search_platform_knowledge()  BM25, top 5 chunks
 │  threat/mitigation threat_retrieval.retrieve_structured_threat_evidence()  MongoDB CTI, up to 5 records
 │  general_security  none (model knowledge, answer labelled as not verified)
 │  laptop scan       local_agent.start() -> signed request to the Windows agent; progress streamed
 ▼
Prompt  _platform_prompt / _threat_prompt / _mitigation_prompt / _general_prompt
 │  system prompt with numbered evidence [S1].. and rules (cite labels, never invent CVEs/IOCs/records)
 │  + previous turns (6 messages x 500 chars) + the question; output cap 400-600 tokens by route
 ▼
LLM (Ollama)  backend/services/llm_provider.py  stream_chat_answer()
 │  POST {OLLAMA_BASE_URL}/api/chat  model llama3.2:3b, stream=true, keep_alive=30m
 ▼
Answer checks  assistant.py
 │  _strip_unknown_citations, _remove_unverified_cves, validate_grounded_answer (threat answers that fail
 │  grounding are replaced by the retrieved records)
 ▼
Response
 │  NDJSON: {"type":"meta"} -> {"type":"token","text":...} x N -> {"type":"final", answer, route, mode, sources...}
 │  after "final": save user + assistant messages (rag_demo.conversations, conversation_metadata)
 ▼
Frontend renders tokens as they arrive, strips [S#] markers from the display, refreshes the history list
```

## Components and responsibilities

| Layer | Original file | Key functions / objects |
|---|---|---|
| UI | `public/ai-threat-assistant.js`, `.css`; markup in `public/index.html` (`#view-ai-assistant`) | `initializeAiThreatAssistant`, `streamAnswer`, history list, agent pill, confirm dialog |
| Sign-in | `public/account.html`, `public/account.js`; `backend/routes/subscriptions.py` | `/api/subscriptions/login`, `_session_user` |
| App wiring | `backend/main.py` | `lifespan` (MongoDB, `warm_platform_knowledge`), `require_platform_session`, `FEATURE_PATHS` quota |
| API | `backend/routes/chapter17_compat.py` | `ask`, `ask_stream`, history endpoints, `/api/ops/ask-index` |
| Orchestration | `backend/services/assistant.py` | `prepare_request`, `classify_question`, `answer_events`, prompts, checks, `_save` |
| LLM | `backend/services/llm_provider.py` | `get_llm_configuration`, `stream_chat_answer`, `generate_chat_answer` |
| Platform RAG | `backend/services/platform_knowledge.py`, `backend/utils/routes.py`, `backend/knowledge/PLATFORM_OVERVIEW.md` | BM25 index, `search_platform_knowledge`, `warm_platform_knowledge` |
| Threat RAG | `backend/services/threat_retrieval.py` | `retrieve_structured_threat_evidence` |
| Memory & grounding helpers | `backend/services/vector_rag.py` | `rag_database`, `CONVERSATIONS_COLLECTION`, `validate_grounded_answer`, citation helpers |
| Laptop agent | `backend/services/local_assistant.py`, `backend/services/local_agent.py`, `backend/routes/local_agent.py`, `local_agent/*.py` | security-only tool allowlist, HMAC client, jobs, cancel, confirmations |
| Settings / DB | `backend/config.py`, `backend/database.py` | `settings`, `connect_database`, `get_database` |

## Data stores

| Store | Content | Written by the chatbot? |
|---|---|---|
| `chapter07.subscription_users`, `subscription_sessions`, `feature_usage` | accounts, sessions, monthly quota | sessions on login; quota via middleware |
| `chapter07.events`, `attributes`, `threats`, `reports`, `passages` | threat intelligence from the collectors | no (read-only) |
| `rag_demo.conversations`, `rag_demo.conversation_metadata` | chat history | yes, after each final answer |
| `%LOCALAPPDATA%\AegisLocalAgent` (Windows) | agent token, action log | by the local agent |

## Deployment on this machine (original)

```text
Browser ──http://localhost:8000──> fresh-backend container (uvicorn backend.main:app)
                                      ├── fresh-mongo-net ──> fresh-mongo1..3  (replica set rs0)
                                      ├── host.docker.internal:11434 ──> Ollama on Windows (llama3.2:3b, GPU)
                                      └── host.docker.internal:8765  ──> local security agent (optional)
```

## Standalone Docker deployment (`jovit206/chatbot`)

```text
Browser ──http://localhost:8000──> standalone-ai-chatbot container (uvicorn app:app, image jovit206/chatbot)
                                      ├── MONGO_URI ──> any MongoDB replica set (yours, or the optional
                                      │                 chatbot-mongo service: docker compose --profile local-mongo)
                                      └── host.docker.internal:11434 ──> Ollama on the host (llama3.2:3b)
```

Startup (`app.py` lifespan): connect MongoDB (`backend/database.py`), create the account indexes, log the LLM
configuration, warm the platform-knowledge index in the background. The Docker `HEALTHCHECK` calls `/health`;
`/health/details` additionally pings MongoDB and asks Ollama for its model list.
