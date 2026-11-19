from __future__ import annotations

import logging
from typing import Any

from app.circuit_breaker import CircuitBreaker

logger = logging.getLogger(__name__)
RECENT_EVENTS_QUERY = """
SELECT user_id, event_time, event_type, transaction_id, amount, location
FROM fraud_data.user_profiles
WHERE user_id = ?
ORDER BY event_time DESC
LIMIT 10
"""


class FraudQueryService:
    def __init__(
        self,
        mongo_transactions,
        cassandra_session,
        neo4j_driver,
        neo4j_database: str = "neo4j",
        circuit_breaker: CircuitBreaker | None = None,
    ):
        self.mongo_transactions = mongo_transactions
        self.cassandra_session = cassandra_session
        self.neo4j_driver = neo4j_driver
        self.neo4j_database = neo4j_database
        self.circuit_breaker = circuit_breaker or CircuitBreaker()
        self._recent_events_statement = None

    def get_transaction(self, txn_id: str) -> dict[str, Any] | None:
        document = self.circuit_breaker.call(
            "mongodb", self.mongo_transactions.find_one, {"txn_id": txn_id}, {"_id": 0}
        )
        return dict(document) if document is not None else None

    def get_user_profile(self, user_id: str) -> list[dict[str, Any]]:
        def execute():
            if self._recent_events_statement is None:
                self._recent_events_statement = self.cassandra_session.prepare(RECENT_EVENTS_QUERY)
            return self.cassandra_session.execute(self._recent_events_statement, (user_id,))

        rows = self.circuit_breaker.call("cassandra", execute)
        return [self._row_to_dict(row) for row in rows]

    def detect_fraud_rings(self, user_id: str, max_hops: int = 3) -> list[dict[str, Any]]:
        if not isinstance(max_hops, int) or isinstance(max_hops, bool) or not 1 <= max_hops <= 5:
            raise ValueError("max_hops must be an integer between 1 and 5.")
        query = f"""
        MATCH p=(u:User {{id: $user_id}})-[:TRANSFERRED_TO*1..{max_hops}]->(connected:User)
        RETURN [node IN nodes(p) | node.id] AS path
        LIMIT 50
        """

        def execute():
            with self.neo4j_driver.session(database=self.neo4j_database) as session:
                return [record["path"] for record in session.run(query, user_id=user_id)]

        paths = self.circuit_breaker.call("neo4j", execute)[:50]
        return [{"path": path, "connected_user_id": path[-1]} for path in paths if len(path) > 1]

    def analyze_user(self, user_id: str, txn_id: str, max_hops: int = 3) -> dict[str, Any]:
        if not isinstance(max_hops, int) or isinstance(max_hops, bool) or not 1 <= max_hops <= 5:
            raise ValueError("max_hops must be an integer between 1 and 5.")
        result = {
            "user_id": user_id,
            "transaction": None,
            "recent_events": [],
            "connected_accounts": [],
            "transfer_paths": [],
            "fraud_indicators": [],
            "unavailable_backends": [],
            "backend_errors": {},
            "analysis_status": "completed",
        }
        operations = (
            ("mongodb", lambda: self.get_transaction(txn_id), "transaction"),
            ("cassandra", lambda: self.get_user_profile(user_id), "recent_events"),
            ("neo4j", lambda: self.detect_fraud_rings(user_id, max_hops), "paths"),
        )
        for backend, operation, field in operations:
            try:
                value = operation()
            except Exception as error:
                logger.exception("Fraud analysis backend %s failed", backend)
                result["unavailable_backends"].append(backend)
                result["backend_errors"][backend] = type(error).__name__
                continue
            if field == "paths":
                result["transfer_paths"] = [item["path"] for item in value]
                result["connected_accounts"] = sorted({
                    item["connected_user_id"] for item in value
                })
            else:
                result[field] = value
        if result["unavailable_backends"]:
            result["analysis_status"] = "partial"
        transaction = result["transaction"]
        if transaction:
            try:
                if transaction.get("status") == "flagged" or float(transaction.get("risk_score", 0)) >= 0.8:
                    result["fraud_indicators"].append("transaction_flagged_or_high_risk")
            except (TypeError, ValueError):
                result["fraud_indicators"].append("transaction_risk_score_invalid")
        if result["transfer_paths"]:
            result["fraud_indicators"].append("connected_transfer_paths_found")
        return result

    @staticmethod
    def _row_to_dict(row) -> dict[str, Any]:
        if hasattr(row, "_asdict"):
            return dict(row._asdict())
        if isinstance(row, dict):
            return dict(row)
        return dict(row)
