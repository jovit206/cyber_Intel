"""Read-only Chapter 16 database probes and evidence queries."""
from __future__ import annotations

import os
import threading
import time
from typing import Any

from pymongo import MongoClient

_FAILURE_THRESHOLD = 3
_RECOVERY_SECONDS = 30


class CircuitBreaker:
    def __init__(self):
        self._lock = threading.RLock()
        self._backends = {name: {"state": "closed", "failures": 0, "opened_at": 0.0, "probe": False} for name in ("mongodb", "cassandra", "neo4j")}

    def state(self, name: str) -> str:
        with self._lock:
            return self._backends[name]["state"]

    def call(self, name: str, operation):
        with self._lock:
            state = self._backends[name]
            now = time.monotonic()
            if state["state"] == "open":
                if now - state["opened_at"] < _RECOVERY_SECONDS:
                    raise RuntimeError("circuit_open")
                state["state"] = "half_open"
            if state["state"] == "half_open":
                if state["probe"]:
                    raise RuntimeError("circuit_probe_in_progress")
                state["probe"] = True
        try:
            result = operation()
        except Exception:
            with self._lock:
                state = self._backends[name]
                state["probe"] = False
                state["failures"] += 1
                if state["state"] == "half_open" or state["failures"] >= _FAILURE_THRESHOLD:
                    state["state"] = "open"
                    state["opened_at"] = time.monotonic()
            raise
        with self._lock:
            state = self._backends[name]
            state.update(state="closed", failures=0, opened_at=0.0, probe=False)
        return result


_CIRCUIT_BREAKER = CircuitBreaker()


def configuration() -> dict[str, Any]:
    mongo_uri = os.getenv("CHAPTER16_MONGO_URI", "").strip()
    cassandra_hosts = tuple(host.strip() for host in os.getenv("CHAPTER16_CASSANDRA_HOSTS", "").split(",") if host.strip())
    neo4j_uri = os.getenv("CHAPTER16_NEO4J_URI", "").strip()
    neo4j_user = os.getenv("CHAPTER16_NEO4J_USERNAME", "").strip()
    neo4j_password = os.getenv("CHAPTER16_NEO4J_PASSWORD", "")
    return {
        "mongo_uri": mongo_uri,
        "mongo_database": os.getenv("CHAPTER16_MONGO_DATABASE", "fraud_detection").strip(),
        "cassandra_hosts": cassandra_hosts,
        "cassandra_port": int(os.getenv("CHAPTER16_CASSANDRA_PORT", "9042")),
        "cassandra_username": os.getenv("CHAPTER16_CASSANDRA_USERNAME", "").strip(),
        "cassandra_password": os.getenv("CHAPTER16_CASSANDRA_PASSWORD", ""),
        "neo4j_uri": neo4j_uri,
        "neo4j_username": neo4j_user,
        "neo4j_password": neo4j_password,
        "neo4j_database": os.getenv("CHAPTER16_NEO4J_DATABASE", "neo4j").strip(),
        "configured": {
            "mongodb": bool(mongo_uri),
            "cassandra": bool(cassandra_hosts),
            "neo4j": bool(neo4j_uri and neo4j_user and neo4j_password),
        },
    }


def _unavailable(reason: str) -> dict[str, Any]:
    return {"status": "not_configured" if reason == "configuration_required" else "unavailable", "reason": reason}


def status_report() -> dict[str, Any]:
    settings = configuration()
    results: dict[str, Any] = {}
    if not settings["configured"]["mongodb"]:
        results["mongodb"] = _unavailable("configuration_required")
    else:
        client = None
        started = time.perf_counter()
        try:
            client = MongoClient(settings["mongo_uri"], serverSelectionTimeoutMS=2500, connectTimeoutMS=2000, appname="aegis-chapter16-status")
            client.admin.command({"ping": 1})
            results["mongodb"] = {"status": "connected", "latency_ms": round((time.perf_counter() - started) * 1000, 2), "database": settings["mongo_database"]}
        except Exception as error:
            results["mongodb"] = {"status": "unavailable", "error_type": type(error).__name__}
        finally:
            if client is not None:
                client.close()

    if not settings["configured"]["cassandra"]:
        results["cassandra"] = _unavailable("configuration_required")
    else:
        cluster = session = None
        started = time.perf_counter()
        try:
            from cassandra.auth import PlainTextAuthProvider
            from cassandra.cluster import Cluster
            options = {}
            if settings["cassandra_username"] and settings["cassandra_password"]:
                options["auth_provider"] = PlainTextAuthProvider(settings["cassandra_username"], settings["cassandra_password"])
            cluster = Cluster(list(settings["cassandra_hosts"]), port=settings["cassandra_port"], connect_timeout=3, **options)
            session = cluster.connect()
            row = session.execute("SELECT release_version, data_center FROM system.local", timeout=5).one()
            results["cassandra"] = {"status": "connected", "version": row.release_version, "datacenter": row.data_center, "latency_ms": round((time.perf_counter() - started) * 1000, 2)}
        except ImportError:
            results["cassandra"] = _unavailable("driver_not_installed")
        except Exception as error:
            results["cassandra"] = {"status": "unavailable", "error_type": type(error).__name__}
        finally:
            if session is not None:
                session.shutdown()
            if cluster is not None:
                cluster.shutdown()

    if not settings["configured"]["neo4j"]:
        results["neo4j"] = _unavailable("configuration_required")
    else:
        driver = None
        started = time.perf_counter()
        try:
            from neo4j import GraphDatabase
            driver = GraphDatabase.driver(settings["neo4j_uri"], auth=(settings["neo4j_username"], settings["neo4j_password"]), connection_timeout=3, max_connection_pool_size=5)
            driver.verify_connectivity()
            results["neo4j"] = {"status": "connected", "database": settings["neo4j_database"], "latency_ms": round((time.perf_counter() - started) * 1000, 2)}
        except ImportError:
            results["neo4j"] = _unavailable("driver_not_installed")
        except Exception as error:
            results["neo4j"] = {"status": "unavailable", "error_type": type(error).__name__}
        finally:
            if driver is not None:
                driver.close()
    states = [item["status"] for item in results.values()]
    overall = "healthy" if all(state == "connected" for state in states) else "not_configured" if all(state == "not_configured" for state in states) else "degraded"
    return {"status": overall, "databases": results, "circuit_breakers": {name: _CIRCUIT_BREAKER.state(name) for name in ("mongodb", "cassandra", "neo4j")},
            "service_control": {"status": "unavailable", "reason": "No service orchestrator is configured for Chapter 16."}}


