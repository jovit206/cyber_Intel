"""Lab 10.1 - Bank transfers with multi-document transactions: 14 payment scenarios.

Run it with the bank servers up:
    docker compose run --rm app lab10.1

Every payment goes through transfer(): one transaction that checks the payer,
debits it, credits the payee and writes a history record. If any step fails the
transaction is aborted and MongoDB undoes the steps that already ran.

Each scenario states the result it expects. After every scenario the script
checks the balances, the history, and that the total money in the bank has not
changed, then prints PASS or FAIL.

Every MongoDB command this script runs, and the reply it gets back, is saved to
lab_10_1_bank_transfer.json next to this file.
"""

import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from bson import json_util
from pymongo import ASCENDING, MongoClient
from pymongo.errors import DuplicateKeyError, PyMongoError
from pymongo.read_concern import ReadConcern
from pymongo.write_concern import WriteConcern

TITLE = "Lab 10.1 - Bank Transfer Transactions: 14 Payment Scenarios"
RS_URI = "mongodb://bank-primary:27017,bank-backup:27017,bank-dr:27017/?replicaSet=rs0"
OUTPUT_FILE = Path(__file__).with_suffix(".json")

client = MongoClient(RS_URI)
bank = client["bank"]
accounts = bank["accounts"]
transfers = bank["transfers"]

steps = []  # the execution log that ends up in the JSON file

# account_id, holder, balance, status, daily_limit
ACCOUNTS = [
    ("ACC-101", "Asha", 5000, "active", 20000),
    ("ACC-102", "Ravi", 3000, "active", 20000),
    ("ACC-103", "Meera", 1000, "active", 20000),
    ("ACC-104", "Kiran", 2000, "frozen", 20000),
    ("ACC-105", "Dev", 8000, "active", 3000),
    ("MERCHANT-01", "Campus Cafe", 0, "active", 50000),
    ("BANK-FEES", "Bank fee account", 0, "active", 0),
]
FEE_RATE = 0.02


class PaymentRejected(Exception):
    """A business rule said no; the transaction is aborted."""


def record(step, command, result):
    steps.append({"step": step, "executedAt": datetime.now(timezone.utc), "command": command, "result": result})


def save_output():
    document = {"activity": TITLE, "script": Path(__file__).name, "steps": steps}
    OUTPUT_FILE.write_text(json_util.dumps(document, indent=2, json_options=json_util.RELAXED_JSON_OPTIONS))
    print(f"\nMongoDB commands and replies saved to {OUTPUT_FILE.name}")


def new_key():
    return f"pay-{uuid.uuid4().hex[:12]}"


