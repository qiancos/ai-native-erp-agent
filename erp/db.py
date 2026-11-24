# -*- coding: utf-8 -*-
"""
ERP-Agent 迷你 ERP 数据层
SQLite 建库、建表、种子数据（客户 / 商品 / 仓库 / 库存 / 订单 / 审批 / 规则 / 工具 / 知识库 / 审计）
所有金额单位：元；时间使用本地时间 ISO 字符串。
"""
import json
import os
import sqlite3
from datetime import date, datetime, timedelta

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "erp.db")

TODAY = date.today()


def d(offset: int) -> str:
    return (TODAY + timedelta(days=offset)).isoformat()


def ts(offset_days: int = 0, hour: int = 10, minute: int = 0) -> str:
    return (datetime.now() + timedelta(days=offset_days)).replace(
        hour=hour, minute=minute, second=0, microsecond=0
    ).isoformat(timespec="seconds")


def order_no(on_date: str, seq: int) -> str:
    return "SO-%s-%04d" % (on_date.replace("-", ""), seq)


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS employees (
  username     TEXT PRIMARY KEY,
  name         TEXT NOT NULL,
  role_code    TEXT NOT NULL,
  role_name    TEXT NOT NULL,
  dept         TEXT NOT NULL,
  scope        TEXT NOT NULL,          -- JSON: ["EAST"] 或 ["*"]
  password     TEXT NOT NULL DEFAULT 'demo123'
);

CREATE TABLE IF NOT EXISTS customers (
  code          TEXT PRIMARY KEY,
  name          TEXT NOT NULL,
  region        TEXT NOT NULL,          -- EAST / SOUTH
  contact       TEXT,
  phone         TEXT,
  address       TEXT,
  credit_limit  REAL NOT NULL DEFAULT 0,
  status        TEXT NOT NULL DEFAULT 'ACTIVE'
);

CREATE TABLE IF NOT EXISTS products (
  sku        TEXT PRIMARY KEY,
  name       TEXT NOT NULL,
  category   TEXT,
  unit       TEXT NOT NULL DEFAULT '件',
  list_price REAL NOT NULL,
  status     TEXT NOT NULL DEFAULT 'ACTIVE'
);

