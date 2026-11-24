# -*- coding: utf-8 -*-
"""
ERP-Agent 对话层
把中文请求解析成意图与实体，调用业务内核，返回结构化卡片（数据全部来自数据库）。
"""
import re
import uuid
from datetime import date, timedelta

import core


def money(v):
    return core.money(v)


def trace_id():
    return "tr-" + uuid.uuid4().hex[:8]


def clarify(text, options, trace):
    return {"type": "clarify", "text": text, "options": options, "trace_id": trace}


def guard_sql(msg: str):
    return bool(re.search(r"(update\s+\w+\s+set|delete\s+from|insert\s+into|drop\s+table|alter\s+table|information_schema|直连数据库|直接改库)", msg, re.I))


def parse_qty(msg: str):
    m = re.search(r"(\d{1,6})\s*(?:件|台|个|套)", msg)
    if m:
        return int(m.group(1))
    m = re.search(r"(?:数量|要|开)\s*[:：]?\s*(\d{1,6})", msg)
    if m:
        return int(m.group(1))
    m = re.search(r"(\d{1,6})\s*(?:pcs|PCS)", msg)
    return int(m.group(1)) if m else None


def parse_price(msg: str, default=None):
    m = re.search(r"单价\s*[:：]?\s*(\d+(?:\.\d+)?)", msg)
    if m:
        return float(m.group(1))
    m = re.search(r"(\d+(?:\.\d+)?)\s*元", msg)
    if m:
        return float(m.group(1))
    return default


def parse_warehouse(msg: str, customer):
    if "杭州" in msg:
        return "WH-HZ"
    if "南京" in msg:
        return "WH-NJ"
    if "上海" in msg:
        return "WH-SH"
    if customer and customer["region"] == "SOUTH":
        return "WH-GZ"
    return "WH-SH"


# --------------------------------------------------------------------------
# 卡片
# --------------------------------------------------------------------------

def receivable_card(conn, customer):
    rec = core.receivables(conn, customer["code"])
    credit = core.credit_snapshot(conn, customer)
    rows = []
    for item in rec["items"]:
        status = {"text": item["status"], "tone": "high" if item["overdue_days"] > 0 else "low"}
        rows.append({
            "order_no": item["order_no"],
            "shipped_at": item["shipped_at"],
            "amount": money(item["open"]),
            "status": status,
        })
    kpis = [
        {"label": "未结应收", "value": money(rec["total"]), "note": "共 %d 笔" % len(rec["items"])},
        {"label": "其中逾期", "value": money(rec["overdue"]), "note": "按出货日 + 30 天账期",
         "tone": "warn" if rec["overdue"] else ""},
        {"label": "信用额度剩余", "value": money(credit["remaining"]),
         "note": "授信 %s" % money(credit["limit"])},
    ]
    return {
        "type": "query_result",
        "title": "%s · 应收概览" % customer["name"],
        "subtitle": "在库口径 · 账龄按出货日计算 · 实时",
        "tag": "只读",
        "kpis": kpis,
        "columns": [
            {"key": "order_no", "label": "单号"},
            {"key": "shipped_at", "label": "出货日期"},
            {"key": "amount", "label": "未结金额", "align": "right"},
            {"key": "status", "label": "状态"},
        ],
        "rows": rows,
        "footnote": "口径：未结 = 订单金额 − 已回款；账龄按出货日 + 30 天账期；数据来自 ERP 订单表",
        "actions": [{"label": "查全部订单", "prompt": "%s 的订单和出货进度" % customer["name"]}],
    }


