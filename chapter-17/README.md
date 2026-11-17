# Lab 17: Retrieval-Augmented Generation and Vector Search

These projects are integrated into the administrator-only **Technology & Innovation → Technology Portfolio** workspace. They do not add a separate dashboard or sidebar entry.

## Lab 17.1: Interactive RAG Pipeline

The RAG workspace provides document ingestion, chunk and embedding status, Vector Search index status, administrator-scoped conversations, retrieved source previews, and a correlated execution-stage history.

### Runtime configuration

Set these values on the FastAPI backend using the deployment's secret-management mechanism:

- `VOYAGE_API_KEY` — required for document and query embeddings.
- `VOYAGE_MODEL` — defaults to `voyage-3`; model dimensions are checked against provider responses.
- `VOYAGE_OUTPUT_DIMENSION` — optional output dimension where supported by the selected Voyage model.
- `LLM_PROVIDER` — `ollama` (preferred for new installs) or `openai-compatible`. When omitted, existing Chapter 17 API-key/endpoint settings continue to select the OpenAI-compatible provider; otherwise Ollama is selected.
- `OLLAMA_BASE_URL` and `OLLAMA_CHAT_MODEL` — Ollama URL and model; defaults are `http://localhost:11434` and `llama3.1:8b`. Plain HTTP is accepted only for local Ollama addresses or the Compose service hostname `ollama`.
- `RAG_LLM_API_KEY` or `OPENAI_API_KEY` — required by the OpenAI-compatible provider only.
- `RAG_LLM_API_URL` — optional OpenAI-compatible chat-completions endpoint; HTTPS is required except for loopback development endpoints.
- `RAG_LLM_MODEL` — OpenAI-compatible model; defaults to `gpt-4o-mini`.
- `LLM_TIMEOUT_SECONDS` — provider request timeout from 1 to 600 seconds; defaults to 90.

The browser never receives provider keys. The service stores chunks in `rag_demo.documents`, and messages in `rag_demo.conversations`; conversation reads and writes are scoped to the signed-in administrator. A MongoDB Vector Search index must be created after the first successful embedding batch. A local replica set does not establish that Vector Search is available; the status endpoint checks the actual deployment capability.

### Pipeline stages

1. Validate and chunk source text into 100-word windows with 20-word overlap.
2. Request and validate Voyage document vectors; store model and actual dimension metadata with each chunk.
3. Create and wait for the `rag_vector_index` Vector Search index.
4. Embed each question, retrieve matching chunks, load up to five recent messages from that administrator's conversation, and call the configured chat-completions service.
5. Return the answer, retrieved source chunks and observed stages.

The system-operation log records safe stage names, status, duration, error codes, project name and correlation ID. It does not record document text, questions, credentials or raw provider errors.

## Lab 17.2: Vector Search Benchmark

The benchmark accepts an administrator-uploaded UTF-8 JSONL file containing exactly 5,000 objects, each with non-empty `source` and `text` strings. Uploads are limited to 25 MiB. The optional `sentence-transformers/all-MiniLM-L6-v2` model generates and validates 384-dimensional vectors; insertion and progress reporting use batches of 500 records.

After MongoDB Atlas reports the `vector_benchmark_index` queryable, a run samples 100 deterministic queries. It records:

- Atlas approximate nearest-neighbor (ANN) latency using the managed Vector Search index.
- Atlas exact nearest-neighbor (ENN) latency.
- Local NumPy exact cosine top-10 results and ANN recall against that reference.
- Mean, p50, p95, minimum and maximum query latencies, plus per-query latency and recall.

The recommendation is generated from that completed run and dataset only. Index build duration is an upper-bound observation between the index request and the first status poll that observes query readiness; it is not an Atlas-reported build metric.

MongoDB Atlas Vector Search does not expose a supported IVF index or the requested custom HNSW `M` / `efConstruction` settings in this implementation. Those configurations are displayed as unsupported and are not simulated or relabeled. Earlier datasets are retained; a new dataset becomes active only after all 5,000 embeddings are stored and validated.

### Optional model dependencies

The backend image keeps machine-learning dependencies optional. To include them in an image build:

```powershell
docker build --build-arg INSTALL_VECTOR_LAB=true -t <your-image-tag> .
```

This installs `chapter-17/requirements-vector-lab.txt`. The model is loaded only after an administrator starts a dataset job. Provider credentials and MongoDB Atlas configuration are not included in the image.

## Administrator API

All routes below require a valid administrator session:

- `GET /api/rag/status`, `GET /api/rag/documents`
- `POST /api/rag/documents`, `POST /api/rag/index`
- `GET /api/rag/conversations`, `POST /api/rag/conversations`
- `GET /api/rag/conversations/{conversation_id}`, `POST /api/rag/query`
- `GET /api/vector-benchmark/status`, `POST /api/vector-benchmark/dataset`
- `POST /api/vector-benchmark/index`, `POST /api/vector-benchmark/runs`
- `GET /api/vector-benchmark/runs/{run_id}`

No RAG answer, benchmark metric, or project capability is represented as available until the corresponding provider, database feature, model, input data, and operation have actually succeeded.

### Main application integration and retrieval modes

The root application's original administrator routes remain available, with course-specification
aliases `POST /api/ask`, `GET/DELETE /api/ask/history`, and `GET/POST /api/ops/ask-index`.
Conversation retrieval is scoped to the current administrator identity; prompt memory includes at
most five recent user/assistant exchanges. The RAG prompt treats retrieved passages as untrusted
evidence and constrains citations to the supplied source labels.

Voyage embeddings remain the compatibility default. To use the 384-dimensional
`sentence-transformers/all-MiniLM-L6-v2` provider, install the optional vector-lab requirements,
preload the model in the backend's local cache, and set `RAG_EMBEDDING_PROVIDER=local`. Requests do
not download model weights. If the model or a queryable MongoDB Vector Search index is unavailable,
the question path uses bounded structured text matching over embedded report chunks; answers then
identify that retrieval mode. If the chat provider is unavailable but evidence was retrieved, the
API returns a clearly labelled extractive fallback. This does not implement custom HNSW/IVF tuning;
Atlas support and measured retrieval results remain deployment-dependent.

`LLM_PROVIDER=nvidia-nim` selects the OpenAI-compatible NVIDIA NIM endpoint through backend-only
`NVIDIA_NIM_API_KEY`, `NVIDIA_NIM_MODEL`, and optional `NVIDIA_NIM_BASE_URL`. `LLM_PROVIDER=ollama`
supports local Ollama without a key and Ollama Cloud through HTTPS plus `OLLAMA_API_KEY`. Provider
selection is explicit; the API does not silently fail over between providers.
