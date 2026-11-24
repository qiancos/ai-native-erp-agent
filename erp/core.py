# -*- coding: utf-8 -*-
"""
ERP-Agent 业务内核
身份与数据范围、库存可用量、应收账龄、风险分级、草案确认、审批执行、审计哈希链。
"""
import hashlib
import json
import re
import time
import urllib.error
import urllib.request
import uuid
from datetime import date, datetime, timedelta

import db


# --------------------------------------------------------------------------
# 基础工具
# --------------------------------------------------------------------------

def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def jloads(text, default):
    try:
        return json.loads(text) if text else default
    except Exception:
        return default


def money(value: float) -> str:
    if abs(value - round(value)) < 0.005:
        return "¥%s" % format(int(round(value)), ",")
    return "¥%s" % format(value, ",.2f")


def today() -> date:
    return date.today()


def parse_date(text: str, default_days: int = 7) -> str:
    if not text:
        return (today() + timedelta(days=default_days)).isoformat()
    m = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", text)
    if m:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
    m = re.search(r"(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]", text)
    if m:
        year = today().year
        candidate = date(year, int(m.group(1)), int(m.group(2)))
        if candidate < today():
            candidate = date(year + 1, int(m.group(1)), int(m.group(2)))
        return candidate.isoformat()
    return (today() + timedelta(days=default_days)).isoformat()


# --------------------------------------------------------------------------
# 身份与会话
# --------------------------------------------------------------------------

ROLE_LABEL = {
    "SALES_FOLLOWER": "销售跟单",
    "SALES_MANAGER": "销售主管",
    "FINANCE": "财务开票员",
    "FINANCE_DIRECTOR": "财务总监",
    "WAREHOUSE": "仓储发货员",
    "RISK_ADMIN": "风控管理员",
}


def employee_payload(row) -> dict:
    scope = jloads(row["scope"], [])
    scope_label = "全部区域" if "*" in scope else "、".join(
        {"EAST": "华东区", "SOUTH": "华南区", "NORTH": "华北区"}.get(s, s) for s in scope
    )
    return {
        "username": row["username"],
        "name": row["name"],
        "role_code": row["role_code"],
        "role_name": row["role_name"],
        "dept": row["dept"],
        "scope": scope,
        "scope_label": scope_label,
        "initials": row["name"][0],
    }


def login(conn, username: str, password: str = "demo123"):
    row = conn.execute("SELECT * FROM employees WHERE username=?", (username,)).fetchone()
    if not row or row["password"] != password:
        return None
    token = "tk-" + uuid.uuid4().hex[:24]
    conn.execute(
        "INSERT INTO sessions(token, username, created_at) VALUES(?,?,?)",
        (token, username, now_iso()),
    )
    conn.commit()
    return {"token": token, "employee": employee_payload(row)}


def employee_by_token(conn, token: str):
    if not token:
        return None
    row = conn.execute(
        "SELECT e.* FROM sessions s JOIN employees e ON e.username=s.username WHERE s.token=?",
        (token,),
    ).fetchone()
    return employee_payload(row) if row else None


def demo_accounts(conn):
    rows = conn.execute("SELECT * FROM employees ORDER BY rowid").fetchall()
    return [employee_payload(r) for r in rows]


# --------------------------------------------------------------------------
# 主数据
# --------------------------------------------------------------------------

def find_customer(conn, text: str):
    if not text:
        return None
    m = re.search(r"C\d{4,6}", text, re.I)
    if m:
        row = conn.execute("SELECT * FROM customers WHERE code=?", (m.group(0).upper(),)).fetchone()
        if row:
            return dict(row)
    for row in conn.execute("SELECT * FROM customers").fetchall():
        name = row["name"]
        short = re.sub(r"(机械|电子|实业|汽配|智能|精密)$", "", name)
        if name in text or (len(short) >= 2 and short in text):
            return dict(row)
    return None


SKU_ALIASES = {"A12": "SKU-A12", "B07": "SKU-B07", "C33": "SKU-C33"}


def find_sku(conn, text: str):
    if not text:
        return None
    upper = text.upper()
    for alias, sku in SKU_ALIASES.items():
        if alias in upper:
            row = conn.execute("SELECT * FROM products WHERE sku=?", (sku,)).fetchone()
            if row:
                return dict(row)
    for word in ("伺服", "编码器", "减速机"):
        if word in text:
            row = conn.execute("SELECT * FROM products WHERE name LIKE ?", ("%" + word + "%",)).fetchone()
            if row:
                return dict(row)
    return None


def product(conn, sku: str):
    row = conn.execute("SELECT * FROM products WHERE sku=?", (sku,)).fetchone()
    return dict(row) if row else None


def customer_by_code(conn, code: str):
    row = conn.execute("SELECT * FROM customers WHERE code=?", (code,)).fetchone()
    return dict(row) if row else None


WAREHOUSE_REGION = {"WH-SH": "EAST", "WH-HZ": "EAST", "WH-NJ": "EAST", "WH-GZ": "SOUTH"}


def allowed_warehouses(emp):
    if not emp or "*" in emp["scope"]:
        return None
    return [code for code, region in WAREHOUSE_REGION.items() if region in emp["scope"]]


def inventory_rows(conn, sku: str, emp=None):
    rows = conn.execute(
        "SELECT i.*, w.name AS warehouse_name FROM inventory i "
        "JOIN warehouses w ON w.code=i.warehouse WHERE i.sku=? ORDER BY i.warehouse",
        (sku,),
    ).fetchall()
    allowed = allowed_warehouses(emp)
    out = [dict(r) for r in rows]
    if allowed is not None:
        out = [r for r in out if r["warehouse"] in allowed]
    return out


def available_stock(conn, sku: str, warehouse: str = None, emp=None) -> int:
    sql = "SELECT COALESCE(SUM(on_hand - locked + inbound_available),0) v FROM inventory WHERE sku=?"
    params = [sku]
    if warehouse:
        sql += " AND warehouse=?"
        params.append(warehouse)
    allowed = allowed_warehouses(emp)
    if allowed is not None:
        sql += " AND warehouse IN (%s)" % ",".join("?" * len(allowed))
        params.extend(allowed)
    return int(conn.execute(sql, params).fetchone()["v"])


def in_scope(emp: dict, customer: dict) -> bool:
    if not customer:
        return False
    return "*" in emp["scope"] or customer["region"] in emp["scope"]


def outstanding_row(conn, order_row) -> dict:
    due = None
    if order_row["shipped_at"]:
        ship_date = date.fromisoformat(order_row["shipped_at"][:10])
        due = ship_date + timedelta(days=order_row["terms_days"])
    overdue_days = (today() - due).days if due and today() > due else 0
    return {
        "order_no": order_row["order_no"],
        "customer_code": order_row["customer_code"],
        "total": order_row["total_amount"],
        "open": round(order_row["total_amount"] - order_row["paid_amount"], 2),
        "shipped_at": (order_row["shipped_at"] or "")[:10],
        "due_date": due.isoformat() if due else "",
        "overdue_days": overdue_days,
        "status": "逾期 %d 天" % overdue_days if overdue_days > 0 else "未到期",
    }


def receivables(conn, customer_code: str):
    rows = conn.execute(
        "SELECT * FROM orders WHERE customer_code=? AND status IN ('SHIPPED','PARTIAL','INVOICED') "
        "AND paid_amount < total_amount ORDER BY shipped_at",
        (customer_code,),
    ).fetchall()
    items = [outstanding_row(conn, r) for r in rows]
    total = round(sum(i["open"] for i in items), 2)
    overdue = round(sum(i["open"] for i in items if i["overdue_days"] > 0), 2)
    return {"items": items, "total": total, "overdue": overdue}


def credit_snapshot(conn, customer: dict):
    rec = receivables(conn, customer["code"])
    remaining = round(customer["credit_limit"] - rec["total"], 2)
    return {"limit": customer["credit_limit"], "used": rec["total"], "remaining": remaining}


# --------------------------------------------------------------------------
# 审计
# --------------------------------------------------------------------------

def audit(conn, trace_id, action_id, event_type, payload, created_at=None):
    return db.append_audit(conn, trace_id, action_id, event_type, payload, created_at)


def verify_trace(conn, trace_id: str):
    rows = conn.execute(
        "SELECT * FROM audit_events WHERE trace_id=? ORDER BY id", (trace_id,)
    ).fetchall()
    ok = True
    for r in rows:
        raw = "|".join([r["prev_hash"] or "", r["trace_id"] or "", r["action_id"] or "",
                        r["event_type"], r["payload"], r["created_at"]])
        if hashlib.sha256(raw.encode("utf-8")).hexdigest() != r["hash"]:
            ok = False
            break
    return ok


# --------------------------------------------------------------------------
# 风险与工具
# --------------------------------------------------------------------------

def rule_map(conn):
    return {r["rule_code"]: dict(r) for r in conn.execute("SELECT * FROM risk_rules").fetchall()}


def tool_row(conn, name: str):
    r = conn.execute("SELECT * FROM tools WHERE name=?", (name,)).fetchone()
    return dict(r) if r else None


def tool_enabled(conn, name: str) -> bool:
    row = tool_row(conn, name)
    return bool(row and row["enabled"])


def evaluate_risk(conn, customer: dict, items, total: float, emp: dict):
    reasons = []
    level = "MEDIUM"
    rules = rule_map(conn)

    amt = rules.get("RISK-AMT-001")
    if amt and amt["enabled"] and total > (amt["threshold"] or 50000):
        level = "HIGH"
        reasons.append({"rule": "RISK-AMT-001",
                        "text": "金额 %s 超过中风险阈值 %s" % (money(total), money(amt["threshold"]))})

    new_rule = rules.get("RISK-NEW-002")
    has_orders = conn.execute(
        "SELECT COUNT(*) c FROM orders WHERE customer_code=? AND status != 'DRAFT'",
        (customer["code"],),
    ).fetchone()["c"]
    if new_rule and new_rule["enabled"] and has_orders == 0:
        level = "HIGH"
        reasons.append({"rule": "RISK-NEW-002", "text": "新客户首单，需主管确认"})

    return level, reasons


