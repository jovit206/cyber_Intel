from __future__ import annotations

from dataclasses import dataclass

from app.cassandra_support import Cluster, create_cluster
from cassandra.auth import PlainTextAuthProvider
from cassandra.cluster import EXEC_PROFILE_DEFAULT, ExecutionProfile
from cassandra.policies import AddressTranslator
from cassandra.policies import DCAwareRoundRobinPolicy, TokenAwarePolicy
from neo4j import GraphDatabase
from pymongo import MongoClient

from app.config import Settings


class LocalhostAddressTranslator(AddressTranslator):
    def translate(self, address):
        return "127.0.0.1"


@dataclass
class DatabaseConnections:
    mongo_client: MongoClient
    mongo_database: object
    cassandra_cluster: Cluster
    cassandra_session: object
    neo4j_driver: object
    neo4j_database: str

    @classmethod
    def connect(cls, settings: Settings) -> "DatabaseConnections":
        settings.validate()
        mongo_client = MongoClient(
            settings.mongo_uri,
            maxPoolSize=10,
            serverSelectionTimeoutMS=5000,
            appname="chapter16-fraud-service",
        )
        cassandra_options = {}
        if settings.cassandra_username and settings.cassandra_password:
            cassandra_options["auth_provider"] = PlainTextAuthProvider(
                settings.cassandra_username, settings.cassandra_password
            )
        profile = ExecutionProfile(
            load_balancing_policy=TokenAwarePolicy(DCAwareRoundRobinPolicy()),
            request_timeout=5,
        )
        cluster = create_cluster(
            contact_points=list(settings.cassandra_hosts),
            port=settings.cassandra_port,
            executor_threads=4,
            execution_profiles={EXEC_PROFILE_DEFAULT: profile},
            address_translator=(
                LocalhostAddressTranslator()
                if all(host in {"localhost", "127.0.0.1"} for host in settings.cassandra_hosts)
                else None
            ),
            **cassandra_options,
        )
        session = None
        driver = None
        try:
            database = mongo_client[settings.fraud_mongo_database]
            database.transactions.create_index("txn_id", unique=True, name="uq_txn_id")
            session = cluster.connect()
            driver = GraphDatabase.driver(
                settings.neo4j_uri,
                auth=(settings.neo4j_username, settings.neo4j_password),
                max_connection_pool_size=10,
            )
            driver.verify_connectivity()
            return cls(mongo_client, database, cluster, session, driver, settings.neo4j_database)
        except Exception:
            if driver is not None:
                driver.close()
            if session is not None:
                session.shutdown()
            cluster.shutdown()
            mongo_client.close()
            raise

    @property
    def transactions(self):
        return self.mongo_database.transactions

    def close(self) -> None:
        errors = []
        for close in (
            self.neo4j_driver.close,
            self.cassandra_session.shutdown,
            self.cassandra_cluster.shutdown,
            self.mongo_client.close,
        ):
            try:
                close()
            except Exception as error:
                errors.append(error)
        if errors:
            raise RuntimeError(f"Failed to close {len(errors)} database resource(s).") from errors[0]


FRAUD_CASSANDRA_SCHEMA = """
CREATE KEYSPACE IF NOT EXISTS fraud_data
WITH replication = {'class': 'SimpleStrategy', 'replication_factor': 1}
"""

USER_PROFILES_SCHEMA = """
CREATE TABLE IF NOT EXISTS fraud_data.user_profiles (
    user_id text,
    event_time timestamp,
    event_type text,
    transaction_id text,
    amount double,
    location text,
    PRIMARY KEY ((user_id), event_time)
) WITH CLUSTERING ORDER BY (event_time DESC)
"""

FRAUD_NEO4J_SCHEMA = (
    "CREATE CONSTRAINT fraud_user_id_unique IF NOT EXISTS "
    "FOR (u:User) REQUIRE u.id IS UNIQUE",
)


def ensure_fraud_schema(cassandra_session, neo4j_driver, neo4j_database: str) -> None:
    cassandra_session.execute(FRAUD_CASSANDRA_SCHEMA)
    cassandra_session.execute(USER_PROFILES_SCHEMA)
    with neo4j_driver.session(database=neo4j_database) as session:
        for statement in FRAUD_NEO4J_SCHEMA:
            session.run(statement).consume()
