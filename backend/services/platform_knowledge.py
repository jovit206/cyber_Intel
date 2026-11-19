"""Searchable knowledge base about this platform, built from the project itself.

Sources (each chunk records which one it came from):
- documentation: Markdown files shipped with the project (README, guides, chapter READMEs, reports,
  and ``backend/knowledge/PLATFORM_OVERVIEW.md``);
- source code: module docstrings and top-level function/class docstrings of the Python code;
- verified API routes: the routes actually registered in the running FastAPI application;
- frontend: the dashboard's sidebar navigation from ``public/index.html``;
- configuration: Docker Compose and Dockerfile text;
- live database / live status: MongoDB collection statistics and deployment state at build time.

The index is built once and cached. File-based chunks are rebuilt only when a source file changes;
live chunks are refreshed at most every ``LIVE_TTL_SECONDS``. Retrieval is BM25 over a small
in-memory inverted index, so a question costs no database queries once the cache is warm.
"""
from __future__ import annotations

import ast
import logging
import math
import os
import re
import threading
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

logger = logging.getLogger("aegis.platform_knowledge")

PROJECT_ROOT = Path(__file__).resolve().parents[2]
LIVE_TTL_SECONDS = 300
MAX_CHUNK_CHARS = 1100
DOC_GLOBS = (
    "README.md", "ADMIN_GUIDE.md", "BILLING_AND_ENTITLEMENTS.md", "SECURITY_CENTER.md",
    "backend/knowledge/*.md", "chapter-*/README.md", "chapter-*/*.md", "chapter-*/**/README.md",
    "chapter-14/reports/*.md", "chapter-16/lab-16.1/*.md", "lab8/**/*.md", "lab9/**/*.md", "lab10/**/*.md", "chapter11/**/*.md",
)
CODE_GLOBS = (
    "backend/*.py", "backend/routes/*.py", "backend/services/*.py", "backend/collectors/*.py",
    "backend/models/*.py", "lab8/**/*.py", "lab9/**/*.py", "lab10/**/*.py", "chapter11/**/*.py", "chapter-14/*.py", "chapter-15/app/**/*.py",
    "chapter-16/lab-16.2/app/*.py",
)
CONFIG_FILES = ("docker-compose.yml", "Dockerfile", "chapter-18/docker-compose.yml", "chapter-16/docker-compose.yml",
                "chapter-13/docker-compose.yml", "lab9/failover-cluster/docker-compose.yml")
SKIP_PARTS = {"__pycache__", ".venv", "node_modules", "results", "secrets", "logs", "backups"}

_STOP = frozenset("""a about above after again all also am an and any are as at be because been before being
below between both but by can could did do does doing done down during each else few for from further get
gets got had has have having he her here hers how i if in into is it its itself just me more most my no nor
not now of off on once only or other our ours out over own please same she should so some such tell than that
the their theirs them then there these they this those through to too under until up us very was we were what
when where which while who whom why will with would you your yours explain describe show give list""".split())

# Words that mean the same thing in this project; the query is expanded with them.
_SYNONYMS = {
    "experiment": ("lab", "chapter", "exercise"), "lab": ("experiment", "chapter"), "chapter": ("experiment", "lab"),
    "exercise": ("experiment", "chapter"),
    "banking": ("wallet", "subscription", "transaction", "lab10"), "wallet": ("banking", "balance", "top-up"),
    "money": ("wallet", "top-up", "balance"), "add": ("top-up",), "credit": ("credits", "plan", "allocation"),
    "subscription": ("plan", "purchase", "entitlement"), "plan": ("subscription", "entitlement"),
    "network": ("partition", "replica", "reliability"), "reliability": ("partition", "replica", "failover"),
    "failover": ("election", "stepdown", "primary"), "election": ("primary", "failover"),
    "replication": ("replica", "secondary", "oplog"), "replica": ("replication", "rs0", "primary", "secondary"),
    "chatbot": ("assistant", "rag", "llm", "ask"), "assistant": ("chatbot", "rag", "llm", "ask"),
    "ollama": ("llm", "llama"), "llm": ("ollama", "model"), "rag": ("retrieval", "vector", "embedding"),
    "login": ("authentication", "session", "sign-in"), "authentication": ("session", "login", "password"),
    "auth": ("authentication", "session"), "ingestion": ("collector", "feed", "sync", "misp"),
    "ingest": ("collector", "feed", "sync"), "threat": ("cti", "misp"), "docker": ("compose", "container", "image"),
    "deployment": ("docker", "compose", "container"), "api": ("route", "endpoint"), "endpoint": ("route", "api"),
    "database": ("mongodb", "collection"), "mongodb": ("database", "collection", "mongo"),
    "frontend": ("public", "html", "javascript", "sidebar"), "backend": ("fastapi", "route", "service"),
    "sharding": ("shard", "mongos", "chapter11"), "backup": ("restore", "mongodump", "chapter-14"),
    "monitoring": ("metrics", "prometheus", "sampler"), "tenant": ("multi-tenant", "chapter-15"),
}
_TOKEN = re.compile(r"[a-z0-9][a-z0-9_\-./]*[a-z0-9]|[a-z0-9]")