def build_checks(conn, customer, items, total, warehouse, emp=None):
    checks = []
    credit = credit_snapshot(conn, customer)
    checks.append({
        "ok": credit["remaining"] >= total,
        "text": "客户信用额度剩余 %s（授信 %s）" % (money(credit["remaining"]), money(credit["limit"])),
    })
    for it in items:
        avail = available_stock(conn, it["sku"], warehouse, emp)
        checks.append({
            "ok": avail >= it["qty"],
            "text": "%s 可用 %s 件 ≥ 需求 %s 件，出库后剩余 %s" % (
                it["sku"], format(avail, ","), format(it["qty"], ","), format(max(avail - it["qty"], 0), ",")),
        })
        p = product(conn, it["sku"])
        if p:
            dev = (it["unit_price"] - p["list_price"]) / p["list_price"] if p["list_price"] else 0
            if abs(dev) > 0.15:
                checks.append({"ok": False, "warn": True,
                               "text": "单价 %s 偏离标准价 %s %.1f%%，需勾选确认" % (
                                   money(it["unit_price"]), money(p["list_price"]), dev * 100)})
    return checks


# --------------------------------------------------------------------------
# 草案与落单
# --------------------------------------------------------------------------

def create_draft(conn, emp: dict, customer: dict, sku: str, qty: int, price: float,
                 delivery_date: str, warehouse: str, message: str, trace_id: str):
    if not tool_enabled(conn, "order.create_draft"):
        return {"type": "blocked", "code": "E_TOOL_001", "title": "能力已停用",
                "subtitle": "工具注册表 · order.create_draft",
                "reason": "该工具已被管理员停用，当前无法生成草案。",
                "checks": [{"ok": False, "text": "工具状态：已停用", "tail": "order.create_draft"}],
                "trace_id": trace_id}

    if not in_scope(emp, customer):
        return blocked_scope(conn, customer, emp, trace_id)

    prod = product(conn, sku)
    if not prod or prod["status"] != "ACTIVE":
        return {"type": "blocked", "code": "E_PARAM_001", "title": "商品不可用",
                "subtitle": "商品主数据校验", "reason": "该商品不存在或已停用，请核对 SKU。",
                "checks": [], "trace_id": trace_id}

    allowed = allowed_warehouses(emp)
    if allowed is not None and warehouse not in allowed:
        return {"type": "blocked", "code": "E_PERM_004", "title": "仓库超出数据范围",
                "subtitle": "规则命中 · 仓库权限",
                "reason": "发货仓 %s 不在你的数据范围内，不能从此仓出库。" % warehouse,
                "checks": [{"ok": False, "text": "仓库权限", "tail": warehouse}],
                "trace_id": trace_id}

    if qty <= 0:
        return {"type": "blocked", "code": "E_PARAM_001", "title": "数量不合法",
                "subtitle": "参数校验", "reason": "数量必须为正整数。",
                "checks": [], "trace_id": trace_id}

    avail = available_stock(conn, sku, warehouse, emp)
    if qty > avail:
        audit(conn, trace_id, None, "CHECK_FAILED", {"reason": "stock", "sku": sku, "qty": qty, "available": avail})
        conn.commit()
        return {"type": "blocked", "code": "E_STOCK_001", "title": "库存不足",
                "subtitle": "可用库存校验",
                "reason": "%s 在所选仓库可用 %s 件，少于所需 %s 件，无法生成可提交的草案。" % (
                    sku, format(avail, ","), format(qty, ",")),
                "checks": [{"ok": True, "text": "客户在数据范围内", "tail": customer["name"]},
                           {"ok": False, "text": "可用库存不足", "tail": "可用 %s / 需求 %s" % (avail, qty)}],
                "trace_id": trace_id}

    items = [{"sku": sku, "name": prod["name"], "qty": qty, "unit_price": price}]
    total = round(qty * price, 2)
    checks = build_checks(conn, customer, items, total, warehouse, emp)
    level, reasons = evaluate_risk(conn, customer, items, total, emp)

    action_id = "act-" + uuid.uuid4().hex[:10]
    draft_no = "SO-DRAFT-" + uuid.uuid4().hex[:4].upper()
    expires = (datetime.now() + timedelta(minutes=30)).isoformat(timespec="seconds")
    draft = {
        "draft_no": draft_no,
        "customer": {"code": customer["code"], "name": customer["name"]},
        "warehouse": warehouse,
        "items": items,
        "total": total,
        "delivery_date": delivery_date,
        "checks": checks,
        "risk": {"level": level, "reasons": reasons},
    }
    conn.execute(
        "INSERT INTO actions(action_id,trace_id,username,intent,tool,params,risk_level,risk_reasons,"
        "status,result,created_at,expires_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (action_id, trace_id, emp["username"], "CREATE_ORDER", "order.create_draft",
         json.dumps({"customer": customer["code"], "items": items, "warehouse": warehouse,
                     "delivery_date": delivery_date, "message": message}, ensure_ascii=False),
         level, json.dumps(reasons, ensure_ascii=False), "PENDING_CONFIRM",
         json.dumps(draft, ensure_ascii=False), now_iso(), expires),
    )
    audit(conn, trace_id, action_id, "DRAFT_CREATED",
          {"draft_no": draft_no, "customer": customer["code"], "total": total, "items": items})
    audit(conn, trace_id, action_id, "RISK_" + level, {"reasons": reasons, "total": total})
    conn.commit()

    checks_payload = []
    for c in checks:
        checks_payload.append({"ok": c.get("ok", True), "warn": c.get("warn", False), "text": c["text"]})

    rules = rule_map(conn)
    threshold = rules.get("RISK-AMT-001", {}).get("threshold")
    if level == "MEDIUM" and threshold:
        condition = "≤ 阈值 " + money(threshold)
    elif level == "HIGH":
        condition = "超过阈值，需主管审批"
    else:
        condition = "按当前策略执行"
    risk_text = "%s风险 · 金额 %s %s｜策略：%s" % (
        {"LOW": "低", "MEDIUM": "中", "HIGH": "高"}[level], money(total), condition,
        "本人确认后提交" if level == "MEDIUM" else ("提交主管审批" if level == "HIGH" else "直接执行"),
    )
    return {
        "type": "draft",
        "action_id": action_id,
        "trace_id": trace_id,
        "draft_no": draft_no,
        "draft": draft,
        "risk": {"level": level, "reasons": reasons, "text": risk_text,
                 "rule": reasons[0]["rule"] if reasons else "RISK-AMT-001"},
        "expires_at": expires,
        "text": "已生成货单草案，%d 项校验完成。金额 %s，判定为%s风险。" % (
            len(checks), money(total), {"LOW": "低", "MEDIUM": "中", "HIGH": "高"}[level]),
    }


def blocked_scope(conn, customer, emp, trace_id):
    audit(conn, trace_id, None, "BLOCKED",
          {"code": "E_PERM_001", "customer": customer["code"],
           "target_region": customer["region"], "employee": emp["username"]})
    conn.commit()
    region_label = {"EAST": "华东大区", "SOUTH": "华南大区", "NORTH": "华北大区"}.get(
        customer["region"], customer["region"])
    return {
        "type": "blocked",
        "code": "E_PERM_001",
        "title": "请求已被拒绝",
        "subtitle": "规则命中 · 数据范围校验",
        "text": "我尝试执行这个请求，但在数据范围校验环节被拦下了 —— 没有任何数据被读取。",
        "reason": "该客户（%s %s）归属%s，不在你的数据范围内。请求未执行，也未读取任何订单数据。" % (
            customer["code"], customer["name"], region_label),
        "checks": [
            {"ok": True, "text": "身份校验", "tail": "%s · %s" % (emp["name"], emp["scope_label"])},
            {"ok": True, "text": "工具授权", "tail": "order.query 已授权"},
            {"ok": False, "text": "数据范围", "tail": "目标归属 " + region_label},
        ],
        "trace_id": trace_id,
    }


