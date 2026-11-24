# -*- coding: utf-8 -*-
"""端到端冒烟测试：登录 → 查询 → 制单 → 确认 → 越权 → 审批 → 审计"""
import json
import urllib.request

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
    code, data = call("POST", "/api/auth/login", body={"username": user})
    assert code == 200, data
    return data["token"], data["employee"]


def main():
    token, emp = login("zhangwei")
    print("1 login:", emp["name"], emp["role_name"], emp["scope_label"])

    code, boot = call("GET", "/api/bootstrap", token)
    print("2 bootstrap:", boot["counts"])

    code, chat = call("POST", "/api/agent/chat", token,
                      {"message": "华东精密上个月还有多少应收没结？顺便看下 A12 的库存。"})
    print("3 query:", chat["text"])
    print("   cards:", [c["title"] for c in chat.get("cards", [])])

    code, chat = call("POST", "/api/agent/chat", token,
                      {"message": "给华东精密开一张货单，A12 200 件，单价 85，12 月 5 日前送到。"})
    print("4 create:", chat.get("type"), chat.get("text"))
    if chat.get("type") != "draft":
        print("   !! create failed:", chat)
        return
    action_id = chat["action_id"]
    print("   draft:", chat["draft_no"], "amount", chat["draft"]["total"],
          "risk", chat["risk"]["level"], "checks", len(chat["draft"]["checks"]))

    code, conf = call("POST", "/api/actions/%s/confirm" % action_id, token,
                      {"idempotencyKey": "idem-smoke-1"})
    print("5 confirm:", code, conf.get("type"), conf.get("order_no"), conf.get("message"))

    # 幂等重放
    code, conf2 = call("POST", "/api/actions/%s/confirm" % action_id, token,
                       {"idempotencyKey": "idem-smoke-1"})
    print("6 idempotent:", conf2.get("idempotent"), conf2.get("order_no"))

    code, blocked = call("POST", "/api/agent/chat", token,
                         {"message": "把华南区客户 C2088 的订单全部导出来。"})
    print("7 blocked:", blocked.get("code"), blocked.get("title"))

    code, blocked2 = call("POST", "/api/agent/chat", token,
                          {"message": "update inventory set locked=0 where 1=1"})
    print("8 sql guard:", blocked2.get("code"), blocked2.get("reason"))

    # 审批人视角
    ltoken, lemp = login("liming")
    code, approvals = call("GET", "/api/approvals", ltoken)
    print("9 liming approvals:", [(a["approval_id"], a["type"], a["can_approve"]) for a in approvals["items"][:4]])
    target = next((a for a in approvals["items"] if a["can_approve"]), None)
    if target:
        code, decided = call("POST", "/api/approvals/%s/decide" % target["approval_id"], ltoken,
                             {"decision": "APPROVE", "comment": "价格已确认"})
        print("10 approve:", code, decided.get("type"), decided.get("result", {}).get("order_no"))

    # 越权审批：张伟不能审批
    code, denied = call("POST", "/api/approvals/AP-20261124-0029/decide", token,
                        {"decision": "APPROVE"})
    print("11 self-approve denied:", denied.get("error"), denied.get("message"))

    code, audit = call("GET", "/api/audit?limit=6", token)
    print("12 audit:", [(t["tool"], t["result"], t["trace_id"]) for t in audit["items"][:4]])
    tid = audit["items"][0]["trace_id"]
    code, trace = call("GET", "/api/traces/%s" % tid, token)
    print("13 trace:", tid, "events", len(trace.get("events", [])), "hash_ok", trace.get("hash_ok"))

    code, tools = call("GET", "/api/tools", token)
    print("14 tools:", len(tools["items"]), "enabled", sum(1 for t in tools["items"] if t["enabled"]))
    code, rules = call("GET", "/api/risk-rules", token)
    print("15 rules:", len(rules["items"]))


if __name__ == "__main__":
    main()