def _stem(word: str) -> str:
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith("s") and not word.endswith(("ss", "us")):
        return word[:-1]
    return word


def tokenize(text: str) -> list[str]:
    out: list[str] = []
    for raw in _TOKEN.findall(text.casefold()):
        parts = [raw] + [p for p in re.split(r"[_\-./]", raw) if p and p != raw]
        for part in parts:
            if part in _STOP or len(part) < 2 and not part.isdigit():
                continue
            out.append(_stem(part))
    return out


def expand_query(question: str) -> list[str]:
    tokens = tokenize(question)
    expanded = list(tokens)
    for token in tokens:
        expanded.extend(_stem(s) for s in _SYNONYMS.get(token, ()))
    # "Experiment 13" / "lab 9" / "chapter-18" -> every spelling the project uses for that number.
    for number in re.findall(r"\b(?:experiment|lab|chapter|exercise|activity)s?[\s#-]*(\d{1,2})\b", question, re.I):
        expanded += ["experiment", "chapter", "lab", number, f"chapter-{number}", f"lab{number}", f"lab_{number}",
                     f"exercise-{number}", f"chapter{number}"]
    return expanded


# When the question names a kind of source, prefer chunks of that kind.
_KIND_HINTS = (
    (r"\b(api|apis|endpoints?|routes?|http)\b", "verified API routes", 1.8),
    (r"\b(frontend|front-end|pages?|sidebar|ui|buttons?|views?|screen)\b", "frontend", 1.6),
    (r"\b(docker|containers?|compose|deploy(ment)?|images?|ports?)\b", "configuration", 1.4),
    (r"\b(collections?|schemas?|stored|records?|documents?|current(ly)?|status|connected)\b", "live status", 1.4),
    (r"\b(source|code|implement(ed|ation)?|functions?|internally|module)\b", "source code", 1.3),
)


@dataclass
class Chunk:
    chunk_id: str
    title: str
    text: str
    kind: str          # documentation | source code | verified API routes | frontend | configuration | live status
    path: str
    tokens: Counter = field(default_factory=Counter, repr=False)
    length: int = 0


def _relative(path: Path) -> str:
    return path.relative_to(PROJECT_ROOT).as_posix()


def _iter_files(patterns: Iterable[str]) -> list[Path]:
    seen: dict[str, Path] = {}
    for pattern in patterns:
        for path in PROJECT_ROOT.glob(pattern):
            if path.is_file() and not (set(path.relative_to(PROJECT_ROOT).parts) & SKIP_PARTS):
                seen.setdefault(path.as_posix(), path)
    return sorted(seen.values())


def _windows(text: str, limit: int = MAX_CHUNK_CHARS) -> list[str]:
    """Split text on paragraph boundaries into windows no longer than ``limit``."""
    paragraphs = []
    for block in (p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()):
        if len(block) <= limit:
            paragraphs.append(block)
            continue
        # Long blocks (typically bullet lists) are split at list items / lines, never mid-sentence,
        # and each piece repeats the previous item so related bullets stay together.
        items = re.split(r"\n(?=\s*(?:[-*]|\d+\.)\s)", block) if re.search(r"\n\s*(?:[-*]|\d+\.)\s", block) else block.splitlines()
        piece, previous = "", ""
        for item in items:
            item = item.strip()
            while len(item) > limit:
                paragraphs.append(item[:limit])
                item = item[limit:]
            if piece and len(piece) + len(item) + 1 > limit:
                paragraphs.append(piece)
                piece = previous if len(previous) + len(item) + 1 <= limit else ""
            piece = f"{piece}\n{item}" if piece else item
            previous = item
        if piece:
            paragraphs.append(piece)
    windows, current = [], ""
    for paragraph in paragraphs:
        if len(current) + len(paragraph) + 2 > limit and current:
            windows.append(current)
            current = paragraph
        else:
            current = f"{current}\n\n{paragraph}" if current else paragraph
    if current:
        windows.append(current)
    return windows


