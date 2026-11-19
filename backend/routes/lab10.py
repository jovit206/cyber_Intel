"""Read-only summaries of saved transaction runs and opt-in live Lab 10 operations."""

from __future__ import annotations

import json
import hashlib
import os
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from pymongo import MongoClient
from pymongo.errors import PyMongoError
from pymongo.read_concern import ReadConcern
from pymongo.write_concern import WriteConcern
from ..seed_lab10_inventory_demo import seed_database

router = APIRouter(prefix="/api/lab10", tags=["Banking & Inventory"])
LAB10_DIR = Path(__file__).resolve().parents[2] / "lab10"
DEMO_INVENTORY_DATABASE = os.getenv("LAB10_DEMO_DATABASE", "lab10_demo").strip() or "lab10_demo"
if DEMO_INVENTORY_DATABASE in {"chapter07", "bank", "shop"}:
    raise RuntimeError("LAB10_DEMO_DATABASE must stay separate from chapter07, bank, and shop.")
_DEMO_SEED_LOCK = threading.Lock()
_DEMO_SEED_CHECKED = False
MAX_ATTEMPTS = 5
BASE_BACKOFF_SECONDS = 0.05

ACTIVITIES: dict[str, dict[str, str]] = {
    "bank-transfers": {"folder": "Activity_10.1", "file": "lab_10_1_bank_transfer.json"},
    "retry-backoff": {"folder": "Activity_10.1_Try_It_Yourself", "file": "try_10_1_retry_backoff.json"},
    "failover": {"folder": "Activity_10.1_Try_It_Yourself", "file": "failover_experiment.json"},
    "inventory": {"folder": "Activity_10.2", "file": "lab_10_2_inventory_reservation.json"},
}


class TransferRequest(BaseModel):
    source_account: str = Field(min_length=1, max_length=64)
    destination_account: str = Field(min_length=1, max_length=64)
    amount: float = Field(gt=0, le=1_000_000_000)
    idempotency_key: str = Field(min_length=8, max_length=128)


class ReservationRequest(BaseModel):
    product_id: str = Field(default="LAPTOP-15", min_length=1, max_length=64)
    customer_id: str = Field(min_length=1, max_length=64)
    quantity: int = Field(gt=0, le=1000)
    idempotency_key: str = Field(min_length=8, max_length=128)


class BusinessRuleError(Exception):
    pass


class InventoryChanged(Exception):
    pass


def _load_activity(activity_id: str) -> dict[str, Any]:
    metadata = ACTIVITIES.get(activity_id)
    if not metadata:
        raise HTTPException(status_code=404, detail="Saved activity not found")
    path = LAB10_DIR / metadata["folder"] / metadata["file"]
    try:
        with path.open("r", encoding="utf-8") as source:
            return json.load(source)
    except (OSError, json.JSONDecodeError) as error:
        raise HTTPException(status_code=503, detail="Saved result is unavailable") from error


def _at(step: dict[str, Any]) -> str | None:
    value = step.get("executedAt")
    return value.get("$date") if isinstance(value, dict) else value


def _steps(activity_id: str) -> list[dict[str, Any]]:
    return _load_activity(activity_id).get("steps", [])


def _snapshot_accounts_and_transfers() -> tuple[list[dict[str, Any]], list[dict[str, Any]], str | None]:
    steps = _steps("bank-transfers")
    final = next((s for s in steps if s.get("step") == "Final balances"), {})
    history = next((s for s in steps if s.get("step") == "Transfer history"), {})
    accounts = final.get("result") if isinstance(final.get("result"), list) else []
    transfers = history.get("result") if isinstance(history.get("result"), list) else []
    return accounts, transfers, _at(final) or _at(history)


def _open_client() -> tuple[MongoClient | None, str | None]:
    uri = os.getenv("LAB10_MONGO_URI", "").strip()
    if not uri:
        return None, "Live banking database is not configured; showing the saved experiment snapshot."
    client = None
    try:
        client = MongoClient(uri, serverSelectionTimeoutMS=1800, connectTimeoutMS=1800, retryWrites=True)
        hello = client.admin.command("hello")
        if not hello.get("setName"):
            client.close()
            return None, "The configured banking database is not a replica set; transactional operations are disabled."
        return client, None
    except PyMongoError:
        if client:
            client.close()
        return None, "Live banking database is unavailable; showing the saved experiment snapshot."


