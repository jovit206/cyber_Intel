from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.database import DatabaseConnections, ensure_fraud_schema
from app.config import Settings


def seed_demo(connections: DatabaseConnections) -> dict[str, int]:
    ensure_fraud_schema(
        connections.cassandra_session,
        connections.neo4j_driver,
        connections.neo4j_database,
    )
    transaction = {
        "txn_id": "TXN001",
        "user_id": "USER001",
        "amount": 5000.0,
        "merchant": "Online Store",
        "timestamp": datetime(2026, 1, 10, 10, 30, tzinfo=timezone.utc),
        "risk_score": 0.85,
        "status": "flagged",
    }
    connections.transactions.replace_one({"txn_id": transaction["txn_id"]}, transaction, upsert=True)
    profile_insert = connections.cassandra_session.prepare("""
        INSERT INTO fraud_data.user_profiles
        (user_id, event_time, event_type, transaction_id, amount, location)
        VALUES (?, ?, ?, ?, ?, ?)
    """)
    now = datetime(2026, 1, 10, 10, 30, tzinfo=timezone.utc)
    for index in range(3):
        connections.cassandra_session.execute(profile_insert, (
            "USER001", now - timedelta(minutes=index), "transaction",
            "TXN001", 5000.0, "US",
        ))
    with connections.neo4j_driver.session(database=connections.neo4j_database) as session:
        session.run("""
            UNWIND $users AS row
            MERGE (u:User {id: row.id})
            SET u.name = row.name, u.risk_level = row.risk_level
        """, users=[
            {"id": "USER001", "name": "User One", "risk_level": "high"},
            {"id": "USER002", "name": "User Two", "risk_level": "medium"},
            {"id": "USER003", "name": "User Three", "risk_level": "low"},
            {"id": "USER004", "name": "User Four", "risk_level": "low"},
        ]).consume()
        session.run("""
            MATCH (a:User {id: 'USER001'}), (b:User {id: 'USER002'}),
                  (c:User {id: 'USER003'}), (d:User {id: 'USER004'})
            MERGE (a)-[:TRANSFERRED_TO]->(b)
            MERGE (b)-[:TRANSFERRED_TO]->(c)
            MERGE (c)-[:TRANSFERRED_TO]->(d)
        """).consume()
    return {"transactions": 1, "events": 3, "users": 4, "transfers": 3}


def main() -> None:
    connections = DatabaseConnections.connect(Settings.from_env())
    try:
        print(seed_demo(connections))
    finally:
        connections.close()


if __name__ == "__main__":
    main()
