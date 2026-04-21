# Pokemon Trading MVP API + Frontend

基于 `system_design.md` 的完整 MVP 实现：
- 后端 API（FastAPI + SQLite）
- 前端管理页面（录入 / 库存 / 利润看板）
- Excel 导入脚本
- 单元测试
- Docker 一键部署

## 1. 本地启动

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

打开：
- 前端页面：`http://127.0.0.1:8000/`
- API 文档：`http://127.0.0.1:8000/docs`

## 2. Docker 一键部署

```bash
docker compose up --build
```

打开：`http://127.0.0.1:8000/`

数据库将持久化到 `./data/pokemon_trading.db`。

## 3. 前端功能

页面包括三块：
1. 录入页：新增卡片、买入、卖出
2. 库存页：实时库存数量与库存成本
3. 利润看板：按时间区间查看销售笔数、收入、成本、利润

## 4. Excel 导入

脚本：`scripts/import_excel.py`

```bash
python scripts/import_excel.py --file ./example.xlsx --db ./pokemon_trading.db
```

### Excel 模板要求

#### Sheet: `cards`（可选）
列顺序：
1. `card_name`（必填）
2. `card_code`（可选）
3. `set_name`（可选）
4. `rarity`（可选）

#### Sheet: `purchases`（可选）
列顺序：
1. `card_name`（必填）
2. `qty`（必填）
3. `unit_cost`（必填）
4. `purchased_at`（必填，ISO 时间，如 `2026-04-21T10:00:00`）
5. `source`（可选）

## 5. 运行测试

```bash
pytest -q
```

## 6. API 概览

- `GET /health`
- `GET /cards`
- `POST /cards`
- `POST /purchases`
- `POST /sales`
- `GET /inventory`
- `GET /transactions`
- `GET /reports/profit?start=2026-01-01&end=2026-12-31`