def inventory_card(conn, sku, emp):
    prod = core.product(conn, sku)
    rows = core.inventory_rows(conn, sku, emp)
    available = sum(r["on_hand"] - r["locked"] + r["inbound_available"] for r in rows)
    on_hand = sum(r["on_hand"] for r in rows)
    locked = sum(r["locked"] for r in rows)
    inbound = sum(r["inbound"] for r in rows)
    table = []
    for r in rows:
        table.append({
            "warehouse": r["warehouse_name"],
            "on_hand": "{:,}".format(r["on_hand"]),
            "locked": "{:,}".format(r["locked"]),
            "available": "{:,}".format(r["on_hand"] - r["locked"] + r["inbound_available"]),
        })
    kpis = [
        {"label": "可用库存", "value": "{:,} 件".format(available), "note": "现存量 %s" % format(on_hand, ",")},
        {"label": "锁定量", "value": "{:,} 件".format(locked), "note": "已审核未出货占用"},
        {"label": "在途", "value": "{:,} 件".format(inbound), "note": "预计 3 天后到仓"},
    ]
    return {
        "type": "query_result",
        "title": "库存 · %s %s" % (sku, prod["name"] if prod else ""),
        "subtitle": "实时口径 · 可用 = 现存 − 锁定 + 可分配在途",
        "tag": "只读",
        "kpis": kpis,
        "columns": [
            {"key": "warehouse", "label": "仓库"},
            {"key": "on_hand", "label": "现存量", "align": "right"},
            {"key": "locked", "label": "锁定量", "align": "right"},
            {"key": "available", "label": "可用量", "align": "right"},
        ],
        "rows": table,
        "footnote": "数据来自 ERP 库存表，每次查询实时计算；锁定量为已审核未出货订单占用",
        "actions": [],
    }


def orders_card(conn, customer):
    rows = conn.execute(
        "SELECT * FROM orders WHERE customer_code=? ORDER BY created_at DESC LIMIT 8",
        (customer["code"],),
    ).fetchall()
    table = []
    for r in rows:
        tone = {"APPROVED": "mid", "PARTIAL": "mid", "SHIPPED": "low",
                "INVOICED": "low", "REJECTED": "high"}.get(r["status"], "ghost")
        label = {"APPROVED": "已审核", "PARTIAL": "部分出货", "SHIPPED": "已出货",
                 "INVOICED": "已开票", "REJECTED": "已驳回"}.get(r["status"], r["status"])
        table.append({
            "order_no": r["order_no"],
            "delivery_date": r["delivery_date"],
            "amount": money(r["total_amount"]),
            "status": {"text": label, "tone": tone},
        })
    return {
        "type": "query_result",
        "title": "%s · 最近订单" % customer["name"],
        "subtitle": "含状态与出货进度 · 实时",
        "tag": "只读",
        "kpis": [
            {"label": "订单数", "value": str(len(rows)), "note": "最近 8 笔"},
            {"label": "未出货金额", "value": money(sum(
                r["total_amount"] for r in rows if r["status"] in ("APPROVED", "PARTIAL"))),
             "note": "待发货"},
        ],
        "columns": [
            {"key": "order_no", "label": "单号"},
            {"key": "delivery_date", "label": "交期"},
            {"key": "amount", "label": "金额", "align": "right"},
            {"key": "status", "label": "状态"},
        ],
        "rows": table,
        "footnote": "数据来自 ERP 订单表；状态由出货、开票动作实时推进",
        "actions": [],
    }


def overdue_card(conn, emp, days=60):
    rows = conn.execute(
        "SELECT o.*, c.name FROM orders o JOIN customers c ON c.code=o.customer_code "
        "WHERE o.status IN ('SHIPPED','INVOICED') AND o.paid_amount < o.total_amount"
    ).fetchall()
    table = []
    for r in rows:
        item = core.outstanding_row(conn, r)
        customer = core.customer_by_code(conn, r["customer_code"])
        if not core.in_scope(emp, customer):
            continue
        if item["overdue_days"] < days:
            continue
        table.append({
            "order_no": item["order_no"],
            "customer": r["name"],
            "amount": money(item["open"]),
            "days": {"text": "%d 天" % item["overdue_days"], "tone": "high"},
        })
    return {
        "type": "query_result",
        "title": "逾期超过 %d 天的应收" % days,
        "subtitle": "仅统计当前身份数据范围内客户",
        "tag": "只读",
        "kpis": [{"label": "笔数", "value": str(len(table)), "note": "按逾期天数排序"},
                 {"label": "合计", "value": money(sum(
                     float(t["amount"].replace("¥", "").replace(",", "")) for t in table)), "note": "未结金额"}],
        "columns": [
            {"key": "order_no", "label": "单号"},
            {"key": "customer", "label": "客户"},
            {"key": "amount", "label": "未结金额", "align": "right"},
            {"key": "days", "label": "逾期"},
        ],
        "rows": table,
        "footnote": "口径：出货日 + 30 天账期；仅返回数据范围内数据",
        "actions": [],
    }


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------