def confirm_action(conn, emp: dict, action_id: str, idempotency_key: str = None):
    row = conn.execute("SELECT * FROM actions WHERE action_id=?", (action_id,)).fetchone()
    if not row:
        return {"error": "E_NOT_FOUND", "message": "动作不存在或已过期"}
    if row["username"] != emp["username"]:
        return {"error": "E_AUTH_002", "message": "只能确认自己发起的动作"}

    if row["status"] == "DONE":
        result = jloads(row["result"], {})
        return {"type": "receipt", "order_no": result.get("order_no"), "trace_id": row["trace_id"],
                "message": "该动作已执行，返回首次结果（幂等）", "idempotent": True}

    if row["status"] != "PENDING_CONFIRM":
        return {"error": "E_FLOW_001", "message": "当前状态不允许确认：%s" % row["status"]}

    expires = row["expires_at"]
    if expires and datetime.fromisoformat(expires) < datetime.now():
        conn.execute("UPDATE actions SET status='EXPIRED' WHERE action_id=?", (action_id,))
        conn.commit()
        return {"error": "E_DRAFT_EXPIRED", "message": "草案已超过 30 分钟有效期，请重新生成"}

    draft = jloads(row["result"], {})
    customer = customer_by_code(conn, draft["customer"]["code"])
    if not in_scope(emp, customer):
        audit(conn, row["trace_id"], action_id, "BLOCKED", {"code": "E_PERM_001"})
        conn.commit()
        return {"error": "E_PERM_001", "message": "该客户不在你的数据范围内"}

    for it in draft["items"]:
        avail = available_stock(conn, it["sku"], draft["warehouse"], emp)
        if avail < it["qty"]:
            audit(conn, row["trace_id"], action_id, "CHECK_FAILED",
                  {"reason": "stock_changed", "sku": it["sku"], "available": avail})
            conn.commit()
            return {"error": "E_STOCK_001", "message": "%s 可用库存已变为 %d，请重新生成草案" % (it["sku"], avail)}

    risk_level = row["risk_level"]
    audit(conn, row["trace_id"], action_id, "CONFIRM", {"by": emp["username"], "risk": risk_level})

    if risk_level == "HIGH":
        approval_id = "AP-" + datetime.now().strftime("%Y%m%d") + "-" + uuid.uuid4().hex[:3].upper()
        reasons = jloads(row["risk_reasons"], [])
        checks = [{"ok": c.get("ok", True), "text": c.get("text", "")} for c in draft.get("checks", [])]
        conn.execute(
            "INSERT INTO approvals(approval_id,action_id,type,applicant,customer_code,customer_name,"
            "amount,rule_code,reason,risk_level,payload,checks,status,created_at,sla_deadline) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,'PENDING',?,?)",
            (approval_id, action_id, "创建货单", emp["username"], customer["code"], customer["name"],
             draft["total"], reasons[0]["rule"] if reasons else "RISK-AMT-001",
             reasons[0]["text"] if reasons else "高风险动作需审批", "HIGH",
             json.dumps(draft, ensure_ascii=False), json.dumps(checks, ensure_ascii=False),
             now_iso(), (datetime.now() + timedelta(hours=24)).isoformat(timespec="seconds")),
        )
        conn.execute("UPDATE actions SET status='PENDING_APPROVAL' WHERE action_id=?", (action_id,))
        audit(conn, row["trace_id"], action_id, "APPROVAL_CREATED", {"approval_id": approval_id})
        conn.commit()
        return {"type": "pending_approval", "approval_id": approval_id, "trace_id": row["trace_id"],
                "text": "金额超过阈值，已提交销售主管审批；审批通过后自动落单。"}

    receipt = execute_order(conn, action_id, row["trace_id"], draft, emp)
    return {"type": "receipt", "trace_id": row["trace_id"], **receipt}


def next_order_no(conn):
    key = "order:" + today().strftime("%Y%m%d")
    seq = db.incr_counter(conn, key)
    return "SO-%s-%04d" % (today().strftime("%Y%m%d"), seq)


def execute_order(conn, action_id, trace_id, draft, emp, approved_by=None):
    order_no = next_order_no(conn)
    customer = customer_by_code(conn, draft["customer"]["code"])
    total = round(sum(i["qty"] * i["unit_price"] for i in draft["items"]), 2)
    conn.execute(
        "INSERT INTO orders(order_no,customer_code,status,total_amount,delivery_date,warehouse,"
        "created_by,created_at,approved_at,terms_days,trace_id) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (order_no, customer["code"], "APPROVED", total, draft["delivery_date"], draft["warehouse"],
         emp["username"], now_iso(), now_iso(), 30, trace_id),
    )
    for item in draft["items"]:
        conn.execute(
            "INSERT INTO order_items(order_no,sku,qty,unit_price) VALUES(?,?,?,?)",
            (order_no, item["sku"], item["qty"], item["unit_price"]),
        )
        conn.execute(
            "UPDATE inventory SET locked = locked + ? WHERE sku=? AND warehouse=?",
            (item["qty"], item["sku"], draft["warehouse"]),
        )
    if action_id:
        conn.execute(
            "UPDATE actions SET status='DONE', tool='order.submit', result=? WHERE action_id=?",
            (json.dumps({"order_no": order_no, "status": "APPROVED"}, ensure_ascii=False), action_id),
        )
    audit(conn, trace_id, action_id, "ORDER_CREATED",
          {"order_no": order_no, "customer": customer["code"], "total": total,
           "approved_by": approved_by or emp["username"]})
    audit(conn, trace_id, action_id, "STOCK_LOCKED",
          {"order_no": order_no, "items": draft["items"], "warehouse": draft["warehouse"]})
    conn.commit()
    return {"order_no": order_no, "status": "APPROVED", "amount": total,
            "message": "货单已创建并锁定库存"}


# --------------------------------------------------------------------------
# 出货 / 开票
# --------------------------------------------------------------------------

def ship_order(conn, emp, order_no: str, qty: int, warehouse: str, trace_id=None, action_id=None):
    order = conn.execute("SELECT * FROM orders WHERE order_no=?", (order_no,)).fetchone()
    if not order:
        return {"error": "E_NOT_FOUND", "message": "货单不存在"}
    if order["status"] not in ("APPROVED", "PARTIAL"):
        return {"error": "E_FLOW_001", "message": "货单当前为 %s，不允许出货" % order["status"]}
    item = conn.execute("SELECT * FROM order_items WHERE order_no=? LIMIT 1", (order_no,)).fetchone()
    remaining = item["qty"] - item["shipped_qty"]
    if qty <= 0 or qty > remaining:
        return {"error": "E_PARAM_001", "message": "出货数量需在 1~%d 之间" % remaining}

    trace_id = trace_id or "tr-" + uuid.uuid4().hex[:8]
    shipment_no = "SH-" + datetime.now().strftime("%Y%m%d") + "-" + uuid.uuid4().hex[:4].upper()
    conn.execute(
        "INSERT INTO shipments(shipment_no,order_no,qty,warehouse,status,created_at,created_by) "
        "VALUES(?,?,?,?,'DONE',?,?)",
        (shipment_no, order_no, qty, warehouse, now_iso(), emp["username"]),
    )
    conn.execute("UPDATE order_items SET shipped_qty = shipped_qty + ? WHERE id=?", (qty, item["id"]))
    conn.execute(
        "UPDATE inventory SET on_hand = on_hand - ?, locked = MAX(locked - ?, 0) WHERE sku=? AND warehouse=?",
        (qty, qty, item["sku"], warehouse),
    )
    new_status = "SHIPPED" if qty == remaining else "PARTIAL"
    conn.execute(
        "UPDATE orders SET status=?, shipped_at=COALESCE(shipped_at, ?) WHERE order_no=?",
        (new_status, now_iso(), order_no),
    )
    audit(conn, trace_id, action_id, "SHIPMENT_CREATED",
          {"shipment_no": shipment_no, "order_no": order_no, "qty": qty})
    conn.commit()
    return {"type": "receipt", "shipment_no": shipment_no, "order_no": order_no,
            "status": new_status, "trace_id": trace_id, "message": "出货登记完成"}


def issue_invoice(conn, emp, order_no: str, amount: float, tax_type="VAT_13", trace_id=None, action_id=None):
    if not role_allows(emp, ["FINANCE", "FINANCE_DIRECTOR"]):
        return {"error": "E_PERM_002", "message": "当前角色没有开票权限"}
    order = conn.execute("SELECT * FROM orders WHERE order_no=?", (order_no,)).fetchone()
    if not order:
        return {"error": "E_NOT_FOUND", "message": "货单不存在"}
    if order["status"] != "SHIPPED":
        return {"error": "E_FLOW_002", "message": "货单当前为 %s，未出货不能开票" % order["status"]}
    trace_id = trace_id or "tr-" + uuid.uuid4().hex[:8]
    invoice_no = "INV-" + datetime.now().strftime("%Y%m%d") + "-" + uuid.uuid4().hex[:4].upper()
    conn.execute(
        "INSERT INTO invoices(invoice_no,order_no,amount,tax_type,status,created_at,created_by) "
        "VALUES(?,?,?,?, 'ISSUED', ?, ?)",
        (invoice_no, order_no, amount, tax_type, now_iso(), emp["username"]),
    )
    conn.execute("UPDATE orders SET status='INVOICED', invoiced_at=? WHERE order_no=?",
                 (now_iso(), order_no))
    audit(conn, trace_id, action_id, "INVOICE_ISSUED",
          {"invoice_no": invoice_no, "order_no": order_no, "amount": amount, "tax": tax_type})
    conn.commit()
    return {"type": "receipt", "invoice_no": invoice_no, "order_no": order_no,
            "status": "INVOICED", "trace_id": trace_id, "message": "发票已开具"}


# --------------------------------------------------------------------------
# 传统 ERP：列表 / 手工开单 / 整单出货
# --------------------------------------------------------------------------

ORDER_STATUS_LABEL = {
    "DRAFT": "草稿", "PENDING_APPROVAL": "待审核", "APPROVED": "已审核",
    "PARTIAL": "部分出货", "SHIPPED": "已出货", "INVOICED": "已开票",
    "CLOSED": "已关闭", "REJECTED": "已驳回", "CANCELLED": "已取消",
}


def role_allows(emp, roles):
    return "*" in roles or emp["role_code"] in roles


def _region_clause(emp, alias="c"):
    if "*" in emp["scope"]:
        return "", []
    return " AND %s.region IN (%s)" % (alias, ",".join("?" * len(emp["scope"]))), list(emp["scope"])


def list_orders(conn, emp, status=None, q=None):
    sql = (
        "SELECT o.*, c.name AS customer_name, c.region, w.name AS warehouse_name, "
        "(SELECT COALESCE(SUM(qty),0) FROM order_items i WHERE i.order_no=o.order_no) AS total_qty, "
        "(SELECT COALESCE(SUM(shipped_qty),0) FROM order_items i WHERE i.order_no=o.order_no) AS shipped_qty "
        "FROM orders o JOIN customers c ON c.code=o.customer_code "
        "LEFT JOIN warehouses w ON w.code=o.warehouse WHERE 1=1"
    )
    params = []
    clause, cp = _region_clause(emp)
    sql += clause
    params += cp
    if status and status != "ALL":
        if status == "OPEN":
            sql += " AND o.status IN ('PENDING_APPROVAL','APPROVED','PARTIAL')"
        else:
            sql += " AND o.status=?"
            params.append(status)
    if q:
        sql += " AND (o.order_no LIKE ? OR c.name LIKE ?)"
        params += ["%" + q + "%", "%" + q + "%"]
    sql += " ORDER BY o.created_at DESC LIMIT 200"
    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    for r in rows:
        r["status_label"] = ORDER_STATUS_LABEL.get(r["status"], r["status"])
    return rows


