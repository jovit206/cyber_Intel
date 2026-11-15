from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")


@dataclass(frozen=True)
class Settings:
    mongo_uri: str
    fraud_mongo_database: str
    cassandra_hosts: tuple[str, ...]
    cassandra_port: int
    cassandra_username: str | None
    cassandra_password: str | None
    neo4j_uri: str
    neo4j_username: str
    neo4j_password: str
    neo4j_database: str
    max_hops: int

    @classmethod
    def from_env(cls) -> "Settings":
        cassandra_username = os.getenv("CASSANDRA_USERNAME") or None
        cassandra_password = os.getenv("CASSANDRA_PASSWORD") or None
        return cls(
            mongo_uri=os.getenv("MONGO_URI", "mongodb://localhost:27020"),
            fraud_mongo_database=os.getenv("FRAUD_MONGO_DATABASE", "fraud_detection"),
            cassandra_hosts=tuple(
                host.strip() for host in os.getenv("CASSANDRA_HOSTS", "localhost").split(",")
                if host.strip()
            ),
            cassandra_port=int(os.getenv("CASSANDRA_PORT", "9043")),
            cassandra_username=cassandra_username,
            cassandra_password=cassandra_password,
            neo4j_uri=os.getenv("NEO4J_URI", "bolt://localhost:7688"),
            neo4j_username=os.getenv("NEO4J_USERNAME", "neo4j"),
            neo4j_password=os.getenv("NEO4J_PASSWORD", ""),
            neo4j_database=os.getenv("NEO4J_DATABASE", "neo4j"),
            max_hops=int(os.getenv("FRAUD_MAX_HOPS", "3")),
        )

    def validate(self) -> None:
        if not self.cassandra_hosts:
            raise ValueError("CASSANDRA_HOSTS must contain at least one host.")
        if not 1 <= self.max_hops <= 5:
            raise ValueError("FRAUD_MAX_HOPS must be between 1 and 5.")
        if not self.neo4j_password:
            raise ValueError("NEO4J_PASSWORD must be set.")
