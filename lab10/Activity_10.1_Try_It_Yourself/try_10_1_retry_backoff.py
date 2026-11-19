"""Activity 10.1 (Try it yourself) - Retry with exponential backoff on write conflicts.

Run it with the bank servers up:
    docker compose run --rm app try10.1

run_with_retry() reruns the whole transaction when MongoDB labels the error
TransientTransactionError, sleeping 0.1 * 2**attempt seconds in between, and
retries only the commit when the label is UnknownTransactionCommitResult.

Experiments:
  1. A forced write conflict between two payments, shown attempt by attempt.
  2. A double spend: two payments of 700 from a balance of 1,000 at the same moment,
     first without a transaction (read, check, write), then with one.
  3. Stress: 8 threads x 10 payments around a ring of accounts, without and with retry.
The bank-server failover experiment is in failover_experiment.py.

Every MongoDB command this script runs, and the reply it gets back, is saved to
try_10_1_retry_backoff.json next to this file.
"""

import random
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from bson import json_util
from pymongo import ASCENDING, MongoClient
from pymongo.errors import OperationFailure, PyMongoError
from pymongo.read_concern import ReadConcern
from pymongo.write_concern import WriteConcern

TITLE = "Activity 10.1 - Retry with Exponential Backoff"
RS_URI = "mongodb://bank-primary:27017,bank-backup:27017,bank-dr:27017/?replicaSet=rs0"
OUTPUT_FILE = Path(__file__).with_suffix(".json")
MAX_ATTEMPTS = 5

client = MongoClient(RS_URI, maxPoolSize=50)
bank = client["bank_retry"]
accounts = bank["accounts"]
transfers = bank["transfers"]

steps = []  # the execution log that ends up in the JSON file
started = time.perf_counter()


class PaymentRejected(Exception):
    """A business rule said no. Not transient, so never retried."""


def record(step, command, result):
    steps.append({"step": step, "executedAt": datetime.now(timezone.utc), "command": command, "result": result})


def save_output():
    document = {"activity": TITLE, "script": Path(__file__).name, "steps": steps}
    OUTPUT_FILE.write_text(json_util.dumps(document, indent=2, json_options=json_util.RELAXED_JSON_OPTIONS))
    print(f"\nMongoDB commands and replies saved to {OUTPUT_FILE.name}")


def log(message):
    print(f"  +{time.perf_counter() - started:6.2f}s  {message}", flush=True)


def describe(error):
    code = f" code {error.code}" if isinstance(error, OperationFailure) else ""
    labels = [label for label in ("TransientTransactionError", "UnknownTransactionCommitResult")
              if error.has_error_label(label)]
    return f"{type(error).__name__}{code} {labels}"


def pay(session, payer, payee, amount, key):
    debit = accounts.update_one(
        {"account_id": payer, "balance": {"$gte": amount}},
        {"$inc": {"balance": -amount, "version": 1}},
        session=session,
    )
    if debit.modified_count == 0:
        raise PaymentRejected(f"insufficient balance in {payer}")
    accounts.update_one({"account_id": payee}, {"$inc": {"balance": amount, "version": 1}}, session=session)
    transfers.insert_one(
        {"idempotency_key": key, "from": payer, "to": payee, "amount": amount, "at": datetime.now(timezone.utc)},
        session=session,
    )


def commit_with_retry(session):
    while True:
        try:
            session.commit_transaction()
            return
        except PyMongoError as error:
            if error.has_error_label("UnknownTransactionCommitResult"):
                continue  # safe: committing the same transaction twice has no extra effect
            raise


def run_with_retry(work, max_attempts=MAX_ATTEMPTS, on_attempt=None):
    """Run work(session) as a transaction, retrying transient errors with exponential backoff.

    Returns (ok, attempts, seconds slept, last error text).
    """
    slept = 0.0
    for attempt in range(max_attempts):
        try:
            with client.start_session() as session:
                session.start_transaction(read_concern=ReadConcern("snapshot"), write_concern=WriteConcern("majority"))
                work(session)
                commit_with_retry(session)
            if on_attempt:
                on_attempt(attempt + 1, "committed", None)
            return True, attempt + 1, slept, None
        except PaymentRejected as error:
            if on_attempt:
                on_attempt(attempt + 1, f"rejected: {error}", None)
            return False, attempt + 1, slept, str(error)
        except PyMongoError as error:
            last = describe(error)
            if not error.has_error_label("TransientTransactionError") or attempt + 1 == max_attempts:
                if on_attempt:
                    on_attempt(attempt + 1, f"failed: {last}", None)
                return False, attempt + 1, slept, last
            delay = 0.1 * 2 ** attempt
            if on_attempt:
                on_attempt(attempt + 1, f"{last} -> retry in {delay:.1f} s", delay)
            time.sleep(delay)
            slept += delay


def seed(balances):
    accounts.drop()
    transfers.drop()
    transfers.create_index([("idempotency_key", ASCENDING)], unique=True)
    docs = [{"account_id": a, "balance": b, "version": 0} for a, b in balances.items()]
    accounts.insert_many(docs)
    return docs


def balance_of(account_id):
    return accounts.find_one({"account_id": account_id})["balance"]


def total():
    return sum(a["balance"] for a in accounts.find())


print(TITLE.upper())

# --- 1. forced write conflict -----------------------------------------------
print("\nExperiment 1: a forced write conflict, retried with backoff")
seed({"ACC-201": 5000, "ACC-202": 0, "ACC-203": 0})
log("transaction A: Asha (ACC-201) pays ACC-202 100, debit applied, A stays open")
session_a = client.start_session()
session_a.start_transaction(write_concern=WriteConcern("majority"))
pay(session_a, "ACC-201", "ACC-202", 100, "A")