def _open_inventory_client() -> tuple[MongoClient | None, str | None, str | None, str | None]:
    """Use the dedicated Lab 10 replica set when configured, else an isolated persisted demo DB."""
    live_uri = os.getenv("LAB10_MONGO_URI", "").strip()
    live_issue = "LAB10_MONGO_URI is not configured."
    if live_uri:
        live_client = None
        try:
            live_client = MongoClient(live_uri, serverSelectionTimeoutMS=1800, connectTimeoutMS=1800, retryWrites=True)
            if live_client.admin.command("hello").get("setName"):
                return live_client, "shop", "live database", None
            live_issue = "The configured Lab 10 MongoDB server is not a replica set."
        except PyMongoError:
            live_issue = "The configured Lab 10 MongoDB replica set is unavailable."
        if live_client:
            live_client.close()

    demo_uri = os.getenv("MONGO_URI", "").strip()
    if not demo_uri:
        return None, None, None, f"{live_issue} No MongoDB connection is available for demonstration data."
    demo_client = None
    try:
        demo_client = MongoClient(demo_uri, serverSelectionTimeoutMS=1800, connectTimeoutMS=1800, retryWrites=True)
        if demo_client.admin.command("hello").get("setName"):
            global _DEMO_SEED_CHECKED
            with _DEMO_SEED_LOCK:
                if not _DEMO_SEED_CHECKED:
                    seed_database(demo_client[DEMO_INVENTORY_DATABASE])
                    _DEMO_SEED_CHECKED = True
            return demo_client, DEMO_INVENTORY_DATABASE, "demonstration data", live_issue
        demo_client.close()
        return None, None, None, f"{live_issue} The configured MongoDB server does not support transactions."
    except PyMongoError:
        if demo_client:
            demo_client.close()
        return None, None, None, f"{live_issue} The demonstration MongoDB replica set is unavailable."


def _recorded_transfer_results() -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for step in _steps("bank-transfers"):
        payments = (step.get("command") or {}).get("payments")
        step_result = step.get("result") if isinstance(step.get("result"), dict) else {}
        outcomes = step_result.get("results")
        if not isinstance(payments, list) or not isinstance(outcomes, list):
            continue
        for index, payment in enumerate(payments):
            outcome = outcomes[index] if index < len(outcomes) else {}
            results.append({
                "description": payment.get("description", "Payment"),
                "status": outcome.get("outcome", "UNKNOWN"),
                "reference": outcome.get("detail") if str(outcome.get("detail", "")).startswith("TXN-") else None,
                "reason": outcome.get("detail") if not str(outcome.get("detail", "")).startswith("TXN-") else None,
                "balance_changes": step_result.get("balanceChanges", []) if len(payments) == 1 else [],
                "recorded_at": _at(step),
                "passed": step_result.get("passed"),
            })
    return results


def _retry_summary() -> dict[str, Any]:
    steps = _steps("retry-backoff")
    conflict = steps[0].get("result", {}) if steps else {}
    double_spend = steps[2].get("result", {}) if len(steps) > 2 else {}
    comparison_command = steps[1].get("command", {}) if len(steps) > 1 else {}
    starting_balance_match = re.search(r"balance\s+([\d,]+)", str(comparison_command.get("payer", "")))
    stress_without = steps[3].get("result", {}) if len(steps) > 3 else {}
    stress_with = steps[4].get("result", {}) if len(steps) > 4 else {}
    return {
        "source": "saved benchmark",
        "recorded_at": _at(steps[-1]) if steps else None,
        "conflict_attempts": conflict.get("attemptsB", []),
        "double_spend": {
            "transactional": double_spend,
            "comparison": steps[1].get("result", {}) if len(steps) > 1 else {},
            "starting_balance": int(starting_balance_match.group(1).replace(",", "")) if starting_balance_match else None,
        },
        "stress": {"without_retry": stress_without, "with_retry": stress_with},
        "technical": _load_activity("retry-backoff"),
    }