class Chapter16Connections:
    def __init__(self, settings: dict[str, Any]):
        self.settings = settings
        self.mongo_client = None
        self.cassandra_cluster = None
        self.cassandra_session = None
        self.neo4j_driver = None
        try:
            if settings["configured"]["mongodb"]:
                self.mongo_client = MongoClient(settings["mongo_uri"], serverSelectionTimeoutMS=2500, connectTimeoutMS=2000, appname="aegis-chapter16-fraud")
            if settings["configured"]["cassandra"]:
                from cassandra.auth import PlainTextAuthProvider
                from cassandra.cluster import Cluster
                options = {}
                if settings["cassandra_username"] and settings["cassandra_password"]:
                    options["auth_provider"] = PlainTextAuthProvider(settings["cassandra_username"], settings["cassandra_password"])
                self.cassandra_cluster = Cluster(list(settings["cassandra_hosts"]), port=settings["cassandra_port"], connect_timeout=3, **options)
                self.cassandra_session = self.cassandra_cluster.connect()
            if settings["configured"]["neo4j"]:
                from neo4j import GraphDatabase
                self.neo4j_driver = GraphDatabase.driver(settings["neo4j_uri"], auth=(settings["neo4j_username"], settings["neo4j_password"]), connection_timeout=3, max_connection_pool_size=5)
                self.neo4j_driver.verify_connectivity()
        except Exception:
            self.close()
            raise

    def close(self) -> None:
        for resource, method in ((self.neo4j_driver, "close"), (self.cassandra_session, "shutdown"), (self.cassandra_cluster, "shutdown"), (self.mongo_client, "close")):
            if resource is not None:
                try:
                    getattr(resource, method)()
                except Exception:
                    pass


def fraud_review(account_id: str, transaction_id: str, max_hops: int = 3) -> dict[str, Any]:
    if not 1 <= max_hops <= 3:
        raise ValueError("max_hops must be between 1 and 3")
    settings = configuration()
    connections = Chapter16Connections(settings)
    results: dict[str, Any] = {}
    try:
        if connections.mongo_client is not None:
            started = time.perf_counter()
            transaction = _CIRCUIT_BREAKER.call("mongodb", lambda: connections.mongo_client[settings["mongo_database"]].transactions.find_one({"txn_id": transaction_id}, {"_id": 0}))
            results["mongodb"] = {"status": "queried", "transaction": transaction, "query_ms": round((time.perf_counter() - started) * 1000, 2)}
        else:
            results["mongodb"] = _unavailable("configuration_required")
        if connections.cassandra_session is not None:
            started = time.perf_counter()
            def read_timeline():
                statement = connections.cassandra_session.prepare("SELECT user_id, event_time, event_type, transaction_id, amount, location FROM fraud_data.user_profiles WHERE user_id = ? ORDER BY event_time DESC LIMIT 10")
                return [dict(row._asdict()) for row in connections.cassandra_session.execute(statement, (account_id,), timeout=5)]
            timeline = _CIRCUIT_BREAKER.call("cassandra", read_timeline)
            results["cassandra"] = {"status": "queried", "timeline": timeline, "query_ms": round((time.perf_counter() - started) * 1000, 2)}
        else:
            results["cassandra"] = _unavailable("configuration_required")
        if connections.neo4j_driver is not None:
            started = time.perf_counter()
            def read_paths():
                query = f"MATCH p=(u:User {{id: $user_id}})-[:TRANSFERRED_TO*1..{max_hops}]->(connected:User) RETURN [node IN nodes(p) | node.id] AS path LIMIT 50"
                with connections.neo4j_driver.session(database=settings["neo4j_database"]) as session:
                    return [record["path"] for record in session.run(query, user_id=account_id)]
            paths = _CIRCUIT_BREAKER.call("neo4j", read_paths)
            results["neo4j"] = {"status": "queried", "transfer_paths": paths, "query_ms": round((time.perf_counter() - started) * 1000, 2)}
        else:
            results["neo4j"] = _unavailable("configuration_required")
        missing = [name for name, value in results.items() if value.get("status") != "queried"]
        return {"account_id": account_id, "transaction_id": transaction_id, "max_hops": max_hops,
                "status": "partial" if missing else "complete", "databases": results,
                "circuit_breakers": {name: _CIRCUIT_BREAKER.state(name) for name in ("mongodb", "cassandra", "neo4j")},
                "unavailable_backends": missing,
                "evidence_note": "MongoDB transaction, Cassandra timeline, and Neo4j paths are separate evidence sources; no automated verdict is asserted."}
    finally:
        connections.close()
