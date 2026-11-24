# -*- coding: utf-8 -*-
"""传统 ERP CRUD 冒烟：新增 / 修改 / 审核 / 删除 / 取消 + 客户商品主数据"""
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
    lm = login("liming")

    # 1) 新建大额订单（待审核）
    _, order = call("POST", "/api/erp/orders", t, {
        "customerCode": "C10023", "warehouse": "WH-SH", "deliveryDate": "2026-12-12",
        "items": [{"sku": "SKU-A12", "qty": 700, "unitPrice": 85}],
    })
    no = order.get("order_no")
    print("1 create pending:", no, order.get("status"))

    # 2) 修改订单
    code, updated = call("PUT", "/api/erp/orders/" + no, t, {
        "warehouse": "WH-SH", "deliveryDate": "2026-12-15",
        "items": [{"sku": "SKU-A12", "qty": 650, "unitPrice": 85}],
    })
    print("2 update:", code, updated.get("total"))

    # 3) 张伟不能审核
    code, denied = call("POST", "/api/erp/orders/%s/approve" % no, t, {"decision": "APPROVE"})
    print("3 employee approve denied:", denied.get("error"))

    # 4) 李明审核通过
    code, approved = call("POST", "/api/erp/orders/%s/approve" % no, lm, {"decision": "APPROVE", "comment": "ok"})
    print("4 manager approve:", code, approved.get("status"))

    # 5) 取消订单（释放库存）
    code, cancelled = call("POST", "/api/erp/orders/%s/cancel" % no, lm, {})
    print("5 cancel:", code, cancelled.get("status"))

    # 6) 再建一张待审核单并删除
    _, order2 = call("POST", "/api/erp/orders", t, {
        "customerCode": "C10023", "warehouse": "WH-SH",
        "items": [{"sku": "SKU-A12", "qty": 600, "unitPrice": 85}],
    })
    no2 = order2.get("order_no")
    code, deleted = call("DELETE", "/api/erp/orders/" + no2, t)
    print("6 delete pending:", no2, code, deleted.get("deleted"))

    # 7) 客户 CRUD
    code, cust = call("POST", "/api/erp/customers", t, {
        "name": "测试客户股份", "region": "EAST", "contact": "测试",
        "phone": "13800000000", "address": "测试地址", "credit_limit": 50000,
    })
    ccode = cust.get("code")
    print("7 customer create:", code, ccode)
    code, cu = call("PUT", "/api/erp/customers/" + ccode, t, {
        "name": "测试客户股份（改）", "region": "EAST", "credit_limit": 80000,
    })
    print("  update:", code, cu.get("name"))
    code, cd = call("DELETE", "/api/erp/customers/" + ccode, lm)
    print("  delete:", code, cd.get("deleted"))

    # 8) 商品 CRUD（主管）
    code, prod = call("POST", "/api/erp/products", lm, {
        "name": "测试传感器", "category": "传感类", "unit": "件", "list_price": 66,
    })
    sku = prod.get("sku")
    print("8 product create:", code, sku)
    code, pu = call("PUT", "/api/erp/products/" + sku, lm, {
        "name": "测试传感器（改）", "category": "传感类", "unit": "件", "list_price": 70,
    })
    print("  update:", code, pu.get("name"))
    code, pd = call("DELETE", "/api/erp/products/" + sku, lm)
    print("  delete:", code, pd.get("deleted"))

    # 9) 已被订单引用的商品不能删
    code, blocked = call("DELETE", "/api/erp/products/SKU-A12", lm)
    print("9 product in use:", code, blocked.get("error"), blocked.get("message"))

    # 10) 库存恢复检查
    code, inv = call("GET", "/api/erp/inventory", t)
    a12 = [i for i in inv["items"] if i["sku"] == "SKU-A12" and i["warehouse"] == "WH-SH"][0]
    print("10 inventory A12:", a12["on_hand"], a12["locked"], a12["available"])


if __name__ == "__main__":
    main()