def _inventory_summary() -> dict[str, Any]:
    steps = _steps("inventory")
    rush_no_retry = steps[0].get("result", {}) if steps else {}
    rush_retry = steps[1].get("result", {}) if len(steps) > 1 else {}
    throughput = steps[2].get("result", []) if len(steps) > 2 else []
    unsafe_insert = steps[3].get("result", {}) if len(steps) > 3 else {}
    transactional_insert = steps[4].get("result", {}) if len(steps) > 4 else {}
    checkout_success = steps[5].get("result", {}) if len(steps) > 5 else {}
    checkout_declined = steps[6].get("result", {}) if len(steps) > 6 else {}
    final_stock = checkout_declined.get("stock", [None, None])[-1]
    saved_product = {
        "product_id": "LAPTOP-15",
        "name": "Laptop 15-inch",
        "stock": final_stock,
        "available_stock": final_stock,
        "reserved_quantity": None,
        "price": 55000,
        "stock_status": "In Stock" if final_stock and final_stock > 0 else "Out of Stock",
        "last_updated": None,
    }
    return {
        "source": "saved experiment snapshot",
        "recorded_at": _at(steps[-1]) if steps else None,
        "experiment_product": saved_product,
        "catalog_complete": False,
        "reservation_runs": [
            {"label": "First attempt", "requests": 8, "starting_stock": 5, **rush_no_retry},
            {"label": "With conflict retries", "requests": 8, "starting_stock": 5, **rush_retry},
        ],
        "throughput": throughput,
        "transaction_consistency": {"unsafe_comparison": unsafe_insert, "transactional": transactional_insert},
        "checkout": {"successful": checkout_success, "declined": checkout_declined},
        "technical": _load_activity("inventory"),
    }


@router.get("/overview")
def get_overview() -> dict[str, Any]:
    client, issue = _open_client()
    if client:
        client.close()
    return {
        "execution_mode": "Live database" if not issue else "Saved run records",
        "cluster": {"available": not bool(issue), "message": issue or "Connected to the configured banking replica set."},
    }


@router.get("/banking/dashboard")
def get_banking_dashboard() -> dict[str, Any]:
    client, issue = _open_client()
    try:
        if client:
            db = client["bank"]
            accounts = list(db.accounts.find({}, {"_id": 0}).sort("account_id", 1))
            transfers = list(db.transfers.find({}, {"_id": 0}).sort("at", -1).limit(100))
            recorded_at = None
            mode = "live"
        else:
            accounts, transfers, recorded_at = _snapshot_accounts_and_transfers()
            mode = "saved"
        return {
            "mode": mode,
            "message": issue or "Live account and transfer data.",
            "recorded_at": recorded_at,
            "accounts": accounts,
            "transfers": transfers,
            "transfer_results": _recorded_transfer_results(),
            "technical": _load_activity("bank-transfers"),
        }
    finally:
        if client:
            client.close()


@router.get("/banking/retries")
def get_banking_retries() -> dict[str, Any]:
    return _retry_summary()


@router.get("/reliability/dashboard")
def get_reliability_dashboard() -> dict[str, Any]:
    client, inventory_database, inventory_source, inventory_issue = _open_inventory_client()
    try:
        inventory = _inventory_summary()
        if client:
            inventory.pop("experiment_product", None)
            shop = client[inventory_database]
            products = list(shop.inventory.find({}, {"_id": 0}).sort("product_id", 1))
            reserved_by_product = {
                row["_id"]: int(row["quantity"])
                for row in shop.orders.aggregate([
                    {"$match": {"status": "pending"}},
                    {"$unwind": "$items"},
                    {"$group": {"_id": "$items.product_id", "quantity": {"$sum": "$items.qty"}}},
                ])
            }
            for product in products:
                quantity = product.get("stock", product.get("available_stock", 0))
                product["available_stock"] = quantity
                product["reserved_quantity"] = reserved_by_product.get(product.get("product_id"), 0)
                product["stock_status"] = _stock_status(product)
                product["last_updated"] = (
                    product.get("updated_at") or product.get("last_updated") or product.get("modified_at")
                )
            orders = list(shop.orders.find({}, {"_id": 0}).sort("created_at", -1).limit(20))
            inventory["products"] = products
            inventory["product_count"] = len(products)
            inventory["database_product_count"] = shop.inventory.count_documents({})
            inventory["total_available_stock"] = sum(
                int(product.get("available_stock", 0) or 0) for product in products
            )
            inventory["catalog_complete"] = True
            inventory["reserved_quantity"] = sum(
                int(product.get("reserved_quantity", 0) or 0) for product in products
            )
            inventory["recent_orders"] = orders
            inventory["source"] = inventory_source
            inventory["source_database"] = inventory_database
            inventory["source_message"] = (
                "Authoritative Lab 10 inventory from shop.inventory."
                if inventory_source == "live database"
                else f"Persistent demonstration catalog from {inventory_database}.inventory; {inventory_issue}"
            )
        else:
            inventory["recent_orders"] = []
            inventory["reserved_quantity"] = None
            inventory["source"] = "unavailable"
            inventory["source_database"] = None
            inventory["catalog_error"] = f"Cannot load the inventory catalog: {inventory_issue or 'MongoDB is unavailable.'}"
        failover_record = _load_activity("failover")
        return {
            "mode": "live" if inventory_source == "live database" else "saved",
            "message": inventory.get("source_message") or inventory.get("catalog_error") or "Inventory source unavailable.",
            "failover": _failover_summary(failover_record),
            "inventory": inventory,
        }
    finally:
        if client:
            client.close()


