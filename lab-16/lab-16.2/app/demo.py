from __future__ import annotations

from app.config import Settings
from app.database import DatabaseConnections, ensure_fraud_schema
from app.seed_demo import seed_demo
from app.fraud_query_service import FraudQueryService


def main() -> None:
    settings = Settings.from_env()
    connections = DatabaseConnections.connect(settings)
    try:
        ensure_fraud_schema(
            connections.cassandra_session,
            connections.neo4j_driver,
            connections.neo4j_database,
        )
        print("Fraud sample seed:", seed_demo(connections))
        service = FraudQueryService(
            connections.transactions,
            connections.cassandra_session,
            connections.neo4j_driver,
            connections.neo4j_database,
        )
        print(service.analyze_user("USER001", "TXN001", settings.max_hops))
    finally:
        connections.close()


if __name__ == "__main__":
    main()
