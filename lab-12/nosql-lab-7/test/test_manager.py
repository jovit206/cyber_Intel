import unittest
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.routes import manager


class ManagerReadOnlyApiTests(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(manager.router)
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()

    @patch("backend.routes.manager.get_database")
    def test_manager_can_read_records_without_auth_secrets(self, get_database):
        db = MagicMock()
        db.subscription_sessions.find_one.return_value = {"user_id": "manager"}
        db.subscription_users.find_one.return_value = {"user_id": "manager", "role": "manager"}
        db.subscription_users.find.return_value.sort.return_value.limit.return_value = []
        get_database.return_value = db
        response = self.client.get("/api/manager/users", cookies={"aegis_session": "manager-session"})
        self.assertEqual(response.status_code, 200)
        db.subscription_users.find.assert_called_once()

    @patch("backend.routes.manager.get_database")
    def test_manager_can_read_subscription_and_transaction_details(self, get_database):
        db = MagicMock()
        db.subscription_sessions.find_one.return_value = {"user_id": "manager"}
        db.subscription_users.find_one.side_effect = [
            {"user_id": "manager", "role": "manager"},
            {"user_id": "manager", "role": "manager"},
        ]
        db.wallet_transactions.find_one.return_value = {"transaction_id": "tx-1", "user_id": "u-1"}
        get_database.return_value = db
        cookie = {"aegis_session": "manager-session"}
        self.assertEqual(self.client.get("/api/manager/subscriptions", cookies=cookie).status_code, 200)
        self.assertEqual(self.client.get("/api/manager/transactions/tx-1", cookies=cookie).status_code, 200)

    @patch("backend.routes.manager.get_database")
    def test_admin_cannot_use_manager_read_routes(self, get_database):
        db = MagicMock()
        db.subscription_sessions.find_one.return_value = {"user_id": "admin"}
        db.subscription_users.find_one.return_value = {"user_id": "admin", "role": "admin"}
        get_database.return_value = db
        response = self.client.get("/api/manager/users", cookies={"aegis_session": "admin-session"})
        self.assertEqual(response.status_code, 403)
        db.subscription_users.find.assert_not_called()


if __name__ == "__main__":
    unittest.main()