def _stock_status(product: dict[str, Any]) -> str:
    stock = int(product.get("stock", product.get("available_stock", 0)) or 0)
    if stock <= 0:
        return "Out of Stock"
    threshold = next((product.get(key) for key in ("low_stock_threshold", "reorder_level", "min_stock") if product.get(key) is not None), None)
    if threshold is not None and stock <= int(threshold):
        return "Low Stock"
    return "In Stock"


def _catalog_or_503() -> dict[str, Any]:
    inventory = get_reliability_dashboard()["inventory"]
    if not inventory.get("catalog_complete"):
        raise HTTPException(status_code=503, detail=inventory.get("catalog_error", "Inventory catalog is unavailable."))
    return inventory


@router.get("/inventory/products")
def list_inventory_products(q: str | None = None, status: str | None = None) -> dict[str, Any]:
    inventory = _catalog_or_503()
    products = inventory["products"]
    if q:
        query = q.casefold().strip()
        products = [product for product in products if query in str(product.get("name", "")).casefold()
                    or query in str(product.get("product_id", "")).casefold()]
    if status:
        products = [product for product in products if product.get("stock_status") == status]
    return {"source": inventory["source"], "database": inventory["source_database"],
            "count": len(products), "products": products}


@router.get("/inventory/products/{product_id}")
def get_inventory_product(product_id: str) -> dict[str, Any]:
    inventory = _catalog_or_503()
    product = next((item for item in inventory["products"] if item.get("product_id") == product_id), None)
    if product is None:
        raise HTTPException(status_code=404, detail="Inventory product not found.")
    return {"source": inventory["source"], "database": inventory["source_database"], "product": product}


@router.get("/inventory/orders")
def list_inventory_orders(limit: int = 100, skip: int = 0) -> dict[str, Any]:
    client, database_name, source, issue = _open_inventory_client()
    if not client:
        raise HTTPException(status_code=503, detail=issue or "Inventory orders are unavailable.")
    if limit < 1 or limit > 500 or skip < 0:
        client.close()
        raise HTTPException(status_code=422, detail="Use a limit from 1 to 500 and a non-negative skip value.")
    try:
        orders = client[database_name].orders
        return {"source": source, "database": database_name, "total": orders.count_documents({}),
                "orders": list(orders.find({}, {"_id": 0}).sort("created_at", -1).skip(skip).limit(limit))}
    finally:
        client.close()


@router.get("/inventory/summary")
def get_inventory_summary() -> dict[str, Any]:
    inventory = _catalog_or_503()
    return {key: inventory[key] for key in (
        "source", "source_database", "product_count", "database_product_count",
        "total_available_stock", "reserved_quantity", "catalog_complete", "recent_orders",
    )}


def _failover_summary(record: dict[str, Any]) -> dict[str, Any]:
    steps = record.get("steps", [])
    checks = next((step.get("result", {}) for step in steps if step.get("step") == "Checks"), {})
    timeline = next((step.get("result", []) for step in steps if step.get("step") == "Timeline"), [])
    return {
        "source": "saved experiment",
        "recorded_at": _at(steps[-1]) if steps else None,
        "checks": checks,
        "timeline": timeline,
        "technical": record,
    }


