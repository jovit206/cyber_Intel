from pymongo import MongoClient
from pymongo.database import Database
from urllib.parse import parse_qs, urlsplit

from backend.config import settings

client: MongoClient | None = None
database: Database | None = None


def mongo_tls_options(uri: str) -> dict[str, str]:
    """Use mounted trust/client certificates without weakening MongoDB TLS validation."""
    try:
        uri_options = {key.lower() for key in parse_qs(urlsplit(uri).query)}
    except Exception:
        uri_options = set()
    options: dict[str, str] = {}
    if settings.mongo_tls_ca_file and "tlscafile" not in uri_options:
        options["tlsCAFile"] = settings.mongo_tls_ca_file
    if settings.mongo_tls_client_certificate_file and "tlscertificatekeyfile" not in uri_options:
        options["tlsCertificateKeyFile"] = settings.mongo_tls_client_certificate_file
    return options


def connect_database() -> Database:
    global client, database
    if client is None:
        client = MongoClient(settings.mongo_uri, serverSelectionTimeoutMS=5000,
                             **mongo_tls_options(settings.mongo_uri))
    client.admin.command("ping")
    database = client[settings.mongo_database]
    return database


def get_database() -> Database:
    if database is None:
        raise RuntimeError("MongoDB is not connected")
    return database


def close_database() -> None:
    global client, database
    if client is not None:
        client.close()
    client = None
    database = None
