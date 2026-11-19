from __future__ import annotations

import logging
import os
import re
import threading
from dataclasses import dataclass, field
from typing import Any

from pymongo.database import Database

logger = logging.getLogger("aegis.chapter18")
_IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,47}$")


class Chapter18ConfigurationError(RuntimeError):
    pass


class Chapter18DependencyError(RuntimeError):
    pass


def _identifier(name: str, value: str) -> str:
    if not _IDENTIFIER.fullmatch(value):
        raise Chapter18ConfigurationError(
            f"{name} must start with a letter and contain only letters, numbers, or underscores."
        )
    return value


@dataclass(frozen=True)
class Chapter18Settings:
    mongo_database: str = "threat_intel_lab18"
    cassandra_hosts: tuple[str, ...] = ()
    cassandra_port: int = 9042
    cassandra_keyspace: str = "threat_intel_lab18"
    cassandra_datacenter: str = "datacenter1"
    cassandra_replication_factor: int = 1
    cassandra_username: str = ""
    cassandra_password: str = field(default="", repr=False)
    neo4j_uri: str = ""
    neo4j_username: str = "neo4j"
    neo4j_password: str = field(default="", repr=False)
    neo4j_database: str = "neo4j"

    @classmethod
    def from_env(cls) -> "Chapter18Settings":
        hosts = tuple(
            item.strip()
            for item in os.getenv("LAB18_CASSANDRA_HOSTS", "").split(",")
            if item.strip()
        )
        try:
            cassandra_port = int(os.getenv("LAB18_CASSANDRA_PORT", "9042"))
            replication_factor = int(os.getenv("LAB18_CASSANDRA_REPLICATION_FACTOR", "1"))
        except ValueError as error:
            raise Chapter18ConfigurationError(
                "Cassandra port and replication factor must be integers."
            ) from error
        if not 1 <= cassandra_port <= 65535:
            raise Chapter18ConfigurationError("LAB18_CASSANDRA_PORT is out of range.")
        if replication_factor < 1:
            raise Chapter18ConfigurationError(
                "LAB18_CASSANDRA_REPLICATION_FACTOR must be positive."
            )
        return cls(
            mongo_database=_identifier(
                "LAB18_MONGO_DATABASE",
                os.getenv("LAB18_MONGO_DATABASE", "threat_intel_lab18").strip(),
            ),
            cassandra_hosts=hosts,
            cassandra_port=cassandra_port,
            cassandra_keyspace=_identifier(
                "LAB18_CASSANDRA_KEYSPACE",
                os.getenv("LAB18_CASSANDRA_KEYSPACE", "threat_intel_lab18").strip(),
            ),
            cassandra_datacenter=_identifier(
                "LAB18_CASSANDRA_DATACENTER",
                os.getenv("LAB18_CASSANDRA_DATACENTER", "datacenter1").strip(),
            ),
            cassandra_replication_factor=replication_factor,
            cassandra_username=os.getenv("LAB18_CASSANDRA_USERNAME", "").strip(),
            cassandra_password=os.getenv("LAB18_CASSANDRA_PASSWORD", ""),
            neo4j_uri=os.getenv("LAB18_NEO4J_URI", "").strip(),
            neo4j_username=os.getenv("LAB18_NEO4J_USERNAME", "neo4j").strip(),
            neo4j_password=os.getenv("LAB18_NEO4J_PASSWORD", ""),
            neo4j_database=os.getenv("LAB18_NEO4J_DATABASE", "neo4j").strip(),
        )


class Chapter18Connections:
    def __init__(self, settings: Chapter18Settings | None = None) -> None:
        self.configuration_error: str | None = None
        try:
            self.settings = settings or Chapter18Settings.from_env()
        except Chapter18ConfigurationError as error:
            self.settings = Chapter18Settings()
            self.configuration_error = str(error)
        self._lock = threading.Lock()
        self._cassandra_cluster: Any | None = None
        self._cassandra_session: Any | None = None
        self._neo4j_driver: Any | None = None

    def _require_valid_configuration(self) -> None:
        if self.configuration_error:
            raise Chapter18ConfigurationError(self.configuration_error)

    def mongo_database(self, application_database: Database) -> Database:
        self._require_valid_configuration()
        return application_database.client[self.settings.mongo_database]

    def cassandra_session(self) -> Any:
        self._require_valid_configuration()
        if not self.settings.cassandra_hosts:
            raise Chapter18ConfigurationError(
                "Set LAB18_CASSANDRA_HOSTS to enable Cassandra."
            )
        with self._lock:
            if self._cassandra_session is not None:
                return self._cassandra_session
            try:
                from cassandra.auth import PlainTextAuthProvider
                from cassandra.cluster import Cluster, NoHostAvailable
                from cassandra import DriverException
            except ImportError as error:
                raise Chapter18DependencyError(
                    "Install lab-18/requirements-chapter18.txt to enable Cassandra."
                ) from error

            options: dict[str, Any] = {}
            if self.settings.cassandra_username or self.settings.cassandra_password:
                if not self.settings.cassandra_username or not self.settings.cassandra_password:
                    raise Chapter18ConfigurationError(
                        "Set both Cassandra username and password, or leave both empty."
                    )
                options["auth_provider"] = PlainTextAuthProvider(
                    self.settings.cassandra_username, self.settings.cassandra_password
                )
            cluster = Cluster(
                contact_points=list(self.settings.cassandra_hosts),
                port=self.settings.cassandra_port,
                **options,
            )
            try:
                session = cluster.connect()
            except (DriverException, NoHostAvailable, OSError, ValueError):
                cluster.shutdown()
                raise
            self._cassandra_cluster = cluster
            self._cassandra_session = session
            return session

    def neo4j_driver(self) -> Any:
        self._require_valid_configuration()
        if not self.settings.neo4j_uri:
            raise Chapter18ConfigurationError("Set LAB18_NEO4J_URI to enable Neo4j.")
        if not self.settings.neo4j_username or not self.settings.neo4j_password:
            raise Chapter18ConfigurationError(
                "Set LAB18_NEO4J_USERNAME and LAB18_NEO4J_PASSWORD to enable Neo4j."
            )
        with self._lock:
            if self._neo4j_driver is not None:
                return self._neo4j_driver
            try:
                from neo4j import GraphDatabase
            except ImportError as error:
                raise Chapter18DependencyError(
                    "Install lab-18/requirements-chapter18.txt to enable Neo4j."
                ) from error
            self._neo4j_driver = GraphDatabase.driver(
                self.settings.neo4j_uri,
                auth=(self.settings.neo4j_username, self.settings.neo4j_password),
            )
            return self._neo4j_driver

    def close(self) -> None:
        with self._lock:
            if self._cassandra_session is not None:
                self._cassandra_session.shutdown()
                self._cassandra_session = None
            if self._cassandra_cluster is not None:
                self._cassandra_cluster.shutdown()
                self._cassandra_cluster = None
            if self._neo4j_driver is not None:
                self._neo4j_driver.close()
                self._neo4j_driver = None