def _markdown_chunks(path: Path) -> list[Chunk]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    rel = _relative(path)
    chunks: list[Chunk] = []
    heading_path: list[str] = []
    section: list[str] = []
    doc_title = rel

    def flush():
        body = "\n".join(section).strip()
        if not body:
            return
        title = " > ".join([doc_title] + heading_path[1:]) if heading_path else doc_title
        for index, window in enumerate(_windows(body)):
            chunks.append(Chunk(f"kb:{rel}#{len(chunks)}", title, window, "documentation", rel))

    in_fence = False
    for line in text.splitlines():
        if line.lstrip().startswith(("```", "~~~")):
            in_fence = not in_fence
        match = None if in_fence else re.match(r"^(#{1,4})\s+(.*)", line)
        if match:
            flush()
            section = []
            level = len(match.group(1))
            heading = match.group(2).strip()
            if level == 1:
                doc_title = f"{rel}: {heading}"
                heading_path = [heading]
            else:
                heading_path = (heading_path[: level - 1] if heading_path else [doc_title]) + [heading]
        else:
            section.append(line)
    flush()
    return chunks


def _code_chunks(path: Path) -> list[Chunk]:
    rel = _relative(path)
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, SyntaxError, ValueError):
        return []
    lines = []
    module_doc = ast.get_docstring(tree)
    if module_doc:
        lines.append(module_doc.strip())
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name.startswith("_") and not ast.get_docstring(node):
                continue
            kind = "class" if isinstance(node, ast.ClassDef) else "function"
            doc = (ast.get_docstring(node) or "").strip().split("\n\n")[0]
            lines.append(f"{kind} {node.name}" + (f": {doc}" if doc else ""))
    if not lines:
        return []
    body = "\n".join(lines)
    return [Chunk(f"kb:{rel}#{i}", f"Source code: {rel}", window, "source code", rel)
            for i, window in enumerate(_windows(body))]


def _config_chunks(path: Path) -> list[Chunk]:
    rel = _relative(path)
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    # Comments and service definitions only; no environment values are secrets in these files,
    # but skip lines that look like credentials just in case.
    text = "\n".join(line for line in text.splitlines()
                     if not re.search(r"(?i)(password|secret|api_key|token)\s*[:=]\s*[^${\s]", line))
    return [Chunk(f"kb:{rel}#{i}", f"Configuration: {rel}", window, "configuration", rel)
            for i, window in enumerate(_windows(text, 1400))]


def _frontend_chunks() -> list[Chunk]:
    path = PROJECT_ROOT / "public" / "index.html"
    try:
        html = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    sidebar = html.split("<main", 1)[0]
    lines, section = [], "Navigation"
    for match in re.finditer(r'nav-section-title[^>]*>([^<]+)<|<button[^>]*data-view="([^"]+)"[^>]*>(.*?)</button>', sidebar, re.S):
        if match.group(1):
            section = match.group(1).strip()
        else:
            label = re.sub(r"<[^>]+>|\s+", " ", match.group(3)).strip()
            restricted = " (administrators only)" if "admin-nav-link" in match.group(0) else ""
            lines.append(f"{section}: '{label}' opens the '{match.group(2)}' view{restricted}.")
    scripts = sorted(p.name for p in (PROJECT_ROOT / "public").glob("*.js"))
    text = ("The dashboard (public/index.html) sidebar contains these views:\n" + "\n".join(lines)
            + "\n\nFrontend scripts in public/: " + ", ".join(scripts)
            + ". The sign-in and registration page is public/account.html (script account.js).")
    return [Chunk(f"kb:frontend#{i}", "Frontend navigation and pages", window, "frontend", "public/index.html")
            for i, window in enumerate(_windows(text))]


