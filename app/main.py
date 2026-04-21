from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field


DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "pokemon_trading.db"


def resolve_db_path() -> Path:
    return Path(os.getenv("POKEMON_DB_PATH", str(DEFAULT_DB_PATH)))


@contextmanager
def get_conn(db_path: Path):
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db(db_path: Path) -> None:
    with get_conn(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS cards (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                card_code TEXT,
                card_name TEXT NOT NULL,
                set_name TEXT,
                rarity TEXT,
                UNIQUE(card_code, card_name)
            );

            CREATE TABLE IF NOT EXISTS inventory_lots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                card_id INTEGER NOT NULL,
                qty_in INTEGER NOT NULL CHECK(qty_in > 0),
                qty_remaining INTEGER NOT NULL CHECK(qty_remaining >= 0),
                unit_cost REAL NOT NULL CHECK(unit_cost >= 0),
                purchased_at TEXT NOT NULL,
                source TEXT,
                FOREIGN KEY(card_id) REFERENCES cards(id)
            );

            CREATE TABLE IF NOT EXISTS sales (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sold_at TEXT NOT NULL,
                channel TEXT,
                customer TEXT,
                shipping_fee REAL NOT NULL DEFAULT 0,
                platform_fee REAL NOT NULL DEFAULT 0,
                note TEXT
            );

            CREATE TABLE IF NOT EXISTS sale_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sale_id INTEGER NOT NULL,
                card_id INTEGER NOT NULL,
                qty INTEGER NOT NULL CHECK(qty > 0),
                unit_price REAL NOT NULL CHECK(unit_price >= 0),
                FOREIGN KEY(sale_id) REFERENCES sales(id) ON DELETE CASCADE,
                FOREIGN KEY(card_id) REFERENCES cards(id)
            );

            CREATE TABLE IF NOT EXISTS sale_cost_allocations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sale_item_id INTEGER NOT NULL,
                inventory_lot_id INTEGER NOT NULL,
                qty_used INTEGER NOT NULL CHECK(qty_used > 0),
                unit_cost REAL NOT NULL CHECK(unit_cost >= 0),
                FOREIGN KEY(sale_item_id) REFERENCES sale_items(id) ON DELETE CASCADE,
                FOREIGN KEY(inventory_lot_id) REFERENCES inventory_lots(id)
            );
            """
        )


class CardCreate(BaseModel):
    card_name: str = Field(min_length=1)
    card_code: Optional[str] = None
    set_name: Optional[str] = None
    rarity: Optional[str] = None


class PurchaseCreate(BaseModel):
    card_id: int
    qty: int = Field(gt=0)
    unit_cost: float = Field(ge=0)
    purchased_at: datetime
    source: Optional[str] = None


class SaleItemCreate(BaseModel):
    card_id: int
    qty: int = Field(gt=0)
    unit_price: float = Field(ge=0)


class SaleCreate(BaseModel):
    sold_at: datetime
    channel: Optional[str] = None
    customer: Optional[str] = None
    shipping_fee: float = Field(default=0, ge=0)
    platform_fee: float = Field(default=0, ge=0)
    note: Optional[str] = None
    items: List[SaleItemCreate]


def _allocate_fifo(conn: sqlite3.Connection, card_id: int, qty_needed: int):
    lots = conn.execute(
        """
        SELECT id, qty_remaining, unit_cost
        FROM inventory_lots
        WHERE card_id = ? AND qty_remaining > 0
        ORDER BY purchased_at ASC, id ASC
        """,
        (card_id,),
    ).fetchall()

    remaining = qty_needed
    allocations: list[tuple[int, int, float]] = []

    for lot in lots:
        if remaining <= 0:
            break
        use_qty = min(remaining, lot["qty_remaining"])
        allocations.append((lot["id"], use_qty, lot["unit_cost"]))
        remaining -= use_qty

    if remaining > 0:
        raise HTTPException(status_code=400, detail=f"Insufficient inventory for card_id={card_id}")

    return allocations


def create_app() -> FastAPI:
    app = FastAPI(title="Pokemon Trading MVP", version="0.2.0")
    app.state.db_path = resolve_db_path()

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    frontend_dir = Path(__file__).resolve().parent.parent / "frontend"
    app.mount("/static", StaticFiles(directory=frontend_dir), name="static")

    @app.on_event("startup")
    def startup_event() -> None:
        init_db(app.state.db_path)

    @app.get("/")
    def home():
        return FileResponse(frontend_dir / "index.html")

    @app.get("/health")
    def health():
        return {"status": "ok", "db_path": str(app.state.db_path)}

    @app.post("/cards")
    def create_card(payload: CardCreate):
        with get_conn(app.state.db_path) as conn:
            try:
                cur = conn.execute(
                    """
                    INSERT INTO cards (card_code, card_name, set_name, rarity)
                    VALUES (?, ?, ?, ?)
                    """,
                    (payload.card_code, payload.card_name, payload.set_name, payload.rarity),
                )
            except sqlite3.IntegrityError as e:
                raise HTTPException(status_code=409, detail=f"Card already exists: {e}")

            card_id = cur.lastrowid
            row = conn.execute("SELECT * FROM cards WHERE id = ?", (card_id,)).fetchone()
            return dict(row)

    @app.get("/cards")
    def list_cards():
        with get_conn(app.state.db_path) as conn:
            rows = conn.execute("SELECT * FROM cards ORDER BY card_name ASC").fetchall()
            return [dict(r) for r in rows]

    @app.post("/purchases")
    def create_purchase(payload: PurchaseCreate):
        with get_conn(app.state.db_path) as conn:
            card = conn.execute("SELECT id FROM cards WHERE id = ?", (payload.card_id,)).fetchone()
            if not card:
                raise HTTPException(status_code=404, detail="Card not found")

            cur = conn.execute(
                """
                INSERT INTO inventory_lots (card_id, qty_in, qty_remaining, unit_cost, purchased_at, source)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    payload.card_id,
                    payload.qty,
                    payload.qty,
                    payload.unit_cost,
                    payload.purchased_at.isoformat(),
                    payload.source,
                ),
            )
            row = conn.execute("SELECT * FROM inventory_lots WHERE id = ?", (cur.lastrowid,)).fetchone()
            return dict(row)

    @app.post("/sales")
    def create_sale(payload: SaleCreate):
        if not payload.items:
            raise HTTPException(status_code=400, detail="Sale must contain at least one item")

        with get_conn(app.state.db_path) as conn:
            cur = conn.execute(
                """
                INSERT INTO sales (sold_at, channel, customer, shipping_fee, platform_fee, note)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    payload.sold_at.isoformat(),
                    payload.channel,
                    payload.customer,
                    payload.shipping_fee,
                    payload.platform_fee,
                    payload.note,
                ),
            )
            sale_id = cur.lastrowid

            total_revenue = 0.0
            total_cost = 0.0

            for item in payload.items:
                card = conn.execute("SELECT id FROM cards WHERE id = ?", (item.card_id,)).fetchone()
                if not card:
                    raise HTTPException(status_code=404, detail=f"Card not found: {item.card_id}")

                allocations = _allocate_fifo(conn, item.card_id, item.qty)

                si_cur = conn.execute(
                    """
                    INSERT INTO sale_items (sale_id, card_id, qty, unit_price)
                    VALUES (?, ?, ?, ?)
                    """,
                    (sale_id, item.card_id, item.qty, item.unit_price),
                )
                sale_item_id = si_cur.lastrowid

                total_revenue += item.qty * item.unit_price

                for lot_id, qty_used, unit_cost in allocations:
                    conn.execute(
                        """
                        UPDATE inventory_lots
                        SET qty_remaining = qty_remaining - ?
                        WHERE id = ?
                        """,
                        (qty_used, lot_id),
                    )
                    conn.execute(
                        """
                        INSERT INTO sale_cost_allocations (sale_item_id, inventory_lot_id, qty_used, unit_cost)
                        VALUES (?, ?, ?, ?)
                        """,
                        (sale_item_id, lot_id, qty_used, unit_cost),
                    )
                    total_cost += qty_used * unit_cost

            profit = total_revenue - total_cost - payload.platform_fee - payload.shipping_fee

            return {
                "sale_id": sale_id,
                "revenue": round(total_revenue, 2),
                "cost": round(total_cost, 2),
                "platform_fee": payload.platform_fee,
                "shipping_fee": payload.shipping_fee,
                "profit": round(profit, 2),
            }

    @app.get("/inventory")
    def get_inventory():
        with get_conn(app.state.db_path) as conn:
            rows = conn.execute(
                """
                SELECT
                    c.id as card_id,
                    c.card_name,
                    COALESCE(SUM(l.qty_remaining), 0) AS qty_remaining,
                    COALESCE(ROUND(SUM(l.qty_remaining * l.unit_cost), 2), 0) AS inventory_cost
                FROM cards c
                LEFT JOIN inventory_lots l ON c.id = l.card_id
                GROUP BY c.id, c.card_name
                ORDER BY c.card_name ASC
                """
            ).fetchall()
            return [dict(r) for r in rows]

    @app.get("/reports/profit")
    def profit_report(
        start: Optional[date] = Query(default=None),
        end: Optional[date] = Query(default=None),
    ):
        start_iso = datetime.combine(start, datetime.min.time()).isoformat() if start else "0001-01-01T00:00:00"
        end_iso = datetime.combine(end, datetime.max.time()).isoformat() if end else "9999-12-31T23:59:59"

        with get_conn(app.state.db_path) as conn:
            sale_rows = conn.execute(
                "SELECT id, shipping_fee, platform_fee FROM sales WHERE sold_at BETWEEN ? AND ?",
                (start_iso, end_iso),
            ).fetchall()

            total_revenue = 0.0
            total_cost = 0.0
            total_shipping_fee = 0.0
            total_platform_fee = 0.0

            for s in sale_rows:
                sale_id = s["id"]
                total_shipping_fee += s["shipping_fee"]
                total_platform_fee += s["platform_fee"]

                total_revenue += conn.execute(
                    "SELECT COALESCE(SUM(qty * unit_price), 0) AS rev FROM sale_items WHERE sale_id = ?",
                    (sale_id,),
                ).fetchone()["rev"]

                total_cost += conn.execute(
                    """
                    SELECT COALESCE(SUM(a.qty_used * a.unit_cost), 0) AS cogs
                    FROM sale_cost_allocations a
                    JOIN sale_items si ON a.sale_item_id = si.id
                    WHERE si.sale_id = ?
                    """,
                    (sale_id,),
                ).fetchone()["cogs"]

            profit = total_revenue - total_cost - total_shipping_fee - total_platform_fee
            return {
                "start": start_iso,
                "end": end_iso,
                "sales_count": len(sale_rows),
                "revenue": round(total_revenue, 2),
                "cost": round(total_cost, 2),
                "shipping_fee": round(total_shipping_fee, 2),
                "platform_fee": round(total_platform_fee, 2),
                "profit": round(profit, 2),
            }

    @app.get("/transactions")
    def list_transactions():
        with get_conn(app.state.db_path) as conn:
            purchases = conn.execute(
                """
                SELECT l.id, 'purchase' AS type, c.card_name, l.qty_in AS qty, l.unit_cost AS unit_price, l.purchased_at AS occurred_at
                FROM inventory_lots l
                JOIN cards c ON c.id = l.card_id
                """
            ).fetchall()
            sales = conn.execute(
                """
                SELECT si.id, 'sale' AS type, c.card_name, si.qty AS qty, si.unit_price AS unit_price, s.sold_at AS occurred_at
                FROM sale_items si
                JOIN cards c ON c.id = si.card_id
                JOIN sales s ON s.id = si.sale_id
                """
            ).fetchall()

            merged = [dict(r) for r in purchases] + [dict(r) for r in sales]
            merged.sort(key=lambda x: x["occurred_at"], reverse=True)
            return merged

    return app


app = create_app()