def order_detail(conn, emp, order_no):
    row = conn.execute(
        "SELECT o.*, c.name AS customer_name, c.contact, c.address, c.region, c.credit_limit, "
        "w.name AS warehouse_name FROM orders o JOIN customers c ON c.code=o.customer_code "
        "LEFT JOIN warehouses w ON w.code=o.warehouse WHERE o.order_no=?", (order_no,)
    ).fetchone()
    if not row:
        return None
    data = dict(row)
    if not in_scope(emp, {"region": data["region"]}):
        return None
    data["status_label"] = ORDER_STATUS_LABEL.get(data["status"], data["status"])
    data["items"] = [dict(r) for r in conn.execute(
        "SELECT i.*, p.name AS product_name FROM order_items i LEFT JOIN products p ON p.sku=i.sku "
        "WHERE i.order_no=?", (order_no,)).fetchall()]
    data["shipments"] = [dict(r) for r in conn.execute(
        "SELECT * FROM shipments WHERE order_no=? ORDER BY created_at", (order_no,)).fetchall()]
    data["invoices"] = [dict(r) for r in conn.execute(
        "SELECT * FROM invoices WHERE order_no=? ORDER BY created_at", (order_no,)).fetchall()]
    data["events"] = [dict(r) for r in conn.execute(
        "SELECT event_type, payload, created_at FROM audit_events WHERE trace_id=? ORDER BY id",
        (data["trace_id"],)).fetchall()]
    return data


def list_inventory(conn, emp, q=None):
    sql = (
        "SELECT i.*, p.name AS product_name, p.unit, w.name AS warehouse_name "
        "FROM inventory i JOIN products p ON p.sku=i.sku JOIN warehouses w ON w.code=i.warehouse "
        "WHERE 1=1"
    )
    params = []
    allowed = allowed_warehouses(emp)
    if allowed is not None:
        sql += " AND i.warehouse IN (%s)" % ",".join("?" * len(allowed))
        params += allowed
    if q:
        sql += " AND (i.sku LIKE ? OR p.name LIKE ?)"
        params += ["%" + q + "%", "%" + q + "%"]
    sql += " ORDER BY i.sku, i.warehouse"
    rows = []
    for r in conn.execute(sql, params).fetchall():
        d = dict(r)
        d["available"] = d["on_hand"] - d["locked"] + d["inbound_available"]
        d["stock_state"] = "低库存" if d["available"] < 100 else "正常"
        rows.append(d)
    return rows


def list_shipments(conn, emp):
    clause, params = _region_clause(emp)
    rows = conn.execute(
        "SELECT s.*, o.customer_code, c.name AS customer_name, w.name AS warehouse_name "
        "FROM shipments s JOIN orders o ON o.order_no=s.order_no "
        "JOIN customers c ON c.code=o.customer_code LEFT JOIN warehouses w ON w.code=s.warehouse "
        "WHERE 1=1" + clause + " ORDER BY s.created_at DESC LIMIT 200", params
    ).fetchall()
    return [dict(r) for r in rows]


def list_invoices(conn, emp):
    clause, params = _region_clause(emp)
    rows = conn.execute(
        "SELECT i.*, o.customer_code, c.name AS customer_name "
        "FROM invoices i JOIN orders o ON o.order_no=i.order_no "
        "JOIN customers c ON c.code=o.customer_code WHERE 1=1" + clause +
        " ORDER BY i.created_at DESC LIMIT 200", params
    ).fetchall()
    return [dict(r) for r in rows]


def list_inbounds(conn, emp):
    sql = (
        "SELECT i.*, p.name AS product_name, w.name AS warehouse_name, e.name AS operator_name "
        "FROM inbounds i LEFT JOIN products p ON p.sku=i.sku "
        "LEFT JOIN warehouses w ON w.code=i.warehouse "
        "LEFT JOIN employees e ON e.username=i.created_by WHERE 1=1"
    )
    params = []
    allowed = allowed_warehouses(emp)
    if allowed is not None:
        sql += " AND i.warehouse IN (%s)" % ",".join("?" * len(allowed))
        params += allowed
    sql += " ORDER BY i.created_at DESC LIMIT 200"
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def create_inbound(conn, emp, data):
    if not role_allows(emp, ["WAREHOUSE", "RISK_ADMIN", "SALES_MANAGER", "SALES_FOLLOWER"]):
        return {"error": "E_PERM_002", "message": "当前角色没有入库权限"}
    supplier = (data.get("supplier") or "").strip()
    if not supplier:
        return {"error": "E_PARAM_001", "message": "请填写供应商"}
    prod = product(conn, data.get("sku"))
    if not prod:
        return {"error": "E_PARAM_001", "message": "商品不存在"}
    qty = int(data.get("qty") or 0)
    if qty <= 0:
        return {"error": "E_PARAM_001", "message": "入库数量必须大于 0"}
    warehouse = data.get("warehouse") or "WH-SH"
    allowed = allowed_warehouses(emp)
    if allowed is not None and warehouse not in allowed:
        return {"error": "E_PERM_004", "message": "仓库不在你的数据范围内"}

    inbound_no = "IN-" + datetime.now().strftime("%Y%m%d") + "-" + uuid.uuid4().hex[:4].upper()
    trace = "tr-" + uuid.uuid4().hex[:8]
    conn.execute(
        "INSERT INTO inbounds(inbound_no,supplier,sku,qty,warehouse,status,created_at,created_by) "
        "VALUES(?,?,?,?,?,'DONE',?,?)",
        (inbound_no, supplier, prod["sku"], qty, warehouse, now_iso(), emp["username"]),
    )
    conn.execute(
        "UPDATE inventory SET on_hand = on_hand + ?, inbound = MAX(inbound - ?, 0) "
        "WHERE sku=? AND warehouse=?", (qty, qty, prod["sku"], warehouse),
    )
    audit(conn, trace, None, "INBOUND_CREATED",
          {"inbound_no": inbound_no, "supplier": supplier, "sku": prod["sku"],
           "qty": qty, "warehouse": warehouse})
    conn.commit()
    return {"inbound_no": inbound_no, "qty": qty, "sku": prod["sku"],
            "warehouse": warehouse, "trace_id": trace}


def create_manual_order(conn, emp, data):
    if not role_allows(emp, ["SALES_FOLLOWER", "SALES_MANAGER"]):
        return {"error": "E_PERM_002", "message": "当前角色没有开单权限"}
    customer = customer_by_code(conn, data.get("customerCode", ""))
    if not customer:
        return {"error": "E_PARAM_001", "message": "客户不存在"}
    if not in_scope(emp, customer):
        return {"error": "E_PERM_001", "message": "该客户不在你的数据范围内"}
    warehouse = data.get("warehouse") or "WH-SH"
    allowed = allowed_warehouses(emp)
    if allowed is not None and warehouse not in allowed:
        return {"error": "E_PERM_004", "message": "仓库不在你的数据范围内"}

    items = []
    for raw in data.get("items", []):
        prod = product(conn, raw.get("sku"))
        qty = int(raw.get("qty") or 0)
        price = float(raw.get("unitPrice") or 0)
        if not prod or prod["status"] != "ACTIVE":
            return {"error": "E_PARAM_001", "message": "商品不存在或已停用"}
        if qty <= 0 or price <= 0:
            return {"error": "E_PARAM_001", "message": "数量与单价必须大于 0"}
        avail = available_stock(conn, prod["sku"], warehouse, emp)
        if qty > avail:
            return {"error": "E_STOCK_001",
                    "message": "%s 在所选仓库可用 %d 件，不足 %d 件" % (prod["sku"], avail, qty)}
        items.append({"sku": prod["sku"], "name": prod["name"], "qty": qty, "unit_price": price})
    if not items:
        return {"error": "E_PARAM_001", "message": "至少需要一行商品"}

    total = round(sum(i["qty"] * i["unit_price"] for i in items), 2)
    checks = build_checks(conn, customer, items, total, warehouse, emp)
    level, reasons = evaluate_risk(conn, customer, items, total, emp)

    order_no = next_order_no(conn)
    trace = "tr-" + uuid.uuid4().hex[:8]
    status = "PENDING_APPROVAL" if level == "HIGH" else "APPROVED"
    conn.execute(
        "INSERT INTO orders(order_no,customer_code,status,total_amount,delivery_date,warehouse,"
        "created_by,created_at,approved_at,terms_days,trace_id,note) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (order_no, customer["code"], status, total,
         data.get("deliveryDate") or (today() + timedelta(days=7)).isoformat(),
         warehouse, emp["username"], now_iso(),
         None if status == "PENDING_APPROVAL" else now_iso(), 30, trace, data.get("note", "")),
    )
    for item in items:
        conn.execute("INSERT INTO order_items(order_no,sku,qty,unit_price) VALUES(?,?,?,?)",
                     (order_no, item["sku"], item["qty"], item["unit_price"]))
    audit(conn, trace, None, "ORDER_CREATED",
          {"order_no": order_no, "customer": customer["code"], "total": total, "channel": "ERP_FORM"})

    approval_id = None
    if status == "APPROVED":
        for item in items:
            conn.execute("UPDATE inventory SET locked=locked+? WHERE sku=? AND warehouse=?",
                         (item["qty"], item["sku"], warehouse))
        audit(conn, trace, None, "STOCK_LOCKED", {"order_no": order_no, "items": items})
    else:
        approval_id = "AP-" + datetime.now().strftime("%Y%m%d") + "-" + uuid.uuid4().hex[:3].upper()
        reason = reasons[0]["text"] if reasons else "金额超过阈值，需主管审批"
        conn.execute(
            "INSERT INTO approvals(approval_id,action_id,type,applicant,customer_code,customer_name,"
            "amount,rule_code,reason,risk_level,payload,checks,status,created_at,sla_deadline) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,'PENDING',?,?)",
            (approval_id, "order:" + order_no, "订单审核", emp["username"], customer["code"],
             customer["name"], total, reasons[0]["rule"] if reasons else "RISK-AMT-001", reason, "HIGH",
             json.dumps({"order_no": order_no, "trace_id": trace, "items": items,
                         "warehouse": warehouse}, ensure_ascii=False),
             json.dumps([{"ok": c.get("ok", True), "text": c["text"]} for c in checks],
                        ensure_ascii=False),
             now_iso(), (datetime.now() + timedelta(hours=24)).isoformat(timespec="seconds")),
        )
        audit(conn, trace, None, "APPROVAL_CREATED", {"approval_id": approval_id, "order_no": order_no})
    conn.commit()
    return {"order_no": order_no, "status": status, "risk": level, "approval_id": approval_id,
            "total": total, "trace_id": trace}