attempts_b = []


def b_attempt(n, text, delay):
    attempts_b.append({"attempt": n, "result": text, "backoffSeconds": delay})
    log(f"transaction B attempt {n}: {text}")


def payment_b():
    run_with_retry(lambda s: pay(s, "ACC-201", "ACC-203", 200, "B"), on_attempt=b_attempt)


log("transaction B: Asha pays ACC-203 200 from the same account")
thread_b = threading.Thread(target=payment_b)
thread_b.start()
time.sleep(0.65)
session_a.commit_transaction()
session_a.end_session()
log("transaction A commits")
thread_b.join()
final_1 = {a: balance_of(a) for a in ("ACC-201", "ACC-202", "ACC-203")}
log(f"balances: ACC-201 {final_1['ACC-201']:,}  ACC-202 {final_1['ACC-202']:,}  ACC-203 {final_1['ACC-203']:,}"
    f"  (both payments applied once: {final_1 == {'ACC-201': 4700, 'ACC-202': 100, 'ACC-203': 200}})")
record("Experiment 1: forced write conflict",
       {"transactionA": "debit ACC-201 100, held open 0.65 s", "transactionB": "debit ACC-201 200 with retry"},
       {"attemptsB": attempts_b, "balances": final_1})

# --- 2. double spend ---------------------------------------------------------
print("\nExperiment 2: double spend - two payments of 700 from a balance of 1,000 at the same moment")


def naive_payment(payee, barrier, results):
    """Read, check, write back: no transaction, no conditional update."""
    barrier.wait()
    balance = accounts.find_one({"account_id": "ACC-211"})["balance"]
    if balance >= 700:
        time.sleep(0.05)  # the app does its other checks
        accounts.update_one({"account_id": "ACC-211"}, {"$set": {"balance": balance - 700}})
        accounts.update_one({"account_id": payee}, {"$inc": {"balance": 700}})
        results[payee] = "paid"
    else:
        results[payee] = "refused"


def transactional_payment(payee, barrier, results):
    barrier.wait()
    ok, attempts, _, error = run_with_retry(lambda s: pay(s, "ACC-211", payee, 700, f"double-{payee}"))
    results[payee] = f"{'committed' if ok else error} ({attempts} attempt{'s' if attempts > 1 else ''})"


for label, payment in (("2a without a transaction (read, check, write)", naive_payment),
                       ("2b with a transaction and retry", transactional_payment)):
    seed({"ACC-211": 1000, "ACC-212": 0, "ACC-213": 0})
    barrier, results = threading.Barrier(2), {}
    threads = [threading.Thread(target=payment, args=(p, barrier, results)) for p in ("ACC-212", "ACC-213")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    payer, received = balance_of("ACC-211"), balance_of("ACC-212") + balance_of("ACC-213")
    print(f"  {label}")
    for payee in ("ACC-212", "ACC-213"):
        print(f"    payment to {payee}: {results[payee]}")
    verdict = "money created out of nothing" if payer + received != 1000 else "books balance"
    print(f"    payer left with {payer:,}, payees received {received:,}, total {payer + received:,} -> {verdict}")
    record(f"Experiment 2: double spend, {label}",
           {"payer": "ACC-211 balance 1000", "payments": "2 x 700 at the same instant"},
           {"results": results, "payerBalance": payer, "received": received, "total": payer + received})

# --- 3. stress ---------------------------------------------------------------
print("\nExperiment 3: stress - 8 threads x 10 payments around a ring of 4 accounts")
RING = ["ACC-221", "ACC-222", "ACC-223", "ACC-224"]
print(f"  {'Mode':<16} {'Committed':>9} {'Failed':>7} {'Attempts':>9} {'Retries':>8} {'Backoff (s)':>11} {'Time (s)':>9}  Total")
for mode, attempts_allowed in (("without retry", 1), ("with retry", MAX_ATTEMPTS)):
    seed({a: 10000 for a in RING})
    stats = {"committed": 0, "failed": 0, "attempts": 0, "slept": 0.0}
    lock = threading.Lock()

    def worker(thread_no):
        rng = random.Random(thread_no)
        for i in range(10):
            payer = RING[(thread_no + i) % 4]
            payee = RING[(thread_no + i + 1) % 4]
            key = f"{mode}-{thread_no}-{i}-{uuid.uuid4().hex[:6]}"
            amount = rng.randint(10, 100)
            ok, attempts, slept, _ = run_with_retry(lambda s: pay(s, payer, payee, amount, key), attempts_allowed)
            with lock:
                stats["committed" if ok else "failed"] += 1
                stats["attempts"] += attempts
                stats["slept"] += slept

    t0 = time.perf_counter()
    threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    elapsed = time.perf_counter() - t0
    ring_total = total()
    print(f"  {mode:<16} {stats['committed']:>9} {stats['failed']:>7} {stats['attempts']:>9} "
          f"{stats['attempts'] - stats['committed'] - stats['failed']:>8} {stats['slept']:>11.1f} {elapsed:>9.2f}  "
          f"{ring_total:,} {'(conserved)' if ring_total == 40000 else '(CHANGED)'}")
    record(f"Experiment 3: stress, {mode}",
           {"threads": 8, "paymentsPerThread": 10, "maxAttempts": attempts_allowed},
           {**stats, "seconds": round(elapsed, 2), "ringTotal": ring_total,
            "historyRecords": transfers.count_documents({})})

save_output()