def _route_chunks(app) -> list[Chunk]:
    from backend.utils.routes import iter_routes
    groups: dict[str, list[str]] = defaultdict(list)
    for route in iter_routes(app):
        methods = sorted(m for m in route.methods if m not in {"HEAD", "OPTIONS"})
        if not route.path.startswith("/api/") or not methods:
            continue
        tag = route.tags[0] if route.tags else route.path.split("/")[2]
        doc = (getattr(route.endpoint, "__doc__", None) or "").strip().splitlines()
        module = getattr(route.endpoint, "__module__", "")
        groups[tag].append(f"{', '.join(methods)} {route.path}" + (f": {doc[0]}" if doc else "") + f" [{module}]")
    summary = ["Summary of the application's HTTP API (verified from the registered routes). "
               "Every /api/ route requires a signed-in session except the sign-in, registration, plan list and "
               "a few health/webhook routes. Route groups and their number of operations:"]
    for tag, entries in sorted(groups.items(), key=lambda item: -len(item[1])):
        prefixes = sorted({entry.split(" ", 2)[1].split("/")[2] for entry in entries if entry.count("/") >= 2})
        summary.append(f"- {tag}: {len(entries)} operations under /api/{', /api/'.join(prefixes)}")
    chunks = [Chunk(f"kb:api:summary#{i}", "API overview (all route groups)", window, "verified API routes",
                    "backend/routes") for i, window in enumerate(_windows("\n\n".join(summary), 1800))]
    for tag, entries in sorted(groups.items()):
        # Window over route lines (one line per route) so a window never splits a route entry.
        header = f"Registered API routes for '{tag}' (verified from the running application):"
        windows, current = [], [header]
        for entry in entries:
            if sum(len(line) + 1 for line in current) + len(entry) > 1400 and len(current) > 1:
                windows.append(current)
                current = [header + " (continued)"]
            current.append(entry)
        windows.append(current)
        for i, lines in enumerate(windows):
            chunks.append(Chunk(f"kb:api:{tag}#{i}", f"API routes: {tag}", "\n".join(lines),
                                "verified API routes", "backend/routes"))
    return chunks


def _live_chunks(database) -> list[Chunk]:
    if database is None:
        return []
    facts: list[str] = []
    try:
        hello = database.client.admin.command("hello")
        if hello.get("setName"):
            facts.append(f"The backend is connected to MongoDB replica set '{hello['setName']}' with members "
                         f"{', '.join(hello.get('hosts', []))}; current primary {hello.get('primary')}.")
        else:
            facts.append("The backend is connected to a standalone MongoDB server, not a replica set. "
                         "Multi-document transactions (wallet top-ups, purchases, registration) and replica-set "
                         "status are unavailable on a standalone server.")
        facts.append(f"MongoDB server version {database.client.server_info().get('version')}.")
    except Exception as error:  # live facts are best-effort and never block the assistant
        facts.append(f"MongoDB status could not be read ({type(error).__name__}).")
    for db_name in (database.name, "rag_demo", "threat_intel_lab18"):
        try:
            target = database.client[db_name]
            names = sorted(target.list_collection_names())
            if not names:
                continue
            parts = []
            for name in names:
                if name.startswith("system."):
                    continue
                count = target[name].estimated_document_count()
                parts.append(f"{name} ({count:,} documents)")
            facts.append(f"Database '{db_name}' collections: " + "; ".join(parts) + ".")
            # Field names are sampled only from threat-intelligence collections; account collections
            # are listed by name and count only.
            for name in ("events", "attributes", "threats", "reports", "passages", "full_threats"):
                if name in names:
                    sample = target[name].find_one({}, {"_id": 0})
                    if sample:
                        facts.append(f"Fields of {db_name}.{name}: " + ", ".join(sorted(sample.keys())[:30]) + ".")
        except Exception as error:
            facts.append(f"Database '{db_name}' could not be inspected ({type(error).__name__}).")
    try:
        from backend.services.llm_provider import get_llm_configuration
        config = get_llm_configuration()
        facts.append(f"Configured LLM provider: {config.provider_label}, model {config.model}, "
                     f"request timeout {config.timeout_seconds} s.")
    except Exception as error:
        facts.append(f"LLM configuration could not be read ({type(error).__name__}).")
    text = "\n\n".join(facts)
    return [Chunk(f"kb:live#{i}", "Live deployment status (read from MongoDB and configuration)", window,
                  "live status", "live")
            for i, window in enumerate(_windows(text, 1400))]