def handle_chat(conn, emp, message: str):
    msg = (message or "").strip()
    trace = trace_id()
    core.audit(conn, trace, None, "INTENT", {"message": msg, "employee": emp["username"]})
    conn.commit()

    if not msg:
        return clarify("想查什么或做什么？可以告诉我要看的客户、商品，或要开的单据。", [], trace)

    # 直改库 / SQL 注入意图
    if guard_sql(msg):
        core.audit(conn, trace, None, "BLOCKED", {"code": "E_GUARD_001", "message": msg})
        conn.commit()
        return {
            "type": "blocked", "code": "E_GUARD_001", "title": "请求已被拒绝",
            "subtitle": "规则命中 · 危险意图识别",
            "text": "这条指令包含直接改库意图，被安全策略拦截。",
            "reason": "系统不支持直接修改数据库或执行原生 SQL。所有写操作必须通过业务单据完成。",
            "checks": [
                {"ok": True, "text": "身份校验", "tail": "%s · %s" % (emp["name"], emp["role_name"])},
                {"ok": False, "text": "危险意图", "tail": "命中 BLOCK-SQL-001"},
            ],
            "trace_id": trace,
        }

    customer = core.find_customer(conn, msg)
    sku = core.find_sku(conn, msg)

    # 越权：明确指向范围外客户
    if customer and not core.in_scope(emp, customer):
        return core.blocked_scope(conn, customer, emp, trace)
    if not customer and "华南" in msg:
        south = conn.execute("SELECT * FROM customers WHERE region='SOUTH' LIMIT 1").fetchone()
        return core.blocked_scope(conn, dict(south), emp, trace)

    # 导出请求（即使有权限也需审批，这里直接拒绝）
    if "导出" in msg or "全部下载" in msg:
        core.audit(conn, trace, None, "BLOCKED", {"code": "E_PERM_002", "message": msg})
        conn.commit()
        return {
            "type": "blocked", "code": "E_PERM_002", "title": "请求已被拒绝",
            "subtitle": "规则命中 · 工具授权",
            "text": "这个导出请求被工具授权校验拦下了。",
            "reason": "批量导出需要数据管理员单独授权，当前角色未开通该能力。",
            "checks": [
                {"ok": True, "text": "身份校验", "tail": emp["name"]},
                {"ok": False, "text": "工具授权", "tail": "export.request 未授权"},
            ],
            "trace_id": trace,
        }

    # 开票
    if "开票" in msg or "发票" in msg:
        if not customer:
            return clarify("要给哪个客户开票？", ["苏州华工电子", "华东精密机械", "上海鼎信实业"], trace)
        return invoice_hint(conn, customer, trace)

    # 出货
    if ("出货" in msg or "发货" in msg or "出库" in msg) and "订单" not in msg:
        if not customer:
            return clarify("要给出货的是哪家客户？", ["华东精密机械", "宁波海川汽配"], trace)
        return ship_hint(conn, customer, trace)

    # 制单
    create_intent = bool(re.search(r"开(一张|一个|个)?(货单|单)|下单|制单|做一张单", msg))
    if not create_intent and "开" in msg and sku and "开票" not in msg:
        create_intent = True
    if create_intent:
        return handle_create(conn, emp, msg, customer, sku, trace)

    # 查询
    wants_receivable = bool(re.search(r"应收|欠款|账款|没结|未结|回款", msg))
    wants_inventory = bool(re.search(r"库存|能发|还有多少|余量|存货", msg)) and (sku or "库存" in msg)
    wants_orders = bool(re.search(r"订单|出货进度|发完", msg))

    cards = []
    texts = []
    if wants_receivable:
        if not customer:
            return clarify("要查哪家客户的应收？", ["华东精密机械", "苏州华工电子", "宁波海川汽配"], trace)
        cards.append(receivable_card(conn, customer))
        rec = core.receivables(conn, customer["code"])
        texts.append("%s 未结应收 %s，其中逾期 %s。" % (
            customer["name"], money(rec["total"]), money(rec["overdue"])))
        core.audit(conn, trace, None, "TOOL_CALL", {"tool": "receivable.query",
                                                    "customer": customer["code"],
                                                    "total": rec["total"]})
    if wants_inventory:
        if not sku:
            return clarify("要查哪个商品的库存？", ["SKU-A12 伺服驱动器", "SKU-B07 编码器", "SKU-C33 减速机"], trace)
        cards.append(inventory_card(conn, sku["sku"], emp))
        avail = core.available_stock(conn, sku["sku"], None, emp)
        texts.append("%s %s 可用 %s 件。" % (sku["sku"], sku["name"], format(avail, ",")))
        core.audit(conn, trace, None, "TOOL_CALL", {"tool": "inventory.query",
                                                    "sku": sku["sku"], "available": avail})
    if wants_orders and customer:
        cards.append(orders_card(conn, customer))
        texts.append("已列出 %s 最近订单与出货状态。" % customer["name"])
        core.audit(conn, trace, None, "TOOL_CALL", {"tool": "order.query",
                                                    "customer": customer["code"]})

    # 逾期组合查询
    if "逾期" in msg and not customer:
        days_match = re.search(r"(\d{2,3})\s*天", msg)
        days = int(days_match.group(1)) if days_match else 60
        cards.append(overdue_card(conn, emp, days))
        texts.append("按你的数据范围统计，逾期超过 %d 天的应收如下。" % days)

    if cards:
        conn.commit()
        return {"type": "answer", "text": "".join(texts), "cards": cards,
                "trace_id": trace, "intent": "QUERY"}

    # 兜底
    conn.commit()
    return clarify(
        "我还需要一点信息才能执行。可以告诉我：客户 + 商品 + 数量，例如“给华东精密开 A12 200 件”。",
        ["给华东精密开 A12 200 件", "华东精密还有多少应收", "查 A12 可用库存"], trace)


