from __future__ import annotations

import os
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app


def make_client(tmp_path: Path) -> TestClient:
    db_file = tmp_path / "test.db"
    os.environ["POKEMON_DB_PATH"] = str(db_file)
    app = create_app()
    return TestClient(app)


def test_health(tmp_path: Path):
    client = make_client(tmp_path)
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"


def test_fifo_and_profit(tmp_path: Path):
    client = make_client(tmp_path)

    card = client.post("/cards", json={"card_name": "皮卡丘 AR", "card_code": "SV5-123"})
    assert card.status_code == 200
    card_id = card.json()["id"]

    p1 = client.post(
        "/purchases",
        json={
            "card_id": card_id,
            "qty": 2,
            "unit_cost": 10,
            "purchased_at": "2026-04-01T10:00:00",
            "source": "A",
        },
    )
    assert p1.status_code == 200

    p2 = client.post(
        "/purchases",
        json={
            "card_id": card_id,
            "qty": 3,
            "unit_cost": 20,
            "purchased_at": "2026-04-02T10:00:00",
            "source": "B",
        },
    )
    assert p2.status_code == 200

    sale = client.post(
        "/sales",
        json={
            "sold_at": "2026-04-03T12:00:00",
            "platform_fee": 5,
            "shipping_fee": 3,
            "items": [{"card_id": card_id, "qty": 3, "unit_price": 30}],
        },
    )
    assert sale.status_code == 200
    sale_data = sale.json()

    # FIFO cost: 2*10 + 1*20 = 40
    assert sale_data["revenue"] == 90
    assert sale_data["cost"] == 40
    assert sale_data["profit"] == 42

    inventory = client.get("/inventory")
    assert inventory.status_code == 200
    inv = inventory.json()[0]
    assert inv["qty_remaining"] == 2

    report = client.get("/reports/profit?start=2026-04-01&end=2026-04-30")
    assert report.status_code == 200
    report_data = report.json()
    assert report_data["sales_count"] == 1
    assert report_data["profit"] == 42