@router.post("/banking/transfers")
def make_transfer(request: TransferRequest) -> dict[str, Any]:
    if request.source_account == request.destination_account:
        raise HTTPException(status_code=422, detail="Choose two different accounts.")
    client, issue = _open_client()
    if not client:
        raise HTTPException(status_code=503, detail=issue or "Live banking database unavailable.")
    db = client["bank"]
    delays: list[float] = []
    try:
        prior = db.transfers.find_one({"idempotency_key": request.idempotency_key}, {"_id": 0})
        if prior:
            if (prior.get("from"), prior.get("to"), float(prior.get("amount", 0))) != (
                request.source_account, request.destination_account, request.amount
            ):
                raise HTTPException(status_code=409, detail="This request key was already used for a different transfer.")
            return {"status": "committed", "transfer": prior, "duplicate_request": True, "attempts": 0, "backoff_seconds": []}
        for attempt in range(1, MAX_ATTEMPTS + 1):
            with client.start_session() as session:
                try:
                    session.start_transaction(read_concern=ReadConcern("snapshot"), write_concern=WriteConcern("majority"))
                    source = db.accounts.find_one({"account_id": request.source_account}, session=session)
                    destination = db.accounts.find_one({"account_id": request.destination_account}, session=session)
                    if not source or not destination:
                        raise BusinessRuleError("Source or destination account was not found.")
                    if source.get("status") != "active" or destination.get("status") != "active":
                        raise BusinessRuleError("Both accounts must be active.")
                    if float(source.get("balance", 0)) < request.amount:
                        raise BusinessRuleError("Insufficient funds.")
                    day_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
                    already_today = sum(row.get("amount", 0) for row in db.transfers.find(
                        {"from": request.source_account, "at": {"$gte": day_start}, "refund_of": None},
                        {"amount": 1, "_id": 0}, session=session,
                    ))
                    limit = source.get("daily_limit")
                    if limit is not None and already_today + request.amount > limit:
                        raise BusinessRuleError("The transfer exceeds this account's daily limit.")
                    changed = db.accounts.update_one(
                        {"account_id": request.source_account, "status": "active", "balance": {"$gte": request.amount}},
                        {"$inc": {"balance": -request.amount, "version": 1}}, session=session,
                    )
                    if changed.modified_count != 1:
                        raise BusinessRuleError("The source balance changed. Refresh the account list and retry.")
                    credited = db.accounts.update_one(
                        {"account_id": request.destination_account, "status": "active"},
                        {"$inc": {"balance": request.amount, "version": 1}}, session=session,
                    )
                    if credited.modified_count != 1:
                        raise BusinessRuleError("The destination account could not be credited.")
                    transfer = {
                        "transfer_id": f"TXN-{uuid.uuid4().hex[:8].upper()}",
                        "idempotency_key": request.idempotency_key,
                        "from": request.source_account,
                        "to": request.destination_account,
                        "amount": request.amount,
                        "note": "online transfer",
                        "refund_of": None,
                        "at": datetime.now(timezone.utc),
                    }
                    db.transfers.insert_one(transfer, session=session)
                    session.commit_transaction()
                    return {
                        "status": "committed", "transfer": {k: v for k, v in transfer.items() if k != "idempotency_key"},
                        "balances": {
                            request.source_account: float(source["balance"]) - request.amount,
                            request.destination_account: float(destination["balance"]) + request.amount,
                        },
                        "balances_before": {
                            request.source_account: float(source["balance"]),
                            request.destination_account: float(destination["balance"]),
                        },
                        "attempts": attempt, "backoff_seconds": delays,
                    }
                except BusinessRuleError as error:
                    if session.in_transaction:
                        session.abort_transaction()
                    accounts = list(db.accounts.find(
                        {"account_id": {"$in": [request.source_account, request.destination_account]}},
                        {"_id": 0, "account_id": 1, "balance": 1},
                    ))
                    return {"status": "declined", "reason": str(error), "transaction": "not committed", "balances": accounts, "attempts": attempt, "backoff_seconds": delays}
                except PyMongoError as error:
                    if error.has_error_label("UnknownTransactionCommitResult"):
                        for _ in range(4):
                            try:
                                session.commit_transaction()
                                return {"status": "committed", "transfer": {k: v for k, v in transfer.items() if k != "idempotency_key"}, "attempts": attempt, "backoff_seconds": delays}
                            except PyMongoError as retry_error:
                                if not retry_error.has_error_label("UnknownTransactionCommitResult"):
                                    error = retry_error
                                    break
                        raise HTTPException(status_code=503, detail="Commit outcome is unknown. Check transaction history before retrying.") from error
                    if session.in_transaction:
                        session.abort_transaction()
                    if not error.has_error_label("TransientTransactionError") or attempt == MAX_ATTEMPTS:
                        raise HTTPException(status_code=503, detail="The transfer could not be completed safely.") from error
            delay = BASE_BACKOFF_SECONDS * (2 ** (attempt - 1))
            delays.append(delay)
            time.sleep(delay)
        raise HTTPException(status_code=503, detail="Transfer retry limit reached.")
    finally:
        client.close()


