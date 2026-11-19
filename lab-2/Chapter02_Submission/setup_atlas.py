"""
Wires a MongoDB Atlas connection string into every .env this project uses,
after proving the string actually works.

Usage:
    python setup_atlas.py "mongodb+srv://user:pass@cluster.mongodb.net/?retryWrites=true&w=majority"

It will:
  1. sanity-check the URI's shape and warn about the classic mistakes
  2. connect, ping, and print the cluster's server_info() + topology
  3. write MONGO_URI_ATLAS into  Chapter02_Submission/.env
     and into  NoSQL-Databases-using-MongoDB/.env  (if that repo is present)
"""

import re
import sys
from pathlib import Path
from urllib.parse import quote_plus, urlsplit

import certifi
from pymongo import MongoClient
from pymongo.errors import PyMongoError

HERE = Path(__file__).resolve().parent
TARGET_ENVS = [
    HERE / ".env",
    HERE.parent / "NoSQL-Databases-using-MongoDB" / ".env",
]


def precheck(uri):
    """Catch the mistakes that account for most Atlas connection failures."""
    problems = []
    if not uri.startswith(("mongodb+srv://", "mongodb://")):
        problems.append("URI must start with mongodb+srv:// (Atlas) or mongodb://")
    if "<password>" in uri or "<db_password>" in uri:
        problems.append("The literal placeholder <password> is still in the string - "
                        "replace it with your real database-user password.")
    if "<username>" in uri or "<db_username>" in uri:
        problems.append("The literal placeholder <username> is still in the string.")

    # An un-escaped @ / : / # / ? inside the password breaks URI parsing.
    m = re.match(r"mongodb(?:\+srv)?://([^@]*)@", uri)
    if m and ":" in m.group(1):
        _, pwd = m.group(1).split(":", 1)
        if pwd and quote_plus(pwd) != pwd:
            problems.append(
                f"The password contains characters that must be percent-encoded. "
                f"Replace it with: {quote_plus(pwd)}")
    return problems


def describe(client):
    info = client.server_info()
    print(f"  Server version   : {info['version']}")
    print(f"  Modules          : {', '.join(info.get('modules', [])) or '(none)'}")
    print(f"  Max BSON size    : {info.get('maxBsonObjectSize')}")
    try:
        hello = client.admin.command("hello")
        print(f"  Topology         : {'replica set' if hello.get('setName') else 'standalone'}")
        print(f"  Replica set name : {hello.get('setName')}")
        hosts = hello.get("hosts", [])
        print(f"  Nodes            : {len(hosts)}")
        for h in hosts:
            print(f"      - {h}")
    except PyMongoError:
        pass
    print(f"  Databases        : {', '.join(client.list_database_names())}")


def write_env(path, uri):
    """Insert or replace MONGO_URI_ATLAS in one .env file."""
    if not path.parent.exists():
        print(f"  [skip] {path} (directory not present)")
        return
    lines = path.read_text().splitlines() if path.exists() else []
    out, replaced = [], False
    for line in lines:
        stripped = line.lstrip("# ").strip()
        if stripped.startswith("MONGO_URI_ATLAS="):
            if not replaced:
                out.append(f"MONGO_URI_ATLAS={uri}")
                replaced = True
        else:
            out.append(line)
    if not replaced:
        out += ["", "# MongoDB Atlas cluster (written by setup_atlas.py)",
                f"MONGO_URI_ATLAS={uri}"]
    path.write_text("\n".join(out).rstrip() + "\n")
    print(f"  [OK] {path}")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    uri = sys.argv[1].strip().strip('"').strip("'")

    print("=" * 70)
    print(" Validating Atlas connection string")
    print("=" * 70)

    problems = precheck(uri)
    if problems:
        print("\n[ERROR] The connection string looks wrong:\n")
        for p in problems:
            print(f"  * {p}")
        sys.exit(1)

    host = urlsplit(uri.replace("mongodb+srv://", "https://")).hostname
    print(f"  Cluster host     : {host}")

    print("\nConnecting...")
    try:
        client = MongoClient(uri, serverSelectionTimeoutMS=15000,
                             tlsCAFile=certifi.where())
        client.admin.command("ping")
        print("[OK] Connected to Atlas\n")
        describe(client)
        client.close()
    except PyMongoError as exc:
        print(f"[FAIL] {type(exc).__name__}: {exc}\n")
        print("Most common causes, in order:")
        print("  1. Your IP is not in Atlas -> Network Access. Add your current IP,")
        print("     or 0.0.0.0/0 for lab purposes only.")
        print("  2. Wrong database-user password (this is NOT your Atlas login password -")
        print("     it is the user under Atlas -> Database Access).")
        print("  3. The cluster is still provisioning - wait for it to show 'Active'.")
        sys.exit(1)

    print("\n" + "=" * 70)
    print(" Writing MONGO_URI_ATLAS into .env files")
    print("=" * 70)
    for path in TARGET_ENVS:
        write_env(path, uri)

    print("\nNext:")
    print("  cd Activity_2.1                 && python activity_2_1_setup_verification.py")
    print("  cd Activity_2.1_Try_It_Yourself && python activity_2_1_try_it_yourself.py")
    print("  python build_report.py")


if __name__ == "__main__":
    main()
