from __future__ import annotations

import logging
import time

from app.config import Settings
from app.database import DatabaseConnections

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def main() -> None:
    settings = Settings.from_env()
    deadline = time.monotonic() + 180
    while True:
        try:
            connections = DatabaseConnections.connect(settings)
            connections.close()
            logging.info("MongoDB, Cassandra, and Neo4j are ready.")
            return
        except Exception as error:
            if time.monotonic() >= deadline:
                raise RuntimeError("Timed out waiting for Chapter 16 databases.") from error
            logging.info("Database dependencies are not ready yet (%s); retrying shortly.", type(error).__name__)
            time.sleep(5)


if __name__ == "__main__":
    main()