class KnowledgeBase:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._file_chunks: list[Chunk] = []
        self._file_signature: tuple = ()
        self._route_chunks: list[Chunk] = []
        self._route_count = -1
        self._live_chunks: list[Chunk] = []
        self._live_built = 0.0
        self._index: dict[str, list[tuple[int, int]]] = {}
        self._chunks: list[Chunk] = []
        self._avg_len = 1.0

    def _signature(self, files: list[Path]) -> tuple:
        sig = []
        for path in files:
            try:
                stat = path.stat()
                sig.append((path.as_posix(), stat.st_mtime_ns, stat.st_size))
            except OSError:
                continue
        return tuple(sig)

    def _rebuild_index(self) -> None:
        chunks = self._file_chunks + self._route_chunks + self._live_chunks
        index: dict[str, list[tuple[int, int]]] = defaultdict(list)
        total = 0
        for position, chunk in enumerate(chunks):
            if not chunk.tokens:
                chunk.tokens = Counter(tokenize(f"{chunk.title}\n{chunk.path}\n{chunk.text}"))
                chunk.length = sum(chunk.tokens.values())
            total += chunk.length
            for token, frequency in chunk.tokens.items():
                index[token].append((position, frequency))
        self._chunks, self._index = chunks, dict(index)
        self._avg_len = (total / len(chunks)) if chunks else 1.0

    def refresh(self, app=None, database=None) -> None:
        with self._lock:
            changed = False
            doc_files, code_files = _iter_files(DOC_GLOBS), _iter_files(CODE_GLOBS)
            config_files = [PROJECT_ROOT / name for name in CONFIG_FILES if (PROJECT_ROOT / name).is_file()]
            signature = self._signature(doc_files + code_files + config_files + [PROJECT_ROOT / "public" / "index.html"])
            if signature != self._file_signature:
                started = time.perf_counter()
                chunks: list[Chunk] = []
                for path in doc_files:
                    chunks.extend(_markdown_chunks(path))
                for path in code_files:
                    chunks.extend(_code_chunks(path))
                for path in config_files:
                    chunks.extend(_config_chunks(path))
                chunks.extend(_frontend_chunks())
                self._file_chunks, self._file_signature, changed = chunks, signature, True
                logger.info("Platform knowledge: %d file chunks from %d files in %.0f ms", len(chunks),
                            len(signature), (time.perf_counter() - started) * 1000)
            if app is not None:
                from backend.utils.routes import iter_routes
                route_count = sum(1 for _ in iter_routes(app))
                if route_count != self._route_count:
                    self._route_chunks, self._route_count, changed = _route_chunks(app), route_count, True
            if database is not None and time.monotonic() - self._live_built > LIVE_TTL_SECONDS:
                self._live_chunks, self._live_built, changed = _live_chunks(database), time.monotonic(), True
            if changed or not self._chunks:
                self._rebuild_index()

    def search(self, question: str, limit: int = 6, *, app=None, database=None) -> list[dict[str, Any]]:
        self.refresh(app, database)
        terms = expand_query(question)
        if not terms or not self._chunks:
            return []
        weights = Counter(terms)
        n = len(self._chunks)
        scores: dict[int, float] = defaultdict(float)
        k1, b = 1.4, 0.7
        for term, query_weight in weights.items():
            postings = self._index.get(term)
            if not postings:
                continue
            idf = math.log(1 + (n - len(postings) + 0.5) / (len(postings) + 0.5))
            for position, frequency in postings:
                length = self._chunks[position].length or 1
                tf = frequency * (k1 + 1) / (frequency + k1 * (1 - b + b * length / self._avg_len))
                scores[position] += idf * tf * (1.0 + 0.3 * (query_weight - 1))
        lowered = question.casefold()
        boosts = {kind: factor for pattern, kind, factor in _KIND_HINTS if re.search(pattern, lowered)}
        if boosts:
            for position in scores:
                scores[position] *= boosts.get(self._chunks[position].kind, 1.0)
        ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
        results, per_file = [], Counter()
        for position, score in ranked:
            chunk = self._chunks[position]
            if per_file[chunk.path] >= 2:
                continue
            per_file[chunk.path] += 1
            results.append({"chunk_id": chunk.chunk_id, "source": chunk.title, "text": chunk.text,
                            "source_reference": chunk.path, "evidence_type": chunk.kind, "score": round(score, 3)})
            if len(results) >= limit:
                break
        return results

    def stats(self) -> dict[str, Any]:
        kinds = Counter(chunk.kind for chunk in self._chunks)
        return {"chunks": len(self._chunks), "by_kind": dict(kinds)}


KNOWLEDGE_BASE = KnowledgeBase()


def search_platform_knowledge(question: str, limit: int = 6, *, app=None, database=None) -> list[dict[str, Any]]:
    try:
        return KNOWLEDGE_BASE.search(question, limit, app=app, database=database)
    except Exception:  # the knowledge base must never break the assistant
        logger.exception("Platform knowledge search failed")
        return []


def warm_platform_knowledge(app=None, database=None) -> None:
    """Build the index in the background at start-up so the first question is not slower."""
    def run():
        try:
            KNOWLEDGE_BASE.refresh(app, database)
            logger.info("Platform knowledge ready: %s", KNOWLEDGE_BASE.stats())
        except Exception:
            logger.exception("Platform knowledge warm-up failed")
    threading.Thread(target=run, name="platform-knowledge-warmup", daemon=True).start()
