# -*- coding: utf-8 -*-
"""传统 ERP 接口冒烟：表单开单 → 风险审批 → 出货 → 开票"""
import json
import urllib.request
import urllib.error

BASE = "http://127.0.0.1:8765"


def call(method, path, token=None, body=None):
    data = json.dumps(body or {}, ensure_ascii=False).encode("utf-8") if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Content-Type", "application/json; charset=utf-8")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


def login(user):
    return call("POST", "/api/auth/login", body={"username": user})[1]["token"]


def main():
    t = login("zhangwei")
    print("orders:", len(call("GET", "/api/erp/orders", t)[1]["items"]))
    print("inventory rows:", len(call("GET", "/api/erp/inventory", t)[1]["items"]))
    print("shipments:", len(call("GET", "/api/erp/shipments", t)[1]["items"]))
    print("invoices:", len(call("GET", "/api/erp/invoices", t)[1]["items"]))
    print("overview:", call("GET", "/api/erp/overview", t)[1])

    # 1) 小额表单开单 -> 直接已审核
    code, low = call("POST", "/api/erp/orders", t, {
        "customerCode": "C10023", "warehouse": "WH-SH",
        "deliveryDate": "2026-12-10",
        "items": [{"sku": "SKU-A12", "qty": 10, "unitPrice": 85}],
    })
    print("1 low order:", code, low.get("order_no"), low.get("status"), low.get("total"))

    # 2) 大额表单开单 -> 待审核
    code, high = call("POST", "/api/erp/orders", t, {
        "customerCode": "C10023", "warehouse": "WH-SH",
        "deliveryDate": "2026-12-12",
        "items": [{"sku": "SKU-A12", "qty": 600, "unitPrice": 85}],
    })
    print("2 high order:", code, high.get("order_no"), high.get("status"),
          high.get("risk"), high.get("approval_id"))

    # 3) 越权开单 -> 拒绝
    code, denied = call("POST", "/api/erp/orders", t, {
        "customerCode": "C2088", "warehouse": "WH-SH",
        "items": [{"sku": "SKU-A12", "qty": 1, "unitPrice": 85}],
    })
    print("3 scope denied:", code, denied.get("error"), denied.get("message"))

    # 4) 主管审批
    lt = login("liming")
    code, pending = call("GET", "/api/approvals", lt)
    target = next((a for a in pending["items"] if a["type"] == "订单审核"), None)
    print("4 pending approval:", target and target["approval_id"])
    if target:
        code, res = call("POST", "/api/approvals/%s/decide" % target["approval_id"], lt,
                         {"decision": "APPROVE", "comment": "价格已确认"})
        print("  approve:", code, res.get("result"))

    # 5) 出货（小额单）
    code, ship = call("POST", "/api/erp/orders/%s/ship" % low["order_no"], t, {})
    print("5 ship:", code, ship.get("shipment_no"), ship.get("qty"), ship.get("status"))

    # 6) 出货后再查库存
    inv = call("GET", "/api/erp/inventory", t)[1]["items"]
    a12 = [i for i in inv if i["sku"] == "SKU-A12" and i["warehouse"] == "WH-SH"][0]
    print("6 inventory A12 SH:", "on_hand", a12["on_hand"], "locked", a12["locked"], "available", a12["available"])

    # 7) 财务开票
    cj = login("chenjing")
    invs = call("GET", "/api/erp/invoices", cj)[1]["items"]
    print("7 invoices before:", len(invs))
    code, invoice = call("POST", "/api/erp/invoices", cj, {
        "orderNo": low["order_no"], "amount": low["total"], "taxType": "VAT_13"})
    print("  issue:", code, invoice.get("invoice_no"), invoice.get("status"))

    # 8) 张伟开票应被拒
    code, denied_inv = call("POST", "/api/erp/invoices", t, {
        "orderNo": low["order_no"], "amount": low["total"]})
    print("8 role denied invoice:", code, denied_inv.get("error"), denied_inv.get("message"))


if __name__ == "__main__":
    main()