def invoice_hint(conn, customer, trace):
    rows = conn.execute(
        "SELECT * FROM orders WHERE customer_code=? AND status='SHIPPED' ORDER BY shipped_at DESC LIMIT 3",
        (customer["code"],),
    ).fetchall()
    if not rows:
        return {
            "type": "blocked", "code": "E_FLOW_002", "title": "暂不能开票",
            "subtitle": "状态机校验",
            "reason": "%s 没有已出货的货单。未出货不能开票（前置节点未完成）。" % customer["name"],
            "checks": [{"ok": True, "text": "客户存在", "tail": customer["code"]},
                       {"ok": False, "text": "出货节点", "tail": "无已出货货单"}],
            "trace_id": trace,
        }
    table = [{"order_no": r["order_no"], "shipped_at": (r["shipped_at"] or "")[:10],
              "amount": money(r["total_amount"]),
              "status": {"text": "已出货", "tone": "low"}} for r in rows]
    return {
        "type": "answer",
        "text": "%s 有 %d 张已出货货单可以开票。开票属于高风险动作，需要财务总监审批；可在审批中心完成。" % (
            customer["name"], len(rows)),
        "cards": [{
            "type": "query_result", "title": "可开票货单 · %s" % customer["name"],
            "subtitle": "已出货未开票", "tag": "只读",
            "kpis": [{"label": "可开票", "value": str(len(rows)), "note": "笔"},
                     {"label": "合计", "value": money(sum(r["total_amount"] for r in rows)), "note": "含税"}],
            "columns": [{"key": "order_no", "label": "单号"},
                        {"key": "shipped_at", "label": "出货日期"},
                        {"key": "amount", "label": "金额", "align": "right"},
                        {"key": "status", "label": "状态"}],
            "rows": table,
            "footnote": "开票需走审批：财务总监确认后由 ERP 开票服务执行",
            "actions": [{"label": "去审批中心", "route": "#/approvals"}],
        }],
        "trace_id": trace,
    }


