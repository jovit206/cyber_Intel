"""Lab 10.2 - Inventory reservation with transactions, write conflicts and retries.

Run it with the bank servers up:
    docker compose run --rm app lab10.2

reserve_inventory() reads the product, checks the stock, decrements it with
optimistic concurrency (the update matches only the version it read) and creates
a pending order, all in one transaction.

  Part 1  8 customers want the last 5 laptops at the same moment, no retry
  Part 2  the same with retry (5 attempts, short jittered backoff)
  Part 3  throughput at 1, 4, 8 and 16 threads: transaction vs findAndModify
  Part 4  the order insert fails after the stock was taken: with and without a transaction
  Part 5  checkout with payment: stock, wallet and order in one transaction

Every MongoDB command this script runs, and the reply it gets back, is saved to
lab_10_2_inventory_reservation.json next to this file. The Part 3 chart is saved
as throughput.png.
"""

import random
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from bson import json_util  # noqa: E402
from pymongo import ASCENDING, MongoClient, ReturnDocument  # noqa: E402
from pymongo.errors import DuplicateKeyError, OperationFailure, PyMongoError  # noqa: E402
from pymongo.read_concern import ReadConcern  # noqa: E402
from pymongo.write_concern import WriteConcern  # noqa: E402

TITLE = "Lab 10.2 - Inventory Reservation with Deadlock Handling"
RS_URI = "mongodb://bank-primary:27017,bank-backup:27017,bank-dr:27017/?replicaSet=rs0"
OUTPUT_FILE = Path(__file__).with_suffix(".json")
CHART_FILE = Path(__file__).with_name("throughput.png")
PRODUCT_ID = "LAPTOP-15"
PRICE = 55000.0
THREAD_LEVELS = [1, 4, 8, 16]
SECONDS_PER_LEVEL = 3.0

client = MongoClient(RS_URI, maxPoolSize=64)
shop = client["shop"]
inventory = shop["inventory"]
orders = shop["orders"]
wallets = shop["wallets"]

steps = []  # the execution log that ends up in the JSON file


class OutOfStock(Exception):
    pass


class VersionChanged(Exception):
    """The version read is no longer current: another reservation got there first."""


class NotEnoughMoney(Exception):
    pass


def record(step, command, result):
    steps.append({"step": step, "executedAt": datetime.now(timezone.utc), "command": command, "result": result})


def save_output():
    document = {"activity": TITLE, "script": Path(__file__).name, "steps": steps}
    OUTPUT_FILE.write_text(json_util.dumps(document, indent=2, json_options=json_util.RELAXED_JSON_OPTIONS))
    print(f"\nMongoDB commands and replies saved to {OUTPUT_FILE.name}")


def reset(stock):
    inventory.drop()
    orders.drop()
    orders.create_index([("order_id", ASCENDING)], unique=True)
    inventory.insert_one({"product_id": PRODUCT_ID, "name": "Laptop 15-inch", "stock": stock, "version": 0,
                          "price": PRICE})


def stock():
    return inventory.find_one({"product_id": PRODUCT_ID})["stock"]


def new_order_id():
    return f"ORD-{uuid.uuid4().hex[:8].upper()}"


def take_stock(session, qty, trace=None):
    product = inventory.find_one({"product_id": PRODUCT_ID}, session=session)
    if product["stock"] < qty:
        raise OutOfStock(product["stock"])
    taken = inventory.update_one(
        {"product_id": PRODUCT_ID, "version": product["version"]},
        {"$inc": {"stock": -qty, "version": 1}},
        session=session,
    )
    if taken.modified_count == 0:
        raise VersionChanged()
    if trace is not None:
        trace.append(f"stock {product['stock']} -> {product['stock'] - qty}")


