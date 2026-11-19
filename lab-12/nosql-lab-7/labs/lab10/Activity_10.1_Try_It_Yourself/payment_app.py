import time
from dataclasses import dataclass, field

from pymongo import ASCENDING, MongoClient
from pymongo.errors import ConnectionFailure, DuplicateKeyError, PyMongoError
from pymongo.read_concern import ReadConcern
from pymongo.write_concern import WriteConcern

RS_URI = "mongodb://bank-primary:27017,bank-backup:27017,bank-dr:27017/?replicaSet=rs0"
MEMBERS = ["bank-primary:27017", "bank-backup:27017", "bank-dr:27017"]
RING = ["ACC-301", "ACC-302", "ACC-303", "ACC-304"]
OPENING_BALANCE = 100_000
AMOUNT = 10

# Short timeouts, so "no primary" shows up as an error to retry instead of a 30 s
# wait, and a 1 s heartbeat, so the driver notices a new primary quickly.
client = MongoClient(RS_URI, serverSelectionTimeoutMS=3000, connectTimeoutMS=2000, socketTimeoutMS=20000,
                     heartbeatFrequencyMS=1000)
bank = client["bank_failover"]
accounts = bank["accounts"]
transfers = bank["transfers"]

# One direct connection per member for the observer.
direct = {
    host: MongoClient(f"mongodb://{host}/?directConnection=true",
                      serverSelectionTimeoutMS=800, connectTimeoutMS=800, socketTimeoutMS=2000)
    for host in MEMBERS
}


@dataclass
class Payment:
    number: int
    key: str
    outcome: str = ""          # committed | already paid | refused | unknown
    server: str = ""
    attempts: int = 0
    seconds: float = 0.0
    errors: list = field(default_factory=list)


def setup():
    accounts.drop()
    transfers.drop()
    transfers.create_index([("idempotency_key", ASCENDING)], unique=True)
    accounts.insert_many([{"account_id": a, "balance": OPENING_BALANCE, "version": 0} for a in RING])


def short(host):
    return host.split(":")[0] if host else "none"


def error_name(error):
    names = [type(error).__name__]
    names += [label for label in ("TransientTransactionError", "UnknownTransactionCommitResult")
              if error.has_error_label(label)]
    return "/".join(names)


def retryable(error):
    return (error.has_error_label("TransientTransactionError")
            or error.has_error_label("UnknownTransactionCommitResult")
            or isinstance(error, ConnectionFailure))


def pay_once(payment, payer, payee, deadline):
    """One attempt: the whole payment as one transaction. Returns the server that ran it."""
    with client.start_session() as session:
        session.start_transaction(read_concern=ReadConcern("snapshot"), write_concern=WriteConcern("majority"))
        cursor = accounts.find({"account_id": payer}, session=session).limit(1)
        next(cursor)
        server = f"{cursor.address[0]}:{cursor.address[1]}"  # transactions run on the primary
        accounts.update_one({"account_id": payer, "balance": {"$gte": AMOUNT}},
                            {"$inc": {"balance": -AMOUNT, "version": 1}}, session=session)
        accounts.update_one({"account_id": payee}, {"$inc": {"balance": AMOUNT, "version": 1}}, session=session)
        transfers.insert_one({"idempotency_key": payment.key, "number": payment.number, "from": payer,
                              "to": payee, "amount": AMOUNT, "server": server}, session=session)
        while True:
            try:
                session.commit_transaction()
                return server
            except PyMongoError as error:
                if error.has_error_label("UnknownTransactionCommitResult") and time.time() < deadline:
                    payment.errors.append(error_name(error))
                    time.sleep(0.5)
                    continue
                raise


def send_payment(number, budget=30.0):
    payer, payee = RING[number % 4], RING[(number + 1) % 4]
    payment = Payment(number, f"pay-{number:04d}")
    t0 = time.time()
    deadline = t0 + budget
    while True:
        payment.attempts += 1
        try:
            payment.server = pay_once(payment, payer, payee, deadline)
            payment.outcome = "committed"
            break
        except DuplicateKeyError:
            # An earlier attempt did commit; the index stopped a second charge.
            payment.outcome = "already paid"
            break
        except PyMongoError as error:
            payment.errors.append(error_name(error))
            if not retryable(error) or time.time() >= deadline:
                # If the last commit got no answer, the payment may or may not have
                # gone through: the client cannot say "refused", only "unknown".
                unknown = error.has_error_label("UnknownTransactionCommitResult")
                payment.outcome = "unknown" if unknown else "refused"
                break
            time.sleep(min(2.0, 0.1 * 2 ** (payment.attempts - 1)))
    payment.seconds = time.time() - t0
    return payment


def replica_status():
    """rs.status() as the primary sees it, or as any reachable member sees it."""
    fallback = None
    for host in MEMBERS:
        try:
            status = direct[host].admin.command("replSetGetStatus")
        except PyMongoError:
            continue
        if status["myState"] == 1:
            return status
        fallback = fallback or status
    return fallback


def primary_of(status):
    return next((m["name"] for m in status["members"] if m["stateStr"] == "PRIMARY"), None) if status else None


def one_line(status):
    if status is None:
        return "no bank server answered"
    states = "  ".join(f"{short(m['name'])}={m['stateStr']}" for m in status["members"])
    return f"primary={short(primary_of(status)) if primary_of(status) else 'NONE'}  |  {states}"


def summarize_errors(errors):
    counts = {}
    for name in errors:
        counts[name] = counts.get(name, 0) + 1
    return ", ".join(f"{name} x{n}" if n > 1 else name for name, n in counts.items())