@router.post("/inventory/reservations")
def reserve_stock(request: ReservationRequest) -> dict[str, Any]:
    client, database_name, source, issue = _open_inventory_client()
    if not client:
        raise HTTPException(status_code=503, detail=issue or "Live inventory database unavailable.")
    shop = client[database_name]
    order_id = f"ORD-{hashlib.sha256(request.idempotency_key.encode()).hexdigest()[:16].upper()}"
    delays: list[float] = []
    try:
        prior = shop.orders.find_one({"order_id": order_id}, {"_id": 0})
        if prior:
            item = (prior.get("items") or [{}])[0]
            if (prior.get("customer_id"), item.get("product_id"), int(item.get("qty", 0))) != (
                request.customer_id, request.product_id, request.quantity
            ):
                raise HTTPException(status_code=409, detail="This request key was already used for a different reservation.")
            current = shop.inventory.find_one({"product_id": request.product_id}, {"_id": 0, "stock": 1})
            return {"status": "reserved", "order": prior, "stock_after": current.get("stock") if current else None, "attempts": 0, "backoff_seconds": [], "duplicate_request": True}
        for attempt in range(1, MAX_ATTEMPTS + 1):
            with client.start_session() as session:
                try:
                    session.start_transaction(read_concern=ReadConcern("snapshot"), write_concern=WriteConcern("majority"))
                    product = shop.inventory.find_one({"product_id": request.product_id}, session=session)
                    if not product:
                        raise BusinessRuleError("Product was not found.")
                    if int(product.get("stock", 0)) < request.quantity:
                        raise BusinessRuleError("Not enough stock is available for this reservation.")
                    updated = shop.inventory.update_one(
                        {"product_id": request.product_id, "version": product.get("version", 0), "stock": {"$gte": request.quantity}},
                        {"$inc": {"stock": -request.quantity, "version": 1}}, session=session,
                    )
                    if updated.modified_count != 1:
                        raise InventoryChanged()
                    order = {
                        "order_id": order_id, "customer_id": request.customer_id,
                        "items": [{"product_id": request.product_id, "qty": request.quantity, "price": product.get("price", 0)}],
                        "status": "pending", "total": request.quantity * product.get("price", 0),
                        "created_at": datetime.now(timezone.utc), "is_demo": source == "demonstration data",
                    }
                    shop.orders.insert_one(order, session=session)
                    session.commit_transaction()
                    return {"status": "reserved", "order": order, "stock_before": product["stock"], "stock_after": product["stock"] - request.quantity, "attempts": attempt, "backoff_seconds": delays, "source": source}
                except BusinessRuleError as error:
                    if session.in_transaction:
                        session.abort_transaction()
                    current = shop.inventory.find_one({"product_id": request.product_id}, {"_id": 0, "stock": 1})
                    return {"status": "rejected", "reason": str(error), "stock": current.get("stock") if current else None, "attempts": attempt, "backoff_seconds": delays}
                except (InventoryChanged, PyMongoError) as error:
                    if isinstance(error, PyMongoError) and not error.has_error_label("TransientTransactionError"):
                        raise HTTPException(status_code=503, detail="The reservation could not be completed safely.") from error
                    if session.in_transaction:
                        session.abort_transaction()
                    if attempt == MAX_ATTEMPTS:
                        return {"status": "conflict", "reason": "Inventory changed too often; no reservation was made.", "attempts": attempt, "backoff_seconds": delays}
            delay = BASE_BACKOFF_SECONDS * (2 ** (attempt - 1))
            delays.append(delay)
            time.sleep(delay)
        raise HTTPException(status_code=503, detail="Reservation retry limit reached.")
    finally:
        client.close()


@router.get("/activities/{activity_id}")
def get_activity(activity_id: str) -> dict[str, Any]:
    return {"record": _load_activity(activity_id)}


@router.get("/throughput-chart")
def get_throughput_chart() -> FileResponse:
    path = LAB10_DIR / "Activity_10.2" / "throughput.png"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Saved performance chart unavailable")
    return FileResponse(path, media_type="image/png", filename="reservation-throughput.png")
