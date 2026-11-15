from __future__ import annotations

import os
import sys
import importlib.util
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
LAB_161 = ROOT / "lab-16.1"
LAB_162 = ROOT / "lab-16.2"
load_dotenv(ROOT / ".env")
for path in (str(ROOT / "scripts"), str(LAB_161), str(LAB_161 / "common"), str(LAB_162)):
    if path not in sys.path:
        sys.path.insert(0, path)

os.environ.setdefault("MONGO_URI", "mongodb://localhost:27020")
os.environ.setdefault("NEO4J_URI", "bolt://localhost:7688")

from app.cassandra_support import create_cluster
import pytest

from neo4j import GraphDatabase
from pymongo import MongoClient

from app.config import Settings
from app.database import DatabaseConnections, LocalhostAddressTranslator


def load_module(name: str, file_path: Path):
    spec = importlib.util.spec_from_file_location(name, file_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Unable to load module at {file_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def mongo_client():
    client = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27020"), serverSelectionTimeoutMS=5000)
    try:
        client.admin.command("ping")
    except Exception as error:
        client.close()
        pytest.skip(f"Chapter 16 MongoDB is not available: {type(error).__name__}")
    yield client
    client.close()


@pytest.fixture(scope="session")
def mongo_database(mongo_client):
    database_name = os.getenv("MONGO_DATABASE", "university_db")
    if database_name != "university_db":
        raise RuntimeError("Integration tests are restricted to the Chapter 16 university_db.")
    return mongo_client[database_name]


@pytest.fixture(scope="session")
def cassandra_cluster():
    hosts = [host.strip() for host in os.getenv("CASSANDRA_HOSTS", "localhost").split(",") if host.strip()]
    cluster = create_cluster(
        hosts,
        port=int(os.getenv("CASSANDRA_PORT", "9043")),
        executor_threads=4,
        address_translator=(
            LocalhostAddressTranslator()
            if all(host in {"localhost", "127.0.0.1"} for host in hosts)
            else None
        ),
    )
    yield cluster
    cluster.shutdown()


@pytest.fixture(scope="session")
def cassandra_session(cassandra_cluster):
    try:
        session = cassandra_cluster.connect()
    except Exception as error:
        pytest.skip(f"Chapter 16 Cassandra is not available: {type(error).__name__}")
    yield session
    session.shutdown()


@pytest.fixture(scope="session")
def neo4j_driver():
    settings = Settings.from_env()
    if not settings.neo4j_password:
        pytest.skip("Set NEO4J_PASSWORD in chapter-16/.env for Neo4j integration tests.")
    driver = GraphDatabase.driver(
        settings.neo4j_uri,
        auth=(settings.neo4j_username, settings.neo4j_password),
        max_connection_pool_size=10,
    )
    try:
        driver.verify_connectivity()
    except Exception as error:
        driver.close()
        pytest.skip(f"Chapter 16 Neo4j is not available: {type(error).__name__}")
    yield driver
    driver.close()


@pytest.fixture(scope="session")
def neo4j_session(neo4j_driver):
    with neo4j_driver.session(database=os.getenv("NEO4J_DATABASE", "neo4j")) as session:
        yield session


@pytest.fixture(scope="session")
def real_connections():
    try:
        connections = DatabaseConnections.connect(Settings.from_env())
    except Exception as error:
        pytest.skip(f"All Chapter 16 databases must be available for this integration test: {type(error).__name__}")
    yield connections
    connections.close()