def ship_full_order(conn, emp, order_no):
    if not role_allows(emp, ["WAREHOUSE", "SALES_FOLLOWER", "SALES_MANAGER"]):
        return {"error": "E_PERM_002", "message": "当前角色没有出货权限"}
    order = conn.execute("SELECT * FROM orders WHERE order_no=?", (order_no,)).fetchone()
    if not order:
        return {"error": "E_NOT_FOUND", "message": "货单不存在"}
    if order["status"] not in ("APPROVED", "PARTIAL"):
        return {"error": "E_FLOW_001", "message": "货单当前为 %s，不允许出货" % order["status"]}
    items = conn.execute("SELECT * FROM order_items WHERE order_no=?", (order_no,)).fetchall()
    total_qty = 0
    for item in items:
        remain = item["qty"] - item["shipped_qty"]
        if remain <= 0:
            continue
        conn.execute(
            "UPDATE inventory SET on_hand=on_hand-?, locked=MAX(locked-?,0) WHERE sku=? AND warehouse=?",
            (remain, remain, item["sku"], order["warehouse"]),
        )
        conn.execute("UPDATE order_items SET shipped_qty=qty WHERE id=?", (item["id"],))
        total_qty += remain
    if total_qty <= 0:
        return {"error": "E_FLOW_001", "message": "没有待出货数量"}
    shipment_no = "SH-" + datetime.now().strftime("%Y%m%d") + "-" + uuid.uuid4().hex[:4].upper()
    conn.execute(
        "INSERT INTO shipments(shipment_no,order_no,qty,warehouse,status,created_at,created_by) "
        "VALUES(?,?,?,?,'DONE',?,?)",
        (shipment_no, order_no, total_qty, order["warehouse"], now_iso(), emp["username"]),
    )
    conn.execute("UPDATE orders SET status='SHIPPED', shipped_at=? WHERE order_no=?",
                 (now_iso(), order_no))
    trace = order["trace_id"] or "tr-" + uuid.uuid4().hex[:8]
    audit(conn, trace, None, "SHIPMENT_CREATED",
          {"shipment_no": shipment_no, "order_no": order_no, "qty": total_qty,
           "warehouse": order["warehouse"]})
    conn.commit()
    return {"shipment_no": shipment_no, "order_no": order_no, "qty": total_qty,
            "status": "SHIPPED", "trace_id": trace}


def _normalize_items(conn, emp, warehouse, raw_items):
    items = []
    for raw in raw_items or []:
        prod = product(conn, raw.get("sku"))
        qty = int(raw.get("qty") or 0)
        price = float(raw.get("unitPrice") or 0)
        if not prod or prod["status"] != "ACTIVE":
            return None, "商品不存在或已停用"
        if qty <= 0 or price <= 0:
            return None, "数量与单价必须大于 0"
        avail = available_stock(conn, prod["sku"], warehouse, emp)
        if qty > avail:
            return None, "%s 在所选仓库可用 %d 件，不足 %d 件" % (prod["sku"], avail, qty)
        items.append({"sku": prod["sku"], "name": prod["name"], "qty": qty, "unit_price": price})
    if not items:
        return None, "至少需要一行商品"
    return items, None


def _pending_approval_for_order(conn, order_no):
    for row in conn.execute("SELECT * FROM approvals WHERE status='PENDING'").fetchall():
        if jloads(row["payload"], {}).get("order_no") == order_no:
            return row
    return None


def update_pending_order(conn, emp, order_no, data):
    if not role_allows(emp, ["SALES_FOLLOWER", "SALES_MANAGER", "RISK_ADMIN"]):
        return {"error": "E_PERM_002", "message": "当前角色没有修改订单权限"}
    order = conn.execute("SELECT * FROM orders WHERE order_no=?", (order_no,)).fetchone()
    if not order:
        return {"error": "E_NOT_FOUND", "message": "订单不存在"}
    customer = customer_by_code(conn, order["customer_code"])
    if not in_scope(emp, customer):
        return {"error": "E_PERM_001", "message": "该客户不在你的数据范围内"}
    if order["status"] != "PENDING_APPROVAL":
        return {"error": "E_FLOW_001", "message": "只有「待审核」订单可以修改"}

    warehouse = data.get("warehouse") or order["warehouse"]
    items, err = _normalize_items(conn, emp, warehouse, data.get("items"))
    if err:
        return {"error": "E_PARAM_001", "message": err}

    total = round(sum(i["qty"] * i["unit_price"] for i in items), 2)
    checks = build_checks(conn, customer, items, total, warehouse, emp)
    level, reasons = evaluate_risk(conn, customer, items, total, emp)
    delivery = data.get("deliveryDate") or order["delivery_date"]

    conn.execute(
        "UPDATE orders SET total_amount=?, delivery_date=?, warehouse=?, note=? WHERE order_no=?",
        (total, delivery, warehouse, data.get("note", order["note"] or ""), order_no),
    )
    conn.execute("DELETE FROM order_items WHERE order_no=?", (order_no,))
    for item in items:
        conn.execute("INSERT INTO order_items(order_no,sku,qty,unit_price) VALUES(?,?,?,?)",
                     (order_no, item["sku"], item["qty"], item["unit_price"]))

    approval = _pending_approval_for_order(conn, order_no)
    if approval:
        conn.execute(
            "UPDATE approvals SET amount=?, rule_code=?, reason=?, payload=?, checks=? WHERE approval_id=?",
            (total,
             reasons[0]["rule"] if reasons else "RISK-AMT-001",
             reasons[0]["text"] if reasons else "金额超过阈值，需主管审批",
             json.dumps({"order_no": order_no, "trace_id": order["trace_id"], "items": items,
                         "warehouse": warehouse}, ensure_ascii=False),
             json.dumps([{"ok": c.get("ok", True), "text": c["text"]} for c in checks],
                        ensure_ascii=False), approval["approval_id"]),
        )
    trace = order["trace_id"] or "tr-" + uuid.uuid4().hex[:8]
    audit(conn, trace, None, "ORDER_UPDATED",
          {"order_no": order_no, "total": total, "risk": level, "by": emp["username"]})
    conn.commit()
    return {"order_no": order_no, "status": order["status"], "total": total, "risk": level,
            "trace_id": trace}


def approve_order(conn, emp, order_no, decision="APPROVE", comment=""):
    if not role_allows(emp, ["SALES_MANAGER", "RISK_ADMIN"]):
        return {"error": "E_PERM_002", "message": "当前角色没有订单审核权限"}
    order = conn.execute("SELECT * FROM orders WHERE order_no=?", (order_no,)).fetchone()
    if not order:
        return {"error": "E_NOT_FOUND", "message": "订单不存在"}
    customer = customer_by_code(conn, order["customer_code"])
    if not in_scope(emp, customer):
        return {"error": "E_PERM_001", "message": "该客户不在你的数据范围内"}
    if order["status"] != "PENDING_APPROVAL":
        return {"error": "E_FLOW_001", "message": "订单当前不是待审核状态"}

    approval = _pending_approval_for_order(conn, order_no)
    trace = order["trace_id"] or "tr-" + uuid.uuid4().hex[:8]

    if decision == "REJECT":
        conn.execute("UPDATE orders SET status='REJECTED' WHERE order_no=?", (order_no,))
        if approval:
            conn.execute(
                "UPDATE approvals SET status='REJECTED', approver=?, comment=?, decided_at=? "
                "WHERE approval_id=?", (emp["username"], comment, now_iso(), approval["approval_id"]))
        audit(conn, trace, None, "ORDER_REJECTED",
              {"order_no": order_no, "by": emp["username"], "comment": comment})
        conn.commit()
        return {"order_no": order_no, "status": "REJECTED", "trace_id": trace}

    items = conn.execute("SELECT * FROM order_items WHERE order_no=?", (order_no,)).fetchall()
    for item in items:
        avail = available_stock(conn, item["sku"], order["warehouse"], emp)
        if avail < item["qty"]:
            return {"error": "E_STOCK_001",
                    "message": "%s 可用库存已变为 %d，无法审核通过" % (item["sku"], avail)}
    for item in items:
        conn.execute("UPDATE inventory SET locked=locked+? WHERE sku=? AND warehouse=?",
                     (item["qty"], item["sku"], order["warehouse"]))
    conn.execute("UPDATE orders SET status='APPROVED', approved_at=? WHERE order_no=?",
                 (now_iso(), order_no))
    if approval:
        conn.execute(
            "UPDATE approvals SET status='APPROVED', approver=?, comment=?, decided_at=? "
            "WHERE approval_id=?", (emp["username"], comment, now_iso(), approval["approval_id"]))
    audit(conn, trace, None, "ORDER_APPROVED",
          {"order_no": order_no, "by": emp["username"], "comment": comment})
    conn.commit()
    return {"order_no": order_no, "status": "APPROVED", "trace_id": trace}