def create_order(session, customer_id, qty, status="pending", order_id=None):
    order = {
        "order_id": order_id or new_order_id(),
        "customer_id": customer_id,
        "items": [{"product_id": PRODUCT_ID, "qty": qty, "price": PRICE}],
        "status": status,
        "total": qty * PRICE,
        "created_at": datetime.now(timezone.utc),
    }
    orders.insert_one(order, session=session)
    return order["order_id"]


def reserve_inventory(customer_id, qty=1, max_attempts=5, order_id=None):
    """Returns (status, attempts, conflict reasons, order id)."""
    conflicts = []
    for attempt in range(max_attempts):
        try:
            with client.start_session() as session:
                session.start_transaction(read_concern=ReadConcern("snapshot"), write_concern=WriteConcern("majority"))
                take_stock(session, qty)
                made = create_order(session, customer_id, qty, order_id=order_id)
                session.commit_transaction()
            return "RESERVED", attempt + 1, conflicts, made
        except OutOfStock:
            return "INSUFFICIENT", attempt + 1, conflicts, None
        except VersionChanged:
            conflicts.append("version changed")
        except PyMongoError as error:
            if not error.has_error_label("TransientTransactionError"):
                raise
            conflicts.append("WriteConflict" if isinstance(error, OperationFailure) and error.code == 112
                             else type(error).__name__)
        if attempt + 1 < max_attempts:
            time.sleep(0.01 * 2 ** attempt * random.uniform(0.5, 1.5))
    return "CONFLICT", max_attempts, conflicts, None


def reserve_without_transaction(customer_id, qty=1, order_id=None):
    """findAndModify takes the stock atomically; the order is a separate write."""
    taken = inventory.find_one_and_update(
        {"product_id": PRODUCT_ID, "stock": {"$gte": qty}},
        {"$inc": {"stock": -qty, "version": 1}},
        return_document=ReturnDocument.AFTER,
    )
    if taken is None:
        return "INSUFFICIENT"
    create_order(None, customer_id, qty, order_id=order_id)
    return "RESERVED"