def start_of_today():
    now = datetime.now(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def pay(session, payer, payee, amount, key, note, trace, refund_of=None):
    """One payment inside an open transaction. Appends what it did to trace."""
    if not isinstance(amount, (int, float)) or amount <= 0:
        raise PaymentRejected(f"amount must be positive (got {amount})")
    if payer == payee:
        raise PaymentRejected("payer and payee are the same account")

    source = accounts.find_one({"account_id": payer}, session=session)
    if source is None:
        raise PaymentRejected(f"payer account {payer} does not exist")
    if source["status"] != "active":
        raise PaymentRejected(f"payer account {payer} is {source['status']}")

    paid_today = next(transfers.aggregate([
        {"$match": {"from": payer, "at": {"$gte": start_of_today()}, "refund_of": None}},
        {"$group": {"_id": None, "total": {"$sum": "$amount"}}},
    ], session=session), {"total": 0})["total"]
    if refund_of is None and paid_today + amount > source["daily_limit"]:
        raise PaymentRejected(
            f"daily limit {source['daily_limit']:,} exceeded ({paid_today:,} already paid today + {amount:,})"
        )

    # The balance check is part of the filter, so it and the debit are one step.
    debit = accounts.update_one(
        {"account_id": payer, "status": "active", "balance": {"$gte": amount}},
        {"$inc": {"balance": -amount, "version": 1}},
        session=session,
    )
    if debit.modified_count == 0:
        raise PaymentRejected(f"insufficient balance ({source['balance']:,} < {amount:,})")
    trace.append(f"debit  {payer} -{money(amount)}")

    credit = accounts.update_one(
        {"account_id": payee, "status": "active"},
        {"$inc": {"balance": amount, "version": 1}},
        session=session,
    )
    if credit.modified_count == 0:
        raise PaymentRejected(f"payee account {payee} does not exist or is not active")
    trace.append(f"credit {payee} +{money(amount)}")

    transfer_id = f"TXN-{uuid.uuid4().hex[:8].upper()}"
    transfers.insert_one(
        {
            "transfer_id": transfer_id,
            "idempotency_key": key,
            "from": payer,
            "to": payee,
            "amount": amount,
            "note": note,
            "refund_of": refund_of,
            "at": datetime.now(timezone.utc),
        },
        session=session,
    )
    trace.append(f"history {transfer_id}")
    return transfer_id


def run_transaction(work):
    """Run work(session, trace) in one transaction. Returns (outcome, detail, trace)."""
    trace = []
    with client.start_session() as session:
        try:
            with session.start_transaction(
                read_concern=ReadConcern("snapshot"), write_concern=WriteConcern("majority")
            ):
                detail = work(session, trace)
            return "COMMITTED", detail, trace
        except (PaymentRejected, DuplicateKeyError, PyMongoError) as error:
            reason = "duplicate idempotency_key" if isinstance(error, DuplicateKeyError) else str(error)
            # ROLLED BACK = something was written and then undone; REJECTED = nothing was written.
            outcome = "ROLLED BACK" if trace else "REJECTED"
            return outcome, reason, trace


def transfer(payer, payee, amount, key=None, note="payment"):
    return run_transaction(lambda s, t: pay(s, payer, payee, amount, key or new_key(), note, t))


def balances():
    return {a["account_id"]: a["balance"] for a in accounts.find({}, {"_id": 0, "account_id": 1, "balance": 1})}


def total(snapshot):
    return sum(snapshot.values())


def money(value):
    return f"{value:,.0f}" if float(value).is_integer() else f"{value:,.2f}"


# --- the scenarios -----------------------------------------------------------
# Each returns a list of (description, expected outcome, (outcome, detail, trace)).

memo = {}


def s1():
    result = transfer("ACC-101", "ACC-102", 1500, note="rent share")
    memo["payment1"] = result[1]
    return [("Asha pays Ravi 1,500", "COMMITTED", result)]


def s2():
    return [("Meera (1,000) pays Asha 5,000", "REJECTED", transfer("ACC-103", "ACC-101", 5000))]


def s3():
    whole = accounts.find_one({"account_id": "ACC-103"})["balance"]
    return [(f"Meera pays Ravi her whole balance, {whole:,}", "COMMITTED", transfer("ACC-103", "ACC-102", whole))]


def s4():
    return [("Ravi pays 700 to ACC-999, which does not exist", "ROLLED BACK", transfer("ACC-102", "ACC-999", 700))]


def s5():
    return [
        ("Asha pays Ravi 0", "REJECTED", transfer("ACC-101", "ACC-102", 0)),
        ("Asha pays Ravi -500 (would pull 500 from Ravi)", "REJECTED", transfer("ACC-101", "ACC-102", -500)),
    ]


def s6():
    return [("Asha pays Asha 100", "REJECTED", transfer("ACC-101", "ACC-101", 100))]


def s7():
    return [
        ("Kiran (frozen) pays Asha 100", "REJECTED", transfer("ACC-104", "ACC-101", 100)),
        ("Asha pays Kiran (frozen) 100", "ROLLED BACK", transfer("ACC-101", "ACC-104", 100)),
    ]


def s8():
    return [
        ("Dev (limit 3,000/day) pays Asha 2,500", "COMMITTED", transfer("ACC-105", "ACC-101", 2500)),
        ("Dev pays Asha another 1,000 the same day", "REJECTED", transfer("ACC-105", "ACC-101", 1000)),
    ]


def s9():
    key = "order-7731"
    return [
        ("Ravi pays Meera 300, key order-7731", "COMMITTED", transfer("ACC-102", "ACC-103", 300, key=key)),
        ("same request sent again (double click)", "ROLLED BACK", transfer("ACC-102", "ACC-103", 300, key=key)),
    ]


def s10():
    def work(session, trace):
        key = new_key()
        fee = round(500 * FEE_RATE, 2)
        pay(session, "ACC-101", "MERCHANT-01", 500, key, "coffee order", trace)
        pay(session, "MERCHANT-01", "BANK-FEES", fee, key + "-fee", "card fee 2%", trace)
        return f"merchant gets {money(500 - fee)}, fee {money(fee)}"

    return [("Asha pays Campus Cafe 500, 2% fee to BANK-FEES", "COMMITTED", run_transaction(work))]


def s11():
    def work(session, trace):
        for payee in ("ACC-101", "ACC-103", "ACC-999"):
            pay(session, "ACC-102", payee, 200, new_key(), "batch payout", trace)

    return [("Ravi pays 200 each to Asha, Meera and ACC-999", "ROLLED BACK", run_transaction(work))]


def refund(transfer_id):
    def work(session, trace):
        original = transfers.find_one({"transfer_id": transfer_id}, session=session)
        # The key is fixed per original payment, so a second refund collides.
        return pay(session, original["to"], original["from"], original["amount"],
                   f"refund-{transfer_id}", "refund", trace, refund_of=transfer_id)

    return run_transaction(work)


def s12():
    return [
        ("refund payment 1 (Ravi returns 1,500 to Asha)", "COMMITTED", refund(memo["payment1"])),
        ("refund payment 1 again", "ROLLED BACK", refund(memo["payment1"])),
    ]


def open_transactions(session_id):
    """Transactions the server still holds for this session (from $currentOp)."""
    ops = client.admin.aggregate([
        {"$currentOp": {"idleSessions": True, "allUsers": True}},
        {"$match": {"lsid.id": session_id["id"], "transaction": {"$exists": True}}},
    ])
    return len(list(ops))


def s13():
    # The app process "dies": it debits inside a transaction and then never
    # commits, aborts or ends the session. The server must clean up by itself
    # after transactionLifetimeLimitSeconds (60 s by default, 5 s for this test).
    limit = 5
    reply = client.admin.command({"setParameter": 1, "transactionLifetimeLimitSeconds": limit})
    record("Scenario 13: shorten the transaction lifetime",
           {"setParameter": 1, "transactionLifetimeLimitSeconds": limit}, reply)
    app = MongoClient(RS_URI)
    session = app.start_session()
    session.start_transaction(write_concern=WriteConcern("majority"))
    app["bank"]["accounts"].update_one({"account_id": "ACC-101"}, {"$inc": {"balance": -400}}, session=session)
    started = datetime.now(timezone.utc)
    held = open_transactions(session.session_id)
    trace = ["debit  ACC-101 -400", f"app dies; server holds {held} open transaction"]

    while open_transactions(session.session_id) and (datetime.now(timezone.utc) - started).seconds < 90:
        time.sleep(0.5)
    waited = (datetime.now(timezone.utc) - started).total_seconds()
    trace.append(f"server aborted it after {waited:.1f} s")
    client.admin.command({"setParameter": 1, "transactionLifetimeLimitSeconds": 60})
    app.close()

    after = accounts.find_one({"account_id": "ACC-101"})["balance"]
    outcome = "ROLLED BACK" if open_transactions(session.session_id) == 0 else "STILL OPEN"
    return [("Asha pays Meera 400, the app dies after the debit", "ROLLED BACK",
             (outcome, f"Asha still has {after:,}", trace))]


def s14():
    trace = []
    with client.start_session() as session:
        session.start_transaction(read_concern=ReadConcern("snapshot"), write_concern=WriteConcern("majority"))
        pay(session, "ACC-102", "ACC-103", 1000, new_key(), "isolation test", trace)
        outside = accounts.find_one({"account_id": "ACC-102"})["balance"]
        inside = accounts.find_one({"account_id": "ACC-102"}, session=session)["balance"]
        session.commit_transaction()
    after = accounts.find_one({"account_id": "ACC-102"})["balance"]
    trace.append(f"while open: outside reader sees {outside:,}, the transaction sees {inside:,}")
    trace.append(f"after commit: everyone sees {after:,}")
    outcome = "COMMITTED" if outside - 1000 == inside == after else "VISIBLE EARLY"
    return [("Ravi pays Meera 1,000; another reader looks before the commit", "COMMITTED",
             (outcome, "uncommitted debit hidden from other readers", trace))]


SCENARIOS = [
    (1, "Normal payment", s1),
    (2, "Insufficient balance", s2),
    (3, "Pay exactly the whole balance", s3),
    (4, "Payee does not exist", s4),
    (5, "Zero and negative amount", s5),
    (6, "Pay yourself", s6),
    (7, "Frozen payer, frozen payee", s7),
    (8, "Daily limit exceeded", s8),
    (9, "Duplicate submit (idempotency key)", s9),
    (10, "Merchant payment with fee", s10),
    (11, "Batch payout, one payee invalid", s11),
    (12, "Refund, then refund again", s12),
    (13, "App dies mid-payment", s13),
    (14, "Isolation during an open payment", s14),
]


print(TITLE.upper())

print("\nSetup: accounts and history")
accounts.drop()
transfers.drop()
accounts.create_index([("account_id", ASCENDING)], unique=True)
transfers.create_index([("idempotency_key", ASCENDING)], unique=True)
seed = [
    {"account_id": a, "holder": h, "balance": b, "status": s, "daily_limit": d, "version": 0}
    for a, h, b, s, d in ACCOUNTS
]
accounts.insert_many(seed)
record("Setup: seed accounts", {"insert": "accounts", "documents": seed}, {"inserted": len(seed)})
record("Setup: unique index on transfers.idempotency_key",
       {"createIndexes": "transfers", "key": {"idempotency_key": 1}, "unique": True}, {"ok": 1})
for a, h, b, s, d in ACCOUNTS:
    print(f"  {a:<12} {h:<17} {b:>7,}  {s:<7} daily limit {d:,}")
BANK_TOTAL = total(balances())
print(f"  total money in the bank: {BANK_TOTAL:,}")

summary = []
for number, name, scenario in SCENARIOS:
    print(f"\n[{number:>2}] {name}")
    before, history_before = balances(), transfers.count_documents({})
    payments = scenario()
    after, history_after = balances(), transfers.count_documents({})

    passed = True
    for description, expected, (outcome, detail, trace) in payments:
        ok = outcome == expected
        passed &= ok
        print(f"     {description}")
        if trace:
            print(f"       steps: {' | '.join(trace)}")
        print(f"       {outcome}: {detail}   expected {expected}  {'ok' if ok else 'MISMATCH'}")

    changes = [f"{a} {money(before.get(a, 0))} -> {money(v)}" for a, v in after.items() if v != before.get(a)]
    committed = sum(1 for _, _, (o, _, _) in payments if o == "COMMITTED")
    conserved = total(after) == BANK_TOTAL
    no_negative = min(after.values()) >= 0
    passed &= conserved and no_negative
    print(f"     balances: {', '.join(changes) if changes else 'no balance changed'}")
    print(f"     history +{history_after - history_before}   total {money(total(after))} "
          f"{'unchanged' if conserved else 'CHANGED'}   {'no negative balance' if no_negative else 'NEGATIVE BALANCE'}"
          f"   => {'PASS' if passed else 'FAIL'}")
    summary.append((number, name, [e for _, e, _ in payments], [o for _, _, (o, _, _) in payments], passed))
    record(
        f"Scenario {number}: {name}",
        {"payments": [{"description": d, "expected": e} for d, e, _ in payments]},
        {
            "results": [{"outcome": o, "detail": det, "steps": t} for _, _, (o, det, t) in payments],
            "balanceChanges": changes,
            "historyAdded": history_after - history_before,
            "committedPayments": committed,
            "totalMoney": total(after),
            "passed": passed,
        },
    )

print("\nSummary")
print(f"  {'#':>2}  {'Scenario':<36} {'Expected':<26} {'Actual':<26} Result")
for number, name, expected, actual, passed in summary:
    print(f"  {number:>2}  {name:<36} {', '.join(expected):<26} {', '.join(actual):<26} {'PASS' if passed else 'FAIL'}")
passes = sum(1 for *_, p in summary if p)
print(f"  {passes} of {len(summary)} scenarios passed; total money {money(total(balances()))} (started at {BANK_TOTAL:,})")

print("\nFinal balances")
for a in accounts.find({}, {"_id": 0}).sort("account_id"):
    print(f"  {a['account_id']:<12} {a['holder']:<17} {money(a['balance']):>8}   version {a['version']}")
record("Final balances", {"find": "accounts"}, list(accounts.find({}, {"_id": 0}).sort("account_id")))
record("Transfer history", {"find": "transfers"}, list(transfers.find({}, {"_id": 0}).sort("at")))

save_output()
