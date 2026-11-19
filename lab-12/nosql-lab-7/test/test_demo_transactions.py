import unittest
from datetime import datetime
from backend.services.demo_transactions import COLLECTION, RECORD_COUNT, SOURCE, build_records


class SyntheticTransactionDatasetTests(unittest.TestCase):
    def test_dataset_is_exactly_1400_stable_and_labeled(self):
        first = build_records()
        second = build_records()
        self.assertEqual(len(first), RECORD_COUNT)
        self.assertEqual([row["_id"] for row in first], [row["_id"] for row in second])
        self.assertEqual(len({row["transaction_id"] for row in first}), RECORD_COUNT)
        self.assertTrue(all(row["source"] == SOURCE and row["is_synthetic"] is True for row in first))
        self.assertTrue(all(row["user_id"].startswith("demo-user-") for row in first))
        self.assertTrue(all(isinstance(row["order_date"], datetime) for row in first))
        self.assertEqual(COLLECTION, "demo_financial_activity")
        self.assertFalse(any("balance_after" in row or "ledger_id" in row for row in first))

    def test_seed_is_repeatable_and_uses_only_demo_collection(self):
        from unittest.mock import MagicMock
        from backend.services.demo_transactions import seed
        db = MagicMock()
        coll = MagicMock()
        db.__getitem__.return_value = coll
        coll.count_documents.return_value = RECORD_COUNT
        result = seed(db)
        self.assertEqual(result["total_demo_records"], 1400)
        self.assertFalse(result["real_wallet_collections_touched"])
        self.assertEqual(db.__getitem__.call_args.args[0], COLLECTION)
        self.assertEqual(coll.bulk_write.call_count, 1)

    def test_demo_history_route_is_retired_and_cannot_read_dataset(self):
        import backend.routes.demo_transactions as route
        from fastapi import HTTPException
        self.assertFalse(hasattr(route, "get_database"))
        self.assertFalse(route.health()["available"])
        with self.assertRaises(HTTPException) as raised:
            route.retired_history()
        self.assertEqual(raised.exception.status_code, 410)


if __name__ == "__main__":
    unittest.main()