CREATE TABLE IF NOT EXISTS warehouses (
  code   TEXT PRIMARY KEY,
  name   TEXT NOT NULL,
  region TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS inventory (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  sku               TEXT NOT NULL,
  warehouse         TEXT NOT NULL,
  on_hand           INTEGER NOT NULL DEFAULT 0,
  locked            INTEGER NOT NULL DEFAULT 0,
  inbound           INTEGER NOT NULL DEFAULT 0,
  inbound_available INTEGER NOT NULL DEFAULT 0,
  UNIQUE(sku, warehouse)
);

CREATE TABLE IF NOT EXISTS orders (
  order_no      TEXT PRIMARY KEY,
  customer_code TEXT NOT NULL,
  status        TEXT NOT NULL,          -- DRAFT / APPROVED / PARTIAL / SHIPPED / INVOICED / CLOSED / REJECTED
  total_amount  REAL NOT NULL,
  delivery_date TEXT,
  warehouse     TEXT,
  created_by    TEXT,
  created_at    TEXT NOT NULL,
  approved_at   TEXT,
  shipped_at    TEXT,
  invoiced_at   TEXT,
  paid_amount   REAL NOT NULL DEFAULT 0,
  terms_days    INTEGER NOT NULL DEFAULT 30,
  trace_id      TEXT,
  note          TEXT
);

CREATE TABLE IF NOT EXISTS order_items (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  order_no    TEXT NOT NULL,
  sku         TEXT NOT NULL,
  qty         INTEGER NOT NULL,
  unit_price  REAL NOT NULL,
  shipped_qty INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS shipments (
  shipment_no TEXT PRIMARY KEY,
  order_no    TEXT NOT NULL,
  qty         INTEGER NOT NULL,
  warehouse   TEXT NOT NULL,
  status      TEXT NOT NULL DEFAULT 'DONE',
  created_at  TEXT NOT NULL,
  created_by  TEXT
);

CREATE TABLE IF NOT EXISTS inbounds (
  inbound_no  TEXT PRIMARY KEY,
  supplier    TEXT NOT NULL,
  sku         TEXT NOT NULL,
  qty         INTEGER NOT NULL,
  warehouse   TEXT NOT NULL,
  status      TEXT NOT NULL DEFAULT 'DONE',
  created_at  TEXT NOT NULL,
  created_by  TEXT
);

CREATE TABLE IF NOT EXISTS invoices (
  invoice_no  TEXT PRIMARY KEY,
  order_no    TEXT NOT NULL,
  amount      REAL NOT NULL,
  tax_type    TEXT NOT NULL DEFAULT 'VAT_13',
  status      TEXT NOT NULL DEFAULT 'ISSUED',
  created_at  TEXT NOT NULL,
  created_by  TEXT
);

CREATE TABLE IF NOT EXISTS actions (
  action_id   TEXT PRIMARY KEY,
  trace_id    TEXT NOT NULL,
  username    TEXT NOT NULL,
  intent      TEXT NOT NULL,
  tool        TEXT NOT NULL,
  params      TEXT NOT NULL DEFAULT '{}',
  risk_level  TEXT NOT NULL,            -- LOW / MEDIUM / HIGH
  risk_reasons TEXT NOT NULL DEFAULT '[]',
  status      TEXT NOT NULL,            -- PENDING_CONFIRM / PENDING_APPROVAL / DONE / REJECTED / BLOCKED / EXPIRED
  idempotency_key TEXT,
  result      TEXT NOT NULL DEFAULT '{}',
  created_at  TEXT NOT NULL,
  expires_at  TEXT
);

CREATE TABLE IF NOT EXISTS approvals (
  approval_id  TEXT PRIMARY KEY,
  action_id    TEXT NOT NULL,
  type         TEXT NOT NULL,
  applicant    TEXT NOT NULL,
  customer_code TEXT,
  customer_name TEXT,
  amount       REAL NOT NULL DEFAULT 0,
  rule_code    TEXT,
  reason       TEXT,
  risk_level   TEXT NOT NULL DEFAULT 'HIGH',
  payload      TEXT NOT NULL DEFAULT '{}',
  checks       TEXT NOT NULL DEFAULT '[]',
  status       TEXT NOT NULL DEFAULT 'PENDING',  -- PENDING / APPROVED / REJECTED
  approver     TEXT,
  comment      TEXT,
  created_at   TEXT NOT NULL,
  decided_at   TEXT,
  sla_deadline TEXT
);

CREATE TABLE IF NOT EXISTS audit_events (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  trace_id   TEXT NOT NULL,
  action_id  TEXT,
  event_type TEXT NOT NULL,
  payload    TEXT NOT NULL DEFAULT '{}',
  prev_hash  TEXT,
  hash       TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS risk_rules (
  rule_code     TEXT PRIMARY KEY,
  rule_type     TEXT NOT NULL,          -- LEVEL / CONFIRM / BLOCK
  title         TEXT NOT NULL,
  condition_text TEXT NOT NULL,
  level         TEXT NOT NULL,
  policy        TEXT NOT NULL,
  threshold     REAL,
  enabled       INTEGER NOT NULL DEFAULT 1,
  version       INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS tools (
  name          TEXT PRIMARY KEY,
  description   TEXT NOT NULL,
  risk_level    TEXT NOT NULL,
  roles         TEXT NOT NULL DEFAULT '["*"]',
  version       TEXT NOT NULL DEFAULT '1.0',
  enabled       INTEGER NOT NULL DEFAULT 1,
  calls_today   INTEGER NOT NULL DEFAULT 0,
  schema_json   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS knowledge (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  category   TEXT NOT NULL,
  title      TEXT NOT NULL,
  content    TEXT NOT NULL,
  metric_key TEXT,
  version    INTEGER NOT NULL DEFAULT 1,
  status     TEXT NOT NULL DEFAULT 'ACTIVE',
  updated_by TEXT,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
  token      TEXT PRIMARY KEY,
  username   TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS counters (
  name  TEXT PRIMARY KEY,
  value INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS integration_settings (
  id                INTEGER PRIMARY KEY CHECK (id = 1),
  vendor            TEXT NOT NULL DEFAULT '',
  base_url          TEXT NOT NULL DEFAULT '',
  auth_type         TEXT NOT NULL DEFAULT 'Bearer',
  credential        TEXT NOT NULL DEFAULT '',
  endpoints         TEXT NOT NULL DEFAULT '{}',
  enabled           INTEGER NOT NULL DEFAULT 0,
  status            TEXT NOT NULL DEFAULT 'UNCONFIGURED',
  last_test_at      TEXT,
  last_test_message TEXT,
  updated_at        TEXT
);
"""


def connect(db_path: str = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def get_counter(conn, name: str) -> int:
    row = conn.execute("SELECT value FROM counters WHERE name=?", (name,)).fetchone()
    return int(row["value"]) if row else 0


def incr_counter(conn, name: str) -> int:
    conn.execute(
        "INSERT INTO counters(name, value) VALUES(?, 1) "
        "ON CONFLICT(name) DO UPDATE SET value = value + 1",
        (name,),
    )
    return get_counter(conn, name)


def append_audit(conn, trace_id, action_id, event_type, payload, created_at=None):
    """追加审计事件并形成哈希链。"""
    import hashlib

    payload_text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    prev = conn.execute(
        "SELECT hash FROM audit_events ORDER BY id DESC LIMIT 1"
    ).fetchone()
    prev_hash = prev["hash"] if prev else "GENESIS"
    created = created_at or datetime.now().isoformat(timespec="seconds")
    raw = "|".join([prev_hash, trace_id or "", action_id or "", event_type, payload_text, created])
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    conn.execute(
        "INSERT INTO audit_events(trace_id, action_id, event_type, payload, prev_hash, hash, created_at) "
        "VALUES(?,?,?,?,?,?,?)",
        (trace_id, action_id, event_type, payload_text, prev_hash, digest, created),
    )
    return digest


EMPLOYEES = [
    ("zhangwei", "张伟", "SALES_FOLLOWER", "销售跟单", "华东大区 · 销售二部", ["EAST"]),
    ("chenjing", "陈静", "FINANCE", "财务开票员", "财务部", ["EAST", "SOUTH"]),
    ("liming", "李明", "SALES_MANAGER", "销售主管", "华东大区", ["EAST"]),
    ("wangqiang", "王强", "WAREHOUSE", "仓储发货员", "上海仓", ["EAST"]),
    ("chenli", "陈立", "FINANCE_DIRECTOR", "财务总监", "财务部", ["EAST", "SOUTH"]),
    ("wangmin", "王敏", "RISK_ADMIN", "风控管理员", "总部 · 风控", ["*"]),
]

CUSTOMERS = [
    ("C10023", "华东精密机械", "EAST", "周敏", "138****2311", "上海市松江区新桥镇新格路 88 号", 358600.0),
    ("C10031", "苏州华工电子", "EAST", "吴磊", "139****7788", "江苏省苏州市工业园区星湖街 168 号", 200000.0),
    ("C10047", "宁波海川汽配", "EAST", "郑海", "137****5521", "浙江省宁波市北仑区港城大道 66 号", 150000.0),
    ("C10052", "上海鼎信实业", "EAST", "许静", "136****9032", "上海市嘉定区安亭镇宝安公路 1288 号", 120000.0),
    ("C2088", "深圳联合精密", "SOUTH", "梁志", "135****4410", "广东省深圳市宝安区福永街道 12 号", 180000.0),
    ("C2091", "广州锐驰智能", "SOUTH", "何楠", "133****8890", "广东省广州市黄埔区科学城 8 号", 90000.0),
]

PRODUCTS = [
    ("SKU-A12", "伺服驱动器", "驱动类", "件", 85.0),
    ("SKU-B07", "编码器", "传感类", "件", 120.0),
    ("SKU-C33", "减速机", "传动类", "件", 500.0),
]

WAREHOUSES = [
    ("WH-SH", "上海仓", "EAST"),
    ("WH-HZ", "杭州仓", "EAST"),
    ("WH-NJ", "南京仓", "EAST"),
    ("WH-GZ", "广州仓", "SOUTH"),
]

INVENTORY = [
    ("SKU-A12", "WH-SH", 1400, 0, 500, 0),
    ("SKU-B07", "WH-SH", 800, 0, 0, 0),
    ("SKU-C33", "WH-HZ", 640, 0, 0, 0),
    ("SKU-C33", "WH-SH", 80, 0, 0, 0),
    ("SKU-B07", "WH-HZ", 150, 0, 0, 0),
    ("SKU-A12", "WH-GZ", 300, 0, 0, 0),
    ("SKU-B07", "WH-GZ", 260, 0, 0, 0),
]

ORDERS = [
    # (order_no, customer, status, items, created_offset, delivery_offset, warehouse, shipped_offset)
    ("SO-20261007-0091", "C10023", "SHIPPED",
     [("SKU-A12", 200, 85.0), ("SKU-C33", 50, 500.0)], -48, -40, "WH-SH", -48),
    ("SO-20261102-0118", "C10023", "SHIPPED",
     [("SKU-A12", 400, 85.0), ("SKU-B07", 100, 120.0)], -22, -15, "WH-SH", -22),
    ("SO-20261119-0142", "C10023", "SHIPPED",
     [("SKU-A12", 200, 85.0), ("SKU-B07", 100, 120.0)], -5, 2, "WH-SH", -5),
    ("SO-20261112-0111", "C10031", "SHIPPED",
     [("SKU-A12", 200, 85.0), ("SKU-C33", 50, 500.0), ("SKU-B07", 30, 120.0)], -12, -5, "WH-SH", -12),
    ("SO-20261120-0130", "C10031", "APPROVED",
     [("SKU-A12", 120, 85.0), ("SKU-C33", 45, 500.0)], -3, 5, "WH-SH", None),
    ("SO-20261123-0135", "C10047", "APPROVED",
     [("SKU-C33", 150, 500.0), ("SKU-B07", 95, 120.0)], -1, 7, "WH-HZ", None),
    ("SO-20261118-0125", "C10052", "INVOICED",
     [("SKU-A12", 60, 85.0), ("SKU-C33", 14, 500.0)], -6, 1, "WH-SH", -6),
]

TOOL_SEED = [
    ("customer.query", "客户主数据与往来查询（只读）", "LOW", ["*"], "1.3", 2148,
     {"name": "customer.query", "version": "1.3", "input": {"type": "object", "required": ["keyword"],
      "properties": {"keyword": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 50}}}}),
    ("inventory.query", "库存与可用量查询（只读，实时口径）", "LOW", ["*"], "1.2", 3402,
     {"name": "inventory.query", "version": "1.2", "input": {"type": "object", "required": ["sku"],
      "properties": {"sku": {"type": "string"}, "warehouse": {"type": "string"}}}}),
    ("order.query", "订单与出货进度查询（只读）", "LOW", ["*"], "1.1", 1876,
     {"name": "order.query", "version": "1.1", "input": {"type": "object", "required": ["customerCode"],
      "properties": {"customerCode": {"type": "string"}, "status": {"type": "string"}}}}),
    ("receivable.query", "应收余额与账龄查询（只读）", "LOW", ["SALES_FOLLOWER", "FINANCE"], "1.0", 964,
     {"name": "receivable.query", "version": "1.0", "input": {"type": "object", "required": ["customerCode"],
      "properties": {"customerCode": {"type": "string"}}}}),
    ("order.create_draft", "生成货单草案，不落单", "LOW", ["SALES_FOLLOWER", "SALES_MANAGER"], "1.2", 412,
     {"name": "order.create_draft", "version": "1.2", "required": ["customerCode", "items"],
      "input": {"type": "object", "properties": {
          "customerCode": {"type": "string"}, "items": {"type": "array"},
          "deliveryDate": {"type": "string", "format": "date"},
          "warehouse": {"type": "string", "enum": ["WH-SH", "WH-HZ", "WH-NJ"]}}}}),
    ("order.submit", "提交货单草案，写操作需幂等键", "MEDIUM", ["SALES_FOLLOWER"], "1.4", 388,
     {"name": "order.submit", "version": "1.4", "input": {"type": "object",
      "required": ["draftId", "version", "idempotencyKey"],
      "properties": {"draftId": {"type": "string"}, "version": {"type": "integer"},
                     "idempotencyKey": {"type": "string", "maxLength": 64}}}}),
    ("shipment.create", "依据已审核货单执行出货登记", "MEDIUM", ["WAREHOUSE", "SALES_FOLLOWER"], "1.1", 276,
     {"name": "shipment.create", "version": "1.1", "input": {"type": "object",
      "required": ["orderNo", "qty", "warehouse"],
      "properties": {"orderNo": {"type": "string"}, "qty": {"type": "integer"},
                     "warehouse": {"type": "string"}}}}),
    ("invoice.issue", "开具发票（灰度关闭）", "HIGH", ["FINANCE"], "1.0", 0,
     {"name": "invoice.issue", "version": "1.0", "risk": {"default": "HIGH", "policy": "APPROVE"},
      "input": {"type": "object", "required": ["orderNo", "amount", "taxType"],
                "properties": {"orderNo": {"type": "string"}, "amount": {"type": "number"},
                               "taxType": {"type": "string", "enum": ["VAT_13", "VAT_9", "VAT_6"]}}}}),
]

RULES = [
    ("RISK-AMT-001", "LEVEL", "货单金额超过阈值", "amount > 50000", "HIGH", "APPROVE", 50000, 1, 4),
    ("RISK-NEW-002", "LEVEL", "新客户首单", "customer.has_orders = false", "HIGH", "APPROVE", None, 1, 2),
    ("RISK-STOCK-003", "BLOCK", "可用库存不足", "qty > available_stock", "HIGH", "REJECT", None, 1, 1),
    ("RISK-PRICE-004", "CONFIRM", "单价偏离历史区间", "abs(price-avg)/avg > 0.15", "MEDIUM", "WARN", 0.15, 1, 1),
    ("BLOCK-SCOPE-001", "BLOCK", "数据范围外访问", "target.scope not in employee.scope", "HIGH", "REJECT", None, 1, 1),
    ("BLOCK-SQL-001", "BLOCK", "直改库 / 原生 SQL 意图", "params ~ (update|delete|insert)", "HIGH", "REJECT", None, 1, 1),
]

KNOWLEDGE = [
    ("指标", "可用库存", "可用 = 现存量 − 锁定量 + 可分配在途。用于判断能否承诺发货，不等于现存量。",
     "available_stock", 3, "王敏"),
    ("指标", "应收账款账龄", "按出货日起算，30 / 60 / 90 天分档；逾期金额 = 超过约定账期的未结金额。",
     "ar_aging", 2, "陈静"),
    ("规则", "货单状态流转", "草案 → 待确认 → 已审核 → 部分出货 → 已出货 → 已开票。未审核不可出货，未出货不可开票。",
     "order_state", 4, "王敏"),
    ("规则", "信用额度占用", "已审核未出货的货单占用 50% 额度，已出货未回款占用 100%，超额度需主管审批。",
     "credit_limit", 1, "李明"),
    ("FAQ", "华东大区客户归属规则", "按签约主体所在省份归属；跨区集团客户按主合同归属，需在客户主数据维护归属大区字段。",
     "scope_rule", 2, "张伟"),
]


def seed(conn: sqlite3.Connection):
    for e in EMPLOYEES:
        conn.execute(
            "INSERT OR IGNORE INTO employees(username,name,role_code,role_name,dept,scope) VALUES(?,?,?,?,?,?)",
            (e[0], e[1], e[2], e[3], e[4], json.dumps(e[5], ensure_ascii=False)),
        )
    conn.executemany(
        "INSERT OR IGNORE INTO customers(code,name,region,contact,phone,address,credit_limit) VALUES(?,?,?,?,?,?,?)",
        CUSTOMERS,
    )
    conn.executemany(
        "INSERT OR IGNORE INTO products(sku,name,category,unit,list_price) VALUES(?,?,?,?,?)",
        PRODUCTS,
    )
    conn.executemany("INSERT OR IGNORE INTO warehouses(code,name,region) VALUES(?,?,?)", WAREHOUSES)
    conn.executemany(
        "INSERT OR IGNORE INTO inventory(sku,warehouse,on_hand,locked,inbound,inbound_available) VALUES(?,?,?,?,?,?)",
        INVENTORY,
    )

    # 订单与明细
    for idx, (no, cust, status, items, created_off, delivery_off, wh, shipped_off) in enumerate(ORDERS, start=1):
        exists = conn.execute("SELECT 1 FROM orders WHERE order_no=?", (no,)).fetchone()
        if exists:
            continue
        total = round(sum(qty * price for _, qty, price in items), 2)
        trace = "tr-seed-%03d" % idx
        created = ts(created_off, 9 + (idx % 6), (idx * 7) % 60)
        approved = ts(created_off, 15, 10) if status in ("APPROVED", "SHIPPED", "INVOICED") else None
        shipped = ts(shipped_off, 16, 20) if shipped_off is not None else None
        conn.execute(
            "INSERT INTO orders(order_no,customer_code,status,total_amount,delivery_date,warehouse,"
            "created_by,created_at,approved_at,shipped_at,terms_days,trace_id) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (no, cust, status, total, d(delivery_off), wh, "zhangwei", created, approved, shipped, 30, trace),
        )
        for sku, qty, price in items:
            conn.execute(
                "INSERT INTO order_items(order_no,sku,qty,unit_price,shipped_qty) VALUES(?,?,?,?,?)",
                (no, sku, qty, price, qty if shipped else 0),
            )
        if shipped:
            total_qty = sum(q for _, q, _ in items)
            conn.execute(
                "INSERT OR IGNORE INTO shipments(shipment_no,order_no,qty,warehouse,status,created_at,created_by) "
                "VALUES(?,?,?,?,'DONE',?,?)",
                ("SH-" + no[3:], no, total_qty, wh, shipped, "zhangwei"),
            )
        if status == "INVOICED":
            conn.execute(
                "INSERT OR IGNORE INTO invoices(invoice_no,order_no,amount,tax_type,status,created_at,created_by) "
                "VALUES(?,?,?,?,'ISSUED',?,?)",
                ("INV-" + no[3:], no, total, "VAT_13", ts(shipped_off + 1, 10, 30), "chenjing"),
            )
        # 已审核订单占用库存
        if status == "APPROVED":
            for sku, qty, _ in items:
                conn.execute(
                    "UPDATE inventory SET locked = locked + ? WHERE sku=? AND warehouse=?",
                    (qty, sku, wh),
                )
        append_audit(conn, trace, None, "ORDER_CREATED",
                     {"order_no": no, "customer": cust, "amount": total, "status": status}, created)
        if approved:
            append_audit(conn, trace, None, "ORDER_APPROVED", {"order_no": no}, approved)
        if shipped:
            append_audit(conn, trace, None, "ORDER_SHIPPED", {"order_no": no}, shipped)

    # 入库单
    if conn.execute("SELECT COUNT(*) c FROM inbounds").fetchone()["c"] == 0:
        inbound_rows = [
            ("IN-20261120-001", "上海精工供应链", "SKU-A12", 300, "WH-SH", ts(-4, 9, 30), "wangqiang"),
            ("IN-20261118-002", "苏州传感科技", "SKU-B07", 200, "WH-SH", ts(-6, 14, 10), "wangqiang"),
            ("IN-20261115-003", "杭州传动设备", "SKU-C33", 150, "WH-HZ", ts(-9, 11, 0), "wangqiang"),
        ]
        for no, supplier, sku, qty, wh, created, by in inbound_rows:
            conn.execute(
                "INSERT INTO inbounds(inbound_no,supplier,sku,qty,warehouse,status,created_at,created_by) "
                "VALUES(?,?,?,?,?,'DONE',?,?)", (no, supplier, sku, qty, wh, created, by),
            )
            append_audit(conn, "tr-seed-in-" + no[-3:], None, "INBOUND_CREATED",
                         {"inbound_no": no, "sku": sku, "qty": qty}, created)

    # 工具
    for name, desc, risk, roles, ver, calls, schema in TOOL_SEED:
        conn.execute(
            "INSERT OR IGNORE INTO tools(name,description,risk_level,roles,version,enabled,calls_today,schema_json) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (name, desc, risk, json.dumps(roles, ensure_ascii=False), ver,
             0 if name == "invoice.issue" else 1, calls, json.dumps(schema, ensure_ascii=False)),
        )
    # 风控规则
    for r in RULES:
        conn.execute(
            "INSERT OR IGNORE INTO risk_rules(rule_code,rule_type,title,condition_text,level,policy,"
            "threshold,enabled,version) VALUES(?,?,?,?,?,?,?,?,?)", r,
        )
    # 知识库
    if conn.execute("SELECT COUNT(*) c FROM knowledge").fetchone()["c"] == 0:
        for cat, title, content, key, ver, by in KNOWLEDGE:
            conn.execute(
                "INSERT INTO knowledge(category,title,content,metric_key,version,status,updated_by,updated_at) "
                "VALUES(?,?,?,?,?,'ACTIVE',?,?)",
                (cat, title, content, key, ver, by, ts(-3, 10, 30)),
            )

    # 待审批单（高风险）：两条货单 + 一条开票
    if conn.execute("SELECT COUNT(*) c FROM approvals").fetchone()["c"] == 0:
        _seed_approvals(conn)

    # 外部 ERP 接入配置（默认未配置，数据走内置 ERP）
    if conn.execute("SELECT COUNT(*) c FROM integration_settings").fetchone()["c"] == 0:
        conn.execute(
            "INSERT INTO integration_settings(id,vendor,base_url,auth_type,credential,endpoints,"
            "enabled,status,updated_at) VALUES(1,'','','Bearer','',?,0,'UNCONFIGURED',?)",
            (json.dumps({
                "health": "/api/health",
                "customers": "/api/erp/customers",
                "inventory": "/api/erp/inventory",
                "orders": "/api/erp/orders",
                "shipments": "/api/erp/shipments",
                "invoices": "/api/erp/invoices",
            }, ensure_ascii=False), ts(0, 9, 0)),
        )

    conn.commit()


def _seed_approvals(conn):
    approvals = [
        dict(
            approval_id="AP-20261124-0029", action_id="act-seed-ap1", type="创建货单",
            applicant="zhangwei", customer_code="C10047", customer_name="宁波海川汽配",
            amount=86400.0, rule_code="RISK-AMT-001",
            minutes=12,
            reason="货单金额 ¥86,400 超过中风险阈值 ¥50,000，升级为主管审批",
            payload={"customer_code": "C10047", "warehouse": "WH-HZ",
                     "delivery_date": d(7), "items": [{"sku": "SKU-C33", "qty": 150, "unit_price": 500.0},
                                                      {"sku": "SKU-B07", "qty": 95, "unit_price": 120.0}]},
            checks=[{"ok": True, "text": "客户信用额度剩余 ¥150,000，可覆盖本单"},
                    {"ok": True, "text": "杭州仓减速机可用 640 件 ≥ 150 件"},
                    {"ok": True, "text": "单价与近 90 天成交均价一致"}],
            created="-0 12:10", sla=24,
        ),
        dict(
            approval_id="AP-20261124-0030", action_id="act-seed-ap2", type="创建货单",
            applicant="zhangwei", customer_code="C10052", customer_name="上海鼎信实业",
            amount=12100.0, rule_code="RISK-NEW-002",
            minutes=8,
            reason="新客户首单，按策略需主管确认后再落单",
            payload={"customer_code": "C10052", "warehouse": "WH-SH",
                     "delivery_date": d(5), "items": [{"sku": "SKU-A12", "qty": 60, "unit_price": 85.0},
                                                      {"sku": "SKU-C33", "qty": 14, "unit_price": 500.0}]},
            checks=[{"ok": True, "text": "客户主数据完整，信用额度 ¥120,000"},
                    {"ok": True, "text": "上海仓可用库存充足"},
                    {"ok": True, "text": "历史无逾期记录"}],
            created="-0 11:05", sla=24,
        ),
        dict(
            approval_id="AP-20261124-0031", action_id="act-seed-ap3", type="开具发票",
            applicant="chenjing", customer_code="C10031", customer_name="苏州华工电子",
            amount=45600.0, rule_code="RISK-INV-005",
            minutes=41,
            reason="开票属于高风险动作，需财务主管审批后执行",
            payload={"order_no": "SO-20261112-0111", "amount": 45600.0, "tax_type": "VAT_13"},
            checks=[{"ok": True, "text": "单据已全部出货，出货数量与开票数量一致"},
                    {"ok": True, "text": "开票金额 ≤ 合同金额，税率 13% 校验通过"},
                    {"ok": True, "text": "客户状态正常，无红冲记录"}],
            created="-0 09:40", sla=24,
        ),
    ]
    now = datetime.now()
    for a in approvals:
        created_at = (now - timedelta(minutes=a.get("minutes", 120))).replace(microsecond=0).isoformat(timespec="seconds")
        action_id = a["action_id"]
        trace_id = "tr-seed-" + action_id[-3:]
        payload = a["payload"]
        tool = "invoice.issue" if a["type"] == "开具发票" else "order.submit"
        conn.execute(
            "INSERT INTO actions(action_id,trace_id,username,intent,tool,params,risk_level,"
            "risk_reasons,status,result,created_at,expires_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (action_id, trace_id, a["applicant"], "CREATE_ORDER", tool,
             json.dumps(payload, ensure_ascii=False), "HIGH",
             json.dumps([{"rule": a["rule_code"], "text": a["reason"]}], ensure_ascii=False),
             "PENDING_APPROVAL", json.dumps({"draft": payload}, ensure_ascii=False), created_at,
             (now + timedelta(hours=24)).isoformat(timespec="seconds")),
        )
        conn.execute(
            "INSERT INTO approvals(approval_id,action_id,type,applicant,customer_code,customer_name,"
            "amount,rule_code,reason,risk_level,payload,checks,status,created_at,sla_deadline) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,'PENDING',?,?)",
            (a["approval_id"], action_id, a["type"], a["applicant"], a["customer_code"],
             a["customer_name"], a["amount"], a["rule_code"], a["reason"], "HIGH",
             json.dumps(payload, ensure_ascii=False),
             json.dumps(a["checks"], ensure_ascii=False), created_at,
             (now + timedelta(hours=24)).isoformat(timespec="seconds")),
        )
        append_audit(conn, trace_id, action_id, "RISK_HIGH",
                     {"rule": a["rule_code"], "amount": a["amount"]}, created_at)


def init_db(force: bool = False):
    if force and os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    conn = connect()
    conn.executescript(SCHEMA)
    conn.commit()
    seed(conn)
    return conn


if __name__ == "__main__":
    init_db(force=True)
    print("ERP database initialized:", DB_PATH)