def rush(max_attempts):
    """8 customers ask for 1 laptop each at the same instant; 5 are in stock."""
    reset(5)
    barrier, results = threading.Barrier(8), {}

    def customer(n):
        barrier.wait()
        results[n] = reserve_inventory(f"C-{n:02d}", 1, max_attempts)

    threads = [threading.Thread(target=customer, args=(n,)) for n in range(1, 9)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    for n in sorted(results):
        status, attempts, conflicts, _ = results[n]
        reasons = f"  [{', '.join(conflicts)}]" if conflicts else ""
        print(f"    C-{n:02d}: {status:<12} after {attempts} attempt{'s' if attempts > 1 else ' '}{reasons}")
    counts = {s: sum(1 for r in results.values() if r[0] == s) for s in ("RESERVED", "INSUFFICIENT", "CONFLICT")}
    left, made = stock(), orders.count_documents({})
    print(f"    reserved {counts['RESERVED']}, insufficient {counts['INSUFFICIENT']}, conflict {counts['CONFLICT']}"
          f"  ->  stock left {left}, orders {made}, stock + orders = {left + made}")
    return counts, left, made, results


def throughput(threads, transactional):
    reset(1_000_000)
    totals = {"reserved": 0, "conflicts": 0, "gave_up": 0}
    lock = threading.Lock()
    deadline = time.perf_counter() + SECONDS_PER_LEVEL

    def worker(n):
        mine = {"reserved": 0, "conflicts": 0, "gave_up": 0}
        while time.perf_counter() < deadline:
            if transactional:
                status, _, conflicts, _ = reserve_inventory(f"T-{n}", 1, max_attempts=10)
                mine["conflicts"] += len(conflicts)
            else:
                status = reserve_without_transaction(f"T-{n}")
            mine["reserved" if status == "RESERVED" else "gave_up"] += 1
        with lock:
            for key in totals:
                totals[key] += mine[key]

    t0 = time.perf_counter()
    pool = [threading.Thread(target=worker, args=(n,)) for n in range(threads)]
    for t in pool:
        t.start()
    for t in pool:
        t.join()
    elapsed = time.perf_counter() - t0
    consistent = 1_000_000 - stock() == orders.count_documents({})
    return {**totals, "seconds": round(elapsed, 2), "perSecond": round(totals["reserved"] / elapsed, 1),
            "consistent": consistent}


def draw_chart(rows):
    blue, orange, ink, muted, grid, surface = "#2a78d6", "#eb6834", "#0b0b0b", "#52514e", "#e6e5e1", "#fcfcfb"
    fig, ax = plt.subplots(figsize=(7.2, 3.8), dpi=200)
    fig.patch.set_facecolor(surface)
    ax.set_facecolor(surface)
    x = list(range(len(THREAD_LEVELS)))
    for key, label, color in (("txn", "transaction + retry", blue), ("plain", "findAndModify, no transaction", orange)):
        y = [r[key]["perSecond"] for r in rows]
        ax.plot(x, y, color=color, linewidth=2, solid_capstyle="round", solid_joinstyle="round", label=label, zorder=3)
        ax.scatter(x, y, s=64, color=color, edgecolors=surface, linewidths=2, zorder=4)
        ax.annotate(f"{y[-1]:,.0f}/s", (x[-1], y[-1]), xytext=(8, 0), textcoords="offset points",
                    va="center", fontsize=9, color=ink)
    ax.set_xticks(x, [str(t) for t in THREAD_LEVELS])
    ax.set_xlabel("concurrent threads", color=muted, fontsize=9)
    ax.set_ylabel("successful reservations per second", color=muted, fontsize=9)
    ax.set_title("Reservation throughput: one product, all threads competing", loc="left", fontsize=11, color=ink)
    ax.yaxis.grid(True, color=grid, linewidth=1)
    ax.set_axisbelow(True)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(grid)
    ax.tick_params(colors=muted, labelsize=9, length=0)
    ax.set_ylim(bottom=0)
    ax.set_xlim(-0.3, len(x) - 0.4)
    ax.legend(frameon=False, fontsize=9, labelcolor=ink, loc="upper left")
    fig.tight_layout()
    fig.savefig(CHART_FILE, facecolor=surface)


print(TITLE.upper())

print(f"\nPart 1: 8 customers, 5 laptops in stock, no retry")
counts1, left1, made1, results1 = rush(max_attempts=1)
record("Part 1: 8 concurrent reservations, no retry", {"threads": 8, "stock": 5, "maxAttempts": 1},
       {"counts": counts1, "stockLeft": left1, "orders": made1, "perCustomer": results1})

print(f"\nPart 2: the same rush with retry (up to 5 attempts, backoff 10 ms x 2^attempt, jittered)")
counts2, left2, made2, results2 = rush(max_attempts=5)
record("Part 2: 8 concurrent reservations, retry", {"threads": 8, "stock": 5, "maxAttempts": 5},
       {"counts": counts2, "stockLeft": left2, "orders": made2, "perCustomer": results2})

print(f"\nPart 3: throughput, {SECONDS_PER_LEVEL:.0f} s per level, every thread reserving the same product")
print(f"  {'Threads':>7}  {'Transaction (res/s)':>19} {'Conflicts retried':>18}  {'findAndModify (res/s)':>21}  Consistent")
rows = []
for threads in THREAD_LEVELS:
    txn, plain = throughput(threads, True), throughput(threads, False)
    rows.append({"threads": threads, "txn": txn, "plain": plain})
    print(f"  {threads:>7}  {txn['perSecond']:>19,.1f} {txn['conflicts']:>18,}  {plain['perSecond']:>21,.1f}  "
          f"{'yes' if txn['consistent'] and plain['consistent'] else 'NO'}")
record("Part 3: throughput", {"threadLevels": THREAD_LEVELS, "secondsPerLevel": SECONDS_PER_LEVEL}, rows)
peak = max(max(r["txn"]["perSecond"], r["plain"]["perSecond"]) for r in rows)
print("\n  transaction + retry (#) vs findAndModify (=), reservations per second")
for r in rows:
    for key, mark in (("txn", "#"), ("plain", "=")):
        value = r[key]["perSecond"]
        print(f"  {r['threads']:>3} thr {mark} {mark * max(1, round(40 * value / peak)):<40} {value:,.0f}")
draw_chart(rows)
print(f"  chart saved to {CHART_FILE.name}")

print("\nPart 4: the order insert fails after the stock was taken (order id already used)")
for label, transactional in (("without a transaction (findAndModify + insert)", False), ("with a transaction", True)):
    reset(5)
    orders.insert_one({"order_id": "ORD-TAKEN", "customer_id": "C-00", "status": "pending"})
    before = stock()
    try:
        if transactional:
            reserve_inventory("C-41", 1, max_attempts=1, order_id="ORD-TAKEN")
        else:
            reserve_without_transaction("C-41", 1, order_id="ORD-TAKEN")
        outcome = "order created"
    except DuplicateKeyError:
        outcome = "order insert failed (DuplicateKeyError)"
    after = stock()
    new_orders = orders.count_documents({"customer_id": "C-41"})
    verdict = "consistent" if before - after == new_orders else f"{before - after - new_orders} laptop lost: stock taken, no order"
    print(f"  {label}")
    print(f"    {outcome}; stock {before} -> {after}, orders for C-41: {new_orders}  ->  {verdict}")
    record(f"Part 4: failed order insert, {label}", {"orderId": "ORD-TAKEN (already exists)"},
           {"outcome": outcome, "stockBefore": before, "stockAfter": after, "ordersCreated": new_orders,
            "verdict": verdict})

print("\nPart 5: checkout with payment - stock, wallet and order in one transaction")
reset(5)
wallets.drop()
wallets.insert_many([{"customer_id": "C-100", "balance": 60000.0}, {"customer_id": "C-200", "balance": 20000.0}])


def checkout(customer_id, qty=1):
    trace = []
    try:
        with client.start_session() as session:
            with session.start_transaction(read_concern=ReadConcern("snapshot"), write_concern=WriteConcern("majority")):
                take_stock(session, qty, trace)
                charged = wallets.update_one({"customer_id": customer_id, "balance": {"$gte": qty * PRICE}},
                                             {"$inc": {"balance": -qty * PRICE}}, session=session)
                if charged.modified_count == 0:
                    trace.append("wallet charge refused")
                    raise NotEnoughMoney()
                trace.append(f"wallet -{qty * PRICE:,.0f}")
                trace.append(f"order {create_order(session, customer_id, qty, status='paid')} paid")
        return "COMMITTED", trace
    except NotEnoughMoney:
        return "ROLLED BACK", trace


for customer_id in ("C-100", "C-200"):
    wallet_before, stock_before = wallets.find_one({"customer_id": customer_id})["balance"], stock()
    outcome, trace = checkout(customer_id)
    wallet_after, stock_after = wallets.find_one({"customer_id": customer_id})["balance"], stock()
    paid = orders.count_documents({"customer_id": customer_id, "status": "paid"})
    print(f"  {customer_id} (wallet {wallet_before:,.0f}) buys a laptop at {PRICE:,.0f}: {outcome}")
    print(f"    steps: {' | '.join(trace)}")
    print(f"    stock {stock_before} -> {stock_after}, wallet {wallet_before:,.0f} -> {wallet_after:,.0f}, paid orders {paid}")
    record(f"Part 5: checkout by {customer_id}", {"customer": customer_id, "price": PRICE},
           {"outcome": outcome, "steps": trace, "stock": [stock_before, stock_after],
            "wallet": [wallet_before, wallet_after], "paidOrders": paid})

save_output()
