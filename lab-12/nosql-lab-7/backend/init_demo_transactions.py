"""Idempotently seed only the separately labeled synthetic demo collection."""
from backend.database import connect_database
from backend.services.demo_transactions import seed

if __name__ == "__main__":
    print(seed(connect_database()))