def delete_order(conn, emp, order_no):
    order = conn.execute("SELECT * FROM orders WHERE order_no=?", (order_no,)).fetchone()
    if not order:
        return {"error": "E_NOT_FOUND", "message": "订单不存在"}
    if order["status"] not in ("PENDING_APPROVAL", "REJECTED"):
        return {"error": "E_FLOW_001", "message": "已审核订单请先「取消订单」，不能直接删除"}
    if order["created_by"] != emp["username"] and not role_allows(emp, ["SALES_MANAGER", "RISK_ADMIN"]):
        return {"error": "E_PERM_002", "message": "只能删除自己创建的订单"}
    approval = _pending_approval_for_order(conn, order_no)
    if approval:
        conn.execute("DELETE FROM approvals WHERE approval_id=?", (approval["approval_id"],))
    conn.execute("DELETE FROM order_items WHERE order_no=?", (order_no,))
    conn.execute("DELETE FROM orders WHERE order_no=?", (order_no,))
    audit(conn, order["trace_id"] or "tr-" + uuid.uuid4().hex[:8], None, "ORDER_DELETED",
          {"order_no": order_no, "by": emp["username"]})
    conn.commit()
    return {"order_no": order_no, "deleted": True}


def cancel_order(conn, emp, order_no):
    order = conn.execute("SELECT * FROM orders WHERE order_no=?", (order_no,)).fetchone()
    if not order:
        return {"error": "E_NOT_FOUND", "message": "订单不存在"}
    if order["status"] not in ("APPROVED", "PARTIAL"):
        return {"error": "E_FLOW_001", "message": "只有已审核订单可以取消"}
    if order["created_by"] != emp["username"] and not role_allows(emp, ["SALES_MANAGER", "RISK_ADMIN"]):
        return {"error": "E_PERM_002", "message": "只能取消自己创建的订单"}
    items = conn.execute("SELECT * FROM order_items WHERE order_no=?", (order_no,)).fetchall()
    for item in items:
        remain = item["qty"] - item["shipped_qty"]
        if remain > 0:
            conn.execute(
                "UPDATE inventory SET locked=MAX(locked-?,0) WHERE sku=? AND warehouse=?",
                (remain, item["sku"], order["warehouse"]),
            )
    conn.execute("UPDATE orders SET status='CANCELLED' WHERE order_no=?", (order_no,))
    audit(conn, order["trace_id"] or "tr-" + uuid.uuid4().hex[:8], None, "ORDER_CANCELLED",
          {"order_no": order_no, "by": emp["username"]})
    conn.commit()
    return {"order_no": order_no, "status": "CANCELLED"}


# --------------------------- 基础资料（客户 / 商品） ---------------------------

def list_customers(conn, emp):
    clause, params = _region_clause(emp)
    rows = conn.execute("SELECT * FROM customers c WHERE 1=1" + clause + " ORDER BY code",
                        params).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["order_count"] = conn.execute(
            "SELECT COUNT(*) c FROM orders WHERE customer_code=?", (d["code"],)).fetchone()["c"]
        out.append(d)
    return out


def save_customer(conn, emp, code, data):
    if not role_allows(emp, ["SALES_FOLLOWER", "SALES_MANAGER", "RISK_ADMIN"]):
        return {"error": "E_PERM_002", "message": "当前角色没有客户维护权限"}
    name = (data.get("name") or "").strip()
    region = data.get("region") or "EAST"
    if not name:
        return {"error": "E_PARAM_001", "message": "请填写客户名称"}
    if "*" not in emp["scope"] and region not in emp["scope"]:
        return {"error": "E_PERM_001", "message": "不能维护数据范围外的客户"}
    credit = float(data.get("credit_limit") or 0)
    trace = "tr-" + uuid.uuid4().hex[:8]

    if code:
        if not conn.execute("SELECT 1 FROM customers WHERE code=?", (code,)).fetchone():
            return {"error": "E_NOT_FOUND", "message": "客户不存在"}
        conn.execute(
            "UPDATE customers SET name=?, region=?, contact=?, phone=?, address=?, credit_limit=? "
            "WHERE code=?",
            (name, region, data.get("contact", ""), data.get("phone", ""),
             data.get("address", ""), credit, code),
        )
        audit(conn, trace, None, "CUSTOMER_UPDATED", {"code": code, "name": name, "by": emp["username"]})
    else:
        nums = [int(re.sub(r"\D", "", r["code"]) or 0) for r in
                conn.execute("SELECT code FROM customers WHERE code LIKE 'C%'").fetchall()]
        code = "C%05d" % (max(nums + [10000]) + 1)
        conn.execute(
            "INSERT INTO customers(code,name,region,contact,phone,address,credit_limit) "
            "VALUES(?,?,?,?,?,?,?)",
            (code, name, region, data.get("contact", ""), data.get("phone", ""),
             data.get("address", ""), credit),
        )
        audit(conn, trace, None, "CUSTOMER_CREATED", {"code": code, "name": name, "by": emp["username"]})
    conn.commit()
    return {"code": code, "name": name}


def delete_customer(conn, emp, code):
    if not role_allows(emp, ["SALES_MANAGER", "RISK_ADMIN"]):
        return {"error": "E_PERM_002", "message": "只有主管可以删除客户"}
    row = conn.execute("SELECT * FROM customers WHERE code=?", (code,)).fetchone()
    if not row:
        return {"error": "E_NOT_FOUND", "message": "客户不存在"}
    count = conn.execute("SELECT COUNT(*) c FROM orders WHERE customer_code=?", (code,)).fetchone()["c"]
    if count:
        return {"error": "E_FLOW_003", "message": "该客户已有 %d 张订单，不能删除" % count}
    conn.execute("DELETE FROM customers WHERE code=?", (code,))
    audit(conn, "tr-" + uuid.uuid4().hex[:8], None, "CUSTOMER_DELETED",
          {"code": code, "by": emp["username"]})
    conn.commit()
    return {"code": code, "deleted": True}


def list_products(conn, emp):
    return [dict(r) for r in conn.execute("SELECT * FROM products ORDER BY sku").fetchall()]


def save_product(conn, emp, sku, data):
    if not role_allows(emp, ["SALES_MANAGER", "RISK_ADMIN"]):
        return {"error": "E_PERM_002", "message": "只有主管可以维护商品"}
    name = (data.get("name") or "").strip()
    price = float(data.get("list_price") or 0)
    if not name or price <= 0:
        return {"error": "E_PARAM_001", "message": "请填写商品名称与大于 0 的标准价"}
    trace = "tr-" + uuid.uuid4().hex[:8]
    if sku:
        if not conn.execute("SELECT 1 FROM products WHERE sku=?", (sku,)).fetchone():
            return {"error": "E_NOT_FOUND", "message": "商品不存在"}
        conn.execute("UPDATE products SET name=?, category=?, unit=?, list_price=? WHERE sku=?",
                     (name, data.get("category", ""), data.get("unit", "件"), price, sku))
        audit(conn, trace, None, "PRODUCT_UPDATED", {"sku": sku, "name": name, "by": emp["username"]})
    else:
        nums = [int(re.sub(r"\D", "", r["sku"]) or 0) for r in
                conn.execute("SELECT sku FROM products WHERE sku LIKE 'SKU-%'").fetchall()]
        sku = "SKU-%03d" % (max(nums + [0]) + 1)
        conn.execute("INSERT INTO products(sku,name,category,unit,list_price) VALUES(?,?,?,?,?)",
                     (sku, name, data.get("category", ""), data.get("unit", "件"), price))
        audit(conn, trace, None, "PRODUCT_CREATED", {"sku": sku, "name": name, "by": emp["username"]})
    conn.commit()
    return {"sku": sku, "name": name}


def delete_product(conn, emp, sku):
    if not role_allows(emp, ["SALES_MANAGER", "RISK_ADMIN"]):
        return {"error": "E_PERM_002", "message": "只有主管可以删除商品"}
    if not conn.execute("SELECT 1 FROM products WHERE sku=?", (sku,)).fetchone():
        return {"error": "E_NOT_FOUND", "message": "商品不存在"}
    used = conn.execute("SELECT COUNT(*) c FROM order_items WHERE sku=?", (sku,)).fetchone()["c"]
    stock = conn.execute("SELECT COUNT(*) c FROM inventory WHERE sku=? AND on_hand>0", (sku,)).fetchone()["c"]
    if used or stock:
        return {"error": "E_FLOW_003", "message": "商品已有订单或库存，不能删除"}
    conn.execute("DELETE FROM products WHERE sku=?", (sku,))
    audit(conn, "tr-" + uuid.uuid4().hex[:8], None, "PRODUCT_DELETED",
          {"sku": sku, "by": emp["username"]})
    conn.commit()
    return {"sku": sku, "deleted": True}


def erp_overview(conn, emp):
    orders = list_orders(conn, emp)
    open_orders = [o for o in orders if o["status"] in ("PENDING_APPROVAL", "APPROVED", "PARTIAL")]
    pending_ship = [o for o in orders if o["status"] in ("APPROVED", "PARTIAL")]
    pending_approval = [o for o in orders if o["status"] == "PENDING_APPROVAL"]
    inv = list_inventory(conn, emp)
    return {
        "orders": len(orders),
        "open_orders": len(open_orders),
        "open_amount": round(sum(o["total_amount"] for o in open_orders), 2),
        "pending_ship": len(pending_ship),
        "pending_approval": len(pending_approval),
        "skus": len(set(i["sku"] for i in inv)),
        "stock_qty": sum(i["on_hand"] for i in inv),
        "low_stock": sum(1 for i in inv if i["stock_state"] == "低库存"),
    }


# --------------------------------------------------------------------------
# 审批
# --------------------------------------------------------------------------

APPROVER_ROLE = {"开具发票": "FINANCE_DIRECTOR", "发票红冲": "FINANCE_DIRECTOR",
                 "创建货单": "SALES_MANAGER", "出货登记": "SALES_MANAGER",
                 "订单审核": "SALES_MANAGER"}


