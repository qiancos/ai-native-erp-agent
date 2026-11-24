# -*- coding: utf-8 -*-
"""从当前数据库导出一份前端离线演示数据（prototype/demo-data.js）。"""
import json
import os

import core
import db

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.normpath(os.path.join(BASE, "..", "prototype", "demo-data.js"))


def scoped_customers(conn, emp):
    clause, params = core._region_clause(emp)
    rows = conn.execute("SELECT * FROM customers c WHERE 1=1" + clause + " ORDER BY code", params).fetchall()
    return [dict(r) for r in rows]


def main():
    conn = db.connect()
    emp = core.employee_payload(conn.execute(
        "SELECT * FROM employees WHERE username='zhangwei'").fetchone())

    orders = core.list_orders(conn, emp)
    details = {o["order_no"]: core.order_detail(conn, emp, o["order_no"]) for o in orders}

    traces = core.list_traces(conn, 20)
    trace_details = {t["trace_id"]: core.trace_detail(conn, t["trace_id"]) for t in traces}

    approvals = core.list_approvals(conn, emp)
    approval_details = {
        a["approval_id"]: core.approval_detail(conn, emp, a["approval_id"]) for a in approvals
    }

    data = {
        "employee": emp,
        "accounts": core.demo_accounts(conn),
        "bootstrap": core.bootstrap(conn, emp),
        "overview": core.erp_overview(conn, emp),
        "orders": orders,
        "orderDetails": details,
        "inventory": core.list_inventory(conn, emp),
        "inbounds": core.list_inbounds(conn, emp),
        "shipments": core.list_shipments(conn, emp),
        "invoices": core.list_invoices(conn, emp),
        "customers": core.list_customers(conn, emp),
        "products": core.list_products(conn, emp),
        "approvals": approvals,
        "approvalDetails": approval_details,
        "traces": traces,
        "traceDetails": trace_details,
        "tools": core.list_tools(conn, emp),
        "rules": core.list_rules(conn),
        "knowledge": core.list_knowledge(conn),
        "integration": core.get_integration(conn),
    }

    js = "/* 自动生成：离线演示数据（来自 erp.db 种子） */\nwindow.DEMO = " + \
        json.dumps(data, ensure_ascii=False) + ";\n"
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(js)
    print("written:", OUT, len(js), "bytes")


if __name__ == "__main__":
    main()