def ship_hint(conn, customer, trace):
    rows = conn.execute(
        "SELECT * FROM orders WHERE customer_code=? AND status IN ('APPROVED','PARTIAL') "
        "ORDER BY created_at DESC LIMIT 3", (customer["code"],),
    ).fetchall()
    if not rows:
        return {
            "type": "blocked", "code": "E_FLOW_001", "title": "暂不能出货",
            "subtitle": "状态机校验",
            "reason": "%s 没有已审核未出货的货单。未审核货单不允许出货。" % customer["name"],
            "checks": [{"ok": True, "text": "客户存在", "tail": customer["code"]},
                       {"ok": False, "text": "单据状态", "tail": "无已审核货单"}],
            "trace_id": trace,
        }
    table = [{"order_no": r["order_no"], "delivery_date": r["delivery_date"],
              "amount": money(r["total_amount"]),
              "status": {"text": "已审核", "tone": "mid"}} for r in rows]
    return {
        "type": "answer",
        "text": "%s 有 %d 张已审核货单待出货，可由仓储发货员在审批中心确认后执行。" % (
            customer["name"], len(rows)),
        "cards": [{
            "type": "query_result", "title": "待出货货单 · %s" % customer["name"],
            "subtitle": "已审核未出货", "tag": "只读",
            "kpis": [{"label": "待出货", "value": str(len(rows)), "note": "笔"},
                     {"label": "金额", "value": money(sum(r["total_amount"] for r in rows)), "note": "含税"}],
            "columns": [{"key": "order_no", "label": "单号"},
                        {"key": "delivery_date", "label": "交期"},
                        {"key": "amount", "label": "金额", "align": "right"},
                        {"key": "status", "label": "状态"}],
            "rows": table,
            "footnote": "出货会同步扣减现存量并释放锁定量，动作写入审计链",
            "actions": [{"label": "去审批中心", "route": "#/approvals"}],
        }],
        "trace_id": trace,
    }


def handle_create(conn, emp, msg, customer, sku, trace):
    if not customer:
        return clarify("要开给哪家客户？", ["华东精密机械", "苏州华工电子", "宁波海川汽配"], trace)
    if not sku:
        return clarify("要开哪个商品？", ["SKU-A12 伺服驱动器", "SKU-B07 编码器", "SKU-C33 减速机"], trace)
    qty = parse_qty(msg)
    if not qty:
        return clarify("数量是多少？例如“A12 200 件”。", [], trace)

    price = parse_price(msg, sku["list_price"])
    warehouse = parse_warehouse(msg, customer)
    delivery = core.parse_date(msg, 7)

    result = core.create_draft(conn, emp, customer, sku["sku"], qty, price, delivery,
                               warehouse, msg, trace)
    if result.get("type") == "draft":
        d = result["draft"]
        prod = core.product(conn, sku["sku"])
        result["fields"] = [
            {"label": "客户", "value": customer["name"], "note": customer["code"]},
            {"label": "收货地址", "value": customer["address"], "note": ""},
            {"label": "商品", "value": "%s %s" % (sku["sku"], prod["name"] if prod else sku["name"]), "note": ""},
            {"label": "数量", "value": "%d 件" % qty, "note": ""},
            {"label": "单价", "value": money(price), "note": "含税 13%"},
            {"label": "金额", "value": money(d["total"]), "note": "", "strong": True},
            {"label": "交货日期", "value": delivery, "note": ""},
            {"label": "发货仓", "value": {"WH-SH": "上海仓", "WH-HZ": "杭州仓", "WH-NJ": "南京仓",
                                          "WH-GZ": "广州仓"}.get(warehouse, warehouse),
             "note": "可用 %s" % format(core.available_stock(conn, sku["sku"], warehouse, emp), ",")},
        ]
        result["message"] = result.pop("text", "")
    return result