def approval_visible(emp: dict, approval: dict) -> bool:
    if approval["applicant"] == emp["username"]:
        return True
    if approval["status"] != "PENDING":
        return False
    if emp["role_code"] == "RISK_ADMIN":
        return True
    return emp["role_code"] == APPROVER_ROLE.get(approval["type"])


def can_approve(emp: dict, approval: dict) -> bool:
    if approval["status"] != "PENDING" or approval["applicant"] == emp["username"]:
        return False
    return (emp["role_code"] == "RISK_ADMIN"
            or emp["role_code"] == APPROVER_ROLE.get(approval["type"]))


def list_approvals(conn, emp: dict):
    rows = conn.execute("SELECT * FROM approvals ORDER BY status='PENDING' DESC, created_at DESC").fetchall()
    items = []
    for r in rows:
        item = dict(r)
        item["payload"] = jloads(item["payload"], {})
        item["checks"] = jloads(item["checks"], [])
        item["applicant_name"] = conn.execute(
            "SELECT name FROM employees WHERE username=?", (item["applicant"],)
        ).fetchone()["name"]
        item["visible"] = approval_visible(emp, item)
        item["can_approve"] = can_approve(emp, item)
        item["mine"] = item["applicant"] == emp["username"]
        item["wait_minutes"] = max(0, int((datetime.now() - datetime.fromisoformat(item["created_at"])).total_seconds() // 60))
        if item["visible"]:
            items.append(item)
    return items


def approval_detail(conn, emp, approval_id):
    row = conn.execute("SELECT * FROM approvals WHERE approval_id=?", (approval_id,)).fetchone()
    if not row:
        return None
    data = dict(row)
    data["payload"] = jloads(data["payload"], {})
    data["checks"] = jloads(data["checks"], [])
    data["applicant_name"] = conn.execute(
        "SELECT name FROM employees WHERE username=?", (data["applicant"],)
    ).fetchone()["name"]
    data["can_approve"] = can_approve(emp, data)
    data["mine"] = data["applicant"] == emp["username"]
    return data if approval_visible(emp, data) else None


def decide_approval(conn, emp, approval_id, decision, comment=""):
    row = conn.execute("SELECT * FROM approvals WHERE approval_id=?", (approval_id,)).fetchone()
    if not row:
        return {"error": "E_NOT_FOUND", "message": "审批单不存在"}
    if row["status"] != "PENDING":
        return {"error": "E_FLOW_001", "message": "该审批已处理"}
    if row["applicant"] == emp["username"]:
        return {"error": "E_PERM_003", "message": "不能审批自己发起的单据"}
    if not can_approve(emp, dict(row)):
        return {"error": "E_PERM_002", "message": "当前角色无权审批该单据"}

    payload = jloads(row["payload"], {})
    action = conn.execute("SELECT * FROM actions WHERE action_id=?", (row["action_id"],)).fetchone()
    trace_id = action["trace_id"] if action else (payload.get("trace_id") or "tr-" + uuid.uuid4().hex[:8])

    if decision == "REJECT":
        conn.execute(
            "UPDATE approvals SET status='REJECTED', approver=?, comment=?, decided_at=? WHERE approval_id=?",
            (emp["username"], comment, now_iso(), approval_id),
        )
        if action:
            conn.execute("UPDATE actions SET status='REJECTED' WHERE action_id=?", (row["action_id"],))
        if row["type"] == "订单审核" and payload.get("order_no"):
            conn.execute(
                "UPDATE orders SET status='REJECTED' WHERE order_no=? AND status='PENDING_APPROVAL'",
                (payload["order_no"],))
        audit(conn, trace_id, row["action_id"], "APPROVAL_REJECTED",
              {"approval_id": approval_id, "by": emp["username"], "comment": comment})
        conn.commit()
        return {"type": "rejected", "message": "已驳回"}

    result = None
    applicant_row = conn.execute(
        "SELECT * FROM employees WHERE username=?", (row["applicant"],)
    ).fetchone()
    applicant_emp = employee_payload(applicant_row) if applicant_row else emp
    if row["type"] == "创建货单":
        draft = {
            "customer": {"code": payload.get("customer_code") or row["customer_code"]},
            "warehouse": payload.get("warehouse", "WH-SH"),
            "delivery_date": payload.get("delivery_date", (today() + timedelta(days=7)).isoformat()),
            "items": payload.get("items", []),
        }
        result = execute_order(conn, row["action_id"], trace_id, draft, applicant_emp,
                               approved_by=emp["username"])
    elif row["type"] == "开具发票":
        result = issue_invoice(conn, applicant_emp, payload.get("order_no"), payload.get("amount"),
                               payload.get("tax_type", "VAT_13"), trace_id, row["action_id"])
    elif row["type"] == "出货登记":
        result = ship_order(conn, applicant_emp, payload.get("order_no"), payload.get("qty", 0),
                            payload.get("warehouse", "WH-SH"), trace_id, row["action_id"])
    elif row["type"] == "订单审核":
        order_no = payload.get("order_no")
        order = conn.execute("SELECT * FROM orders WHERE order_no=?", (order_no,)).fetchone()
        if not order or order["status"] != "PENDING_APPROVAL":
            return {"error": "E_FLOW_001", "message": "订单状态已变化，无法审核"}
        items = conn.execute("SELECT * FROM order_items WHERE order_no=?", (order_no,)).fetchall()
        for item in items:
            avail = available_stock(conn, item["sku"], order["warehouse"], applicant_emp)
            if avail < item["qty"]:
                return {"error": "E_STOCK_001",
                        "message": "%s 可用库存已变为 %d" % (item["sku"], avail)}
        for item in items:
            conn.execute("UPDATE inventory SET locked=locked+? WHERE sku=? AND warehouse=?",
                         (item["qty"], item["sku"], order["warehouse"]))
        conn.execute("UPDATE orders SET status='APPROVED', approved_at=? WHERE order_no=?",
                     (now_iso(), order_no))
        audit(conn, trace_id, row["action_id"], "ORDER_APPROVED",
              {"order_no": order_no, "by": emp["username"]})
        result = {"order_no": order_no, "status": "APPROVED"}

    if isinstance(result, dict) and result.get("error"):
        return result

    conn.execute(
        "UPDATE approvals SET status='APPROVED', approver=?, comment=?, decided_at=? WHERE approval_id=?",
        (emp["username"], comment, now_iso(), approval_id),
    )
    conn.execute("UPDATE actions SET status='DONE' WHERE action_id=?", (row["action_id"],))
    audit(conn, trace_id, row["action_id"], "APPROVAL_APPROVED",
          {"approval_id": approval_id, "by": emp["username"]})
    conn.commit()
    return {"type": "approved", "result": result, "message": "已通过并执行"}


# --------------------------------------------------------------------------
# 审计查询
# --------------------------------------------------------------------------

STATUS_LABEL = {"DONE": "成功", "PENDING_APPROVAL": "待审批", "PENDING_CONFIRM": "待确认",
                "REJECTED": "已驳回", "BLOCKED": "已拦截", "EXPIRED": "已过期"}


def list_traces(conn, limit=30):
    rows = conn.execute(
        "SELECT a.*, e.name AS employee_name FROM actions a LEFT JOIN employees e ON e.username=a.username "
        "ORDER BY a.created_at DESC LIMIT ?", (limit,)
    ).fetchall()
    traces = []
    seen = set()
    for r in rows:
        last = conn.execute(
            "SELECT MAX(created_at) t, COUNT(*) c FROM audit_events WHERE trace_id=?", (r["trace_id"],)
        ).fetchone()
        level = r["risk_level"]
        seen.add(r["trace_id"])
        traces.append({
            "trace_id": r["trace_id"], "time": (last["t"] or r["created_at"])[11:19],
            "date": (last["t"] or r["created_at"])[:10],
            "employee": r["employee_name"] or r["username"], "tool": r["tool"],
            "result": STATUS_LABEL.get(r["status"], r["status"]),
            "status": r["status"], "risk": level, "events": last["c"],
            "cost": "%.1fs" % max(0.2, last["c"] * 0.4),
        })
    # 种子订单链路
    orders = conn.execute(
        "SELECT o.*, e.name AS employee_name FROM orders o LEFT JOIN employees e ON e.username=o.created_by "
        "ORDER BY o.created_at DESC LIMIT ?", (limit,)
    ).fetchall()
    for o in orders:
        if o["trace_id"] in seen:
            continue
        last = conn.execute(
            "SELECT MAX(created_at) t, COUNT(*) c FROM audit_events WHERE trace_id=?", (o["trace_id"],)
        ).fetchone()
        result = "成功" if o["status"] in ("SHIPPED", "INVOICED", "APPROVED") else "处理中"
        traces.append({
            "trace_id": o["trace_id"], "time": (last["t"] or o["created_at"])[11:19],
            "date": (last["t"] or o["created_at"])[:10],
            "employee": o["employee_name"] or o["created_by"], "tool": "order.submit",
            "result": result, "status": "DONE" if result == "成功" else "PENDING_CONFIRM",
            "risk": "MEDIUM", "events": last["c"], "cost": "39.2s",
        })

    # 被拦截的请求（没有 action 记录，但有审计）
    blocked = conn.execute(
        "SELECT trace_id, MIN(created_at) t, MAX(created_at) last, COUNT(*) c "
        "FROM audit_events WHERE event_type='BLOCKED' GROUP BY trace_id ORDER BY last DESC LIMIT ?",
        (limit,),
    ).fetchall()
    for b in blocked:
        if b["trace_id"] in seen:
            continue
        first = conn.execute(
            "SELECT payload FROM audit_events WHERE trace_id=? AND event_type='BLOCKED' ORDER BY id LIMIT 1",
            (b["trace_id"],),
        ).fetchone()
        payload = jloads(first["payload"], {}) if first else {}
        intent = conn.execute(
            "SELECT payload FROM audit_events WHERE trace_id=? AND event_type='INTENT' ORDER BY id LIMIT 1",
            (b["trace_id"],),
        ).fetchone()
        intent_payload = jloads(intent["payload"], {}) if intent else {}
        username = payload.get("employee") or intent_payload.get("employee")
        emp_row = conn.execute("SELECT name FROM employees WHERE username=?", (username,)).fetchone() if username else None
        traces.append({
            "trace_id": b["trace_id"], "time": (b["last"] or b["t"])[11:19],
            "date": (b["last"] or b["t"])[:10],
            "employee": (emp_row["name"] if emp_row else "张伟"), "tool": "guard.check",
            "result": "已拦截", "status": "BLOCKED", "risk": "HIGH", "events": b["c"],
            "cost": "0.3s", "code": payload.get("code", "E_PERM_001"),
        })
    traces.sort(key=lambda t: (t["date"], t["time"]), reverse=True)
    return traces[:limit]


def trace_detail(conn, trace_id):
    rows = conn.execute(
        "SELECT * FROM audit_events WHERE trace_id=? ORDER BY id", (trace_id,)
    ).fetchall()
    events = []
    for r in rows:
        events.append({
            "type": r["event_type"], "time": r["created_at"][11:19],
            "payload": jloads(r["payload"], {}), "hash": (r["hash"] or "")[:12],
        })
    action = conn.execute(
        "SELECT a.*, e.name AS employee_name FROM actions a LEFT JOIN employees e ON e.username=a.username "
        "WHERE a.trace_id=? LIMIT 1", (trace_id,)
    ).fetchone()
    order = conn.execute("SELECT * FROM orders WHERE trace_id=? LIMIT 1", (trace_id,)).fetchone()
    employee_name = action["employee_name"] if action else None
    tool_name = action["tool"] if action else "order.submit"
    risk = action["risk_level"] if action else "MEDIUM"
    if not action:
        intent = conn.execute(
            "SELECT payload FROM audit_events WHERE trace_id=? AND event_type='INTENT' ORDER BY id LIMIT 1",
            (trace_id,),
        ).fetchone()
        username = jloads(intent["payload"], {}).get("employee") if intent else None
        if username:
            row = conn.execute("SELECT name FROM employees WHERE username=?", (username,)).fetchone()
            employee_name = row["name"] if row else username
        if any(e["type"] == "BLOCKED" for e in events):
            tool_name = "guard.check"
            risk = "HIGH"
    meta = {
        "employee": employee_name or "张伟",
        "tool": tool_name,
        "risk": risk,
        "total": "%.1fs" % max(1.2, len(events) * 1.4),
    }
    return {"trace_id": trace_id, "meta": meta, "events": events,
            "hash_ok": verify_trace(conn, trace_id)}


# --------------------------------------------------------------------------
# 配置
# --------------------------------------------------------------------------

def list_tools(conn, emp):
    rows = conn.execute("SELECT * FROM tools ORDER BY name").fetchall()
    out = []
    for r in rows:
        roles = jloads(r["roles"], ["*"])
        allowed = "*" in roles or any(role in roles for role in [emp["role_code"]])
        out.append({
            "name": r["name"], "description": r["description"], "risk": r["risk_level"],
            "roles": roles, "version": r["version"], "enabled": bool(r["enabled"]),
            "calls": r["calls_today"], "allowed": allowed,
            "schema": jloads(r["schema_json"], {}),
        })
    return out


def set_tool(conn, name, enabled):
    conn.execute("UPDATE tools SET enabled=? WHERE name=?", (1 if enabled else 0, name))
    conn.commit()


def list_rules(conn):
    rows = conn.execute("SELECT * FROM risk_rules ORDER BY rule_code").fetchall()
    return [dict(r, enabled=bool(r["enabled"])) for r in rows]


def set_rule(conn, code, enabled=None, threshold=None):
    if enabled is not None:
        conn.execute("UPDATE risk_rules SET enabled=? WHERE rule_code=?", (1 if enabled else 0, code))
    if threshold is not None:
        conn.execute("UPDATE risk_rules SET threshold=?, version=version+1 WHERE rule_code=?",
                     (float(threshold), code))
    conn.commit()


def list_knowledge(conn):
    rows = conn.execute("SELECT * FROM knowledge WHERE status='ACTIVE' ORDER BY id").fetchall()
    return [dict(r) for r in rows]


# --------------------------------------------------------------------------
# 外部 ERP 接入配置
# --------------------------------------------------------------------------

DEFAULT_ENDPOINTS = {
    "health": "/api/health",
    "customers": "/api/erp/customers",
    "inventory": "/api/erp/inventory",
    "orders": "/api/erp/orders",
    "shipments": "/api/erp/shipments",
    "invoices": "/api/erp/invoices",
}


def _integration_row(conn):
    row = conn.execute("SELECT * FROM integration_settings WHERE id=1").fetchone()
    if not row:
        conn.execute(
            "INSERT INTO integration_settings(id,endpoints,updated_at) VALUES(1,?,?)",
            (json.dumps(DEFAULT_ENDPOINTS, ensure_ascii=False), now_iso()),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM integration_settings WHERE id=1").fetchone()
    return row


def get_integration(conn):
    row = _integration_row(conn)
    data = dict(row)
    data["endpoints"] = dict(DEFAULT_ENDPOINTS, **jloads(data["endpoints"], {}))
    data["credential_set"] = bool(data.pop("credential", ""))
    data["enabled"] = bool(data["enabled"])
    data["source"] = "EXTERNAL" if data["enabled"] else "BUILTIN"
    return data


def save_integration(conn, payload):
    row = _integration_row(conn)
    vendor = (payload.get("vendor") or "").strip()
    base_url = (payload.get("base_url") or "").strip().rstrip("/")
    auth_type = payload.get("auth_type") or "Bearer"
    endpoints = payload.get("endpoints") or {}
    enabled = 1 if payload.get("enabled") else 0

    credential = row["credential"] or ""
    if payload.get("credential"):
        credential = payload["credential"]
    if payload.get("clear_credential"):
        credential = ""

    if not base_url:
        enabled = 0
    status = "CONFIGURED" if base_url and enabled else ("DISABLED" if base_url else "UNCONFIGURED")
    conn.execute(
        "UPDATE integration_settings SET vendor=?, base_url=?, auth_type=?, credential=?, "
        "endpoints=?, enabled=?, status=?, updated_at=? WHERE id=1",
        (vendor, base_url, auth_type, credential,
         json.dumps(dict(DEFAULT_ENDPOINTS, **endpoints), ensure_ascii=False),
         enabled, status, now_iso()),
    )
    conn.commit()
    return get_integration(conn)


def test_integration(conn=None):
    """测试外部 ERP 连通性。使用独立连接，避免持有全局锁时请求自身造成死锁。"""
    own = conn is None
    conn = conn or db.connect()
    try:
        return _test_integration(conn)
    finally:
        if own:
            conn.close()


def _test_integration(conn):
    data = get_integration(conn)
    base = data["base_url"]
    if not base:
        return {"ok": False, "message": "请先填写外部 ERP 的 Base URL"}
    path = (data["endpoints"] or {}).get("health") or "/api/health"
    target = base + path if not base.endswith(path) else base

    headers = {"Accept": "application/json"}
    row = _integration_row(conn)
    token = row["credential"]
    if token:
        if data["auth_type"] == "APIKey":
            headers["X-API-Key"] = token
        elif data["auth_type"] == "Basic":
            headers["Authorization"] = "Basic " + token
        else:
            headers["Authorization"] = "Bearer " + token

    started = time.time()
    ok = False
    message = ""
    try:
        req = urllib.request.Request(target, headers=headers, method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            body = resp.read(800).decode("utf-8", "ignore")
            ok = 200 <= resp.status < 400
            message = "HTTP %d · %dms · %s" % (resp.status, int((time.time() - started) * 1000), body[:120])
    except urllib.error.HTTPError as exc:
        ok = False
        message = "HTTP %d · %s" % (exc.code, str(exc.reason)[:120])
    except Exception as exc:  # 网络不可达、超时等
        ok = False
        message = "%s: %s" % (type(exc).__name__, str(exc)[:160])

    conn.execute(
        "UPDATE integration_settings SET status=?, last_test_at=?, last_test_message=? WHERE id=1",
        ("CONNECTED" if ok else "FAILED", now_iso(), message),
    )
    conn.commit()
    return {"ok": ok, "message": message, "target": target,
            "latency_ms": int((time.time() - started) * 1000),
            "tested_at": now_iso()}


# --------------------------------------------------------------------------
# 概览
# --------------------------------------------------------------------------

def bootstrap(conn, emp):
    pending_rows = conn.execute("SELECT * FROM approvals WHERE status='PENDING'").fetchall()
    counts = {
        "customers": conn.execute("SELECT COUNT(*) c FROM customers").fetchone()["c"],
        "orders": conn.execute("SELECT COUNT(*) c FROM orders").fetchone()["c"],
        "pending": sum(1 for r in pending_rows if can_approve(emp, dict(r))),
        "mine_pending": sum(1 for r in pending_rows if r["applicant"] == emp["username"]),
        "tools": conn.execute("SELECT COUNT(*) c FROM tools WHERE enabled=1").fetchone()["c"],
        "audit": conn.execute("SELECT COUNT(*) c FROM audit_events").fetchone()["c"],
    }
    return {
        "employee": emp,
        "accounts": demo_accounts(conn),
        "counts": counts,
        "server_time": now_iso(),
        "suggestions": [
            "华东精密上个月还有多少应收没结？顺便看下 A12 的库存。",
            "给华东精密开一张货单，A12 200 件，单价 85，12 月 5 日前送到。",
            "把华南区客户 C2088 的订单全部导出来。",
        ],
    }
