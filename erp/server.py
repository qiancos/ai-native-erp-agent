# -*- coding: utf-8 -*-
"""
ERP-Agent 演示服务
- 提供迷你 ERP 的 JSON API（身份、查询、制单、确认、审批、审计、配置）
- 同时托管 prototype/ 静态前端，同源部署，无需 CORS
启动：python server.py  （默认 http://127.0.0.1:8765）
"""
import json
import os
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import agent
import core
import db

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.normpath(os.path.join(BASE_DIR, "..", "prototype"))
PORT = int(os.environ.get("ERP_PORT", "8765"))

CONN = db.init_db()
LOCK = threading.Lock()

MIME = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".json": "application/json; charset=utf-8",
    ".ico": "image/x-icon",
}


class Handler(BaseHTTPRequestHandler):
    server_version = "ERP-Agent/1.0"

    # ------------------------------------------------------------------ 基础
    def log_message(self, fmt, *args):
        print("[%s] %s" % (self.log_date_time_string(), fmt % args))

    def _json(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-Token")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, OPTIONS")

    def _read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            return {}

    def _token(self):
        auth = self.headers.get("Authorization", "")
        if auth.lower().startswith("bearer "):
            return auth[7:].strip()
        return self.headers.get("X-Token") or ""

    def _emp(self):
        return core.employee_by_token(CONN, self._token())

    def _path(self):
        return urllib.parse.urlparse(self.path).path

    def _query(self):
        return urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)

    # ------------------------------------------------------------------ HTTP
    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        path = self._path()
        if path.startswith("/api/"):
            with LOCK:
                try:
                    self._api_get(path)
                except Exception as exc:  # 演示服务：返回可读错误而不是断开连接
                    self._json({"error": "E_SYS_001", "message": "服务异常：%s" % exc}, 500)
            return
        self._static(path)

    def do_POST(self):
        path = self._path()
        if not path.startswith("/api/"):
            self._json({"error": "E_NOT_FOUND", "message": "not found"}, 404)
            return
        # 外部 ERP 连通性测试会发起出站请求，不能持有全局 DB 锁（否则请求自身会死锁）
        if path == "/api/integration/test":
            try:
                self._api_post(path)
            except Exception as exc:
                self._json({"error": "E_SYS_001", "message": "服务异常：%s" % exc}, 500)
            return
        with LOCK:
            try:
                self._api_post(path)
            except Exception as exc:
                self._json({"error": "E_SYS_001", "message": "服务异常：%s" % exc}, 500)

    def do_PUT(self):
        path = self._path()
        if not path.startswith("/api/"):
            self._json({"error": "E_NOT_FOUND", "message": "not found"}, 404)
            return
        with LOCK:
            try:
                self._api_put(path)
            except Exception as exc:
                self._json({"error": "E_SYS_001", "message": "服务异常：%s" % exc}, 500)

    def do_DELETE(self):
        path = self._path()
        if not path.startswith("/api/"):
            self._json({"error": "E_NOT_FOUND", "message": "not found"}, 404)
            return
        with LOCK:
            try:
                self._api_delete(path)
            except Exception as exc:
                self._json({"error": "E_SYS_001", "message": "服务异常：%s" % exc}, 500)

    # ------------------------------------------------------------------ API
    def _api_get(self, path):
        if path == "/api/health":
            return self._json({"ok": True, "service": "erp-agent", "time": core.now_iso()})

        if path == "/api/auth/accounts":
            return self._json({"accounts": core.demo_accounts(CONN)})

        emp = self._emp()
        if not emp:
            return self._json({"error": "E_AUTH_001", "message": "登录已过期，请重新登录"}, 401)

        if path == "/api/bootstrap":
            return self._json(core.bootstrap(CONN, emp))
        if path == "/api/me":
            return self._json({"employee": emp})
        if path == "/api/approvals":
            return self._json({"items": core.list_approvals(CONN, emp)})
        if path.startswith("/api/approvals/"):
            aid = path.split("/")[-1]
            data = core.approval_detail(CONN, emp, aid)
            if not data:
                return self._json({"error": "E_NOT_FOUND", "message": "审批单不存在或无权查看"}, 404)
            return self._json(data)
        if path == "/api/audit":
            q = self._query()
            return self._json({"items": core.list_traces(CONN, int(q.get("limit", ["20"])[0]))})
        if path.startswith("/api/traces/"):
            tid = path.split("/")[-1]
            data = core.trace_detail(CONN, tid)
            if not data["events"]:
                return self._json({"error": "E_NOT_FOUND", "message": "链路不存在"}, 404)
            return self._json(data)
        if path == "/api/tools":
            return self._json({"items": core.list_tools(CONN, emp)})
        if path == "/api/risk-rules":
            return self._json({"items": core.list_rules(CONN)})
        if path == "/api/knowledge":
            return self._json({"items": core.list_knowledge(CONN)})
        if path == "/api/integration":
            return self._json(core.get_integration(CONN))

        # ---------------- 传统 ERP 页面数据 ----------------
        if path == "/api/erp/overview":
            return self._json(core.erp_overview(CONN, emp))
        if path == "/api/erp/orders":
            q = self._query()
            return self._json({"items": core.list_orders(CONN, emp,
                                                         q.get("status", [None])[0],
                                                         q.get("q", [None])[0])})
        if path.startswith("/api/erp/orders/"):
            data = core.order_detail(CONN, emp, path.split("/")[-1])
            if not data:
                return self._json({"error": "E_NOT_FOUND", "message": "订单不存在或无权查看"}, 404)
            return self._json(data)
        if path == "/api/erp/inventory":
            q = self._query().get("q", [None])[0]
            return self._json({"items": core.list_inventory(CONN, emp, q)})
        if path == "/api/erp/shipments":
            return self._json({"items": core.list_shipments(CONN, emp)})
        if path == "/api/erp/inbounds":
            return self._json({"items": core.list_inbounds(CONN, emp)})
        if path == "/api/erp/invoices":
            return self._json({"items": core.list_invoices(CONN, emp)})
        if path == "/api/erp/customers":
            return self._json({"items": core.list_customers(CONN, emp)})
        if path == "/api/erp/products":
            return self._json({"items": core.list_products(CONN, emp)})

        if path == "/api/customers":
            q = (self._query().get("q", [""])[0] or "").strip()
            rows = CONN.execute("SELECT * FROM customers WHERE name LIKE ? OR code LIKE ? LIMIT 20",
                                ("%"+q+"%", "%"+q+"%")).fetchall()
            return self._json({"items": [dict(r) for r in rows]})
        if path == "/api/inventory":
            sku = self._query().get("sku", ["SKU-A12"])[0]
            return self._json({"items": core.inventory_rows(CONN, sku)})
        if path.startswith("/api/orders/"):
            no = path.split("/")[-1]
            row = CONN.execute("SELECT * FROM orders WHERE order_no=?", (no,)).fetchone()
            if not row:
                return self._json({"error": "E_NOT_FOUND", "message": "货单不存在"}, 404)
            items = CONN.execute("SELECT * FROM order_items WHERE order_no=?", (no,)).fetchall()
            data = dict(row)
            data["items"] = [dict(i) for i in items]
            return self._json(data)

        return self._json({"error": "E_NOT_FOUND", "message": "接口不存在"}, 404)

    def _api_post(self, path):
        if path == "/api/auth/login":
            body = self._read_json()
            result = core.login(CONN, body.get("username", "zhangwei"), body.get("password", "demo123"))
            if not result:
                return self._json({"error": "E_AUTH_001", "message": "账号或密码错误"}, 401)
            return self._json(result)

        if path == "/api/agent/chat":
            emp = self._emp()
            if not emp:
                return self._json({"error": "E_AUTH_001", "message": "登录已过期"}, 401)
            body = self._read_json()
            return self._json(agent.handle_chat(CONN, emp, body.get("message", "")))

        if path.startswith("/api/actions/") and path.endswith("/confirm"):
            emp = self._emp()
            if not emp:
                return self._json({"error": "E_AUTH_001", "message": "登录已过期"}, 401)
            action_id = path.split("/")[-2]
            body = self._read_json()
            result = core.confirm_action(CONN, emp, action_id, body.get("idempotencyKey"))
            status = 200 if not result.get("error") else 409
            return self._json(result, status)

        # ---------------- 传统 ERP 写操作 ----------------
        if path == "/api/erp/orders":
            emp = self._emp()
            if not emp:
                return self._json({"error": "E_AUTH_001", "message": "登录已过期"}, 401)
            result = core.create_manual_order(CONN, emp, self._read_json())
            return self._json(result, 200 if not result.get("error") else 400)
        if path == "/api/erp/customers":
            emp = self._emp()
            if not emp:
                return self._json({"error": "E_AUTH_001", "message": "登录已过期"}, 401)
            result = core.save_customer(CONN, emp, None, self._read_json())
            return self._json(result, 200 if not result.get("error") else 400)
        if path == "/api/erp/products":
            emp = self._emp()
            if not emp:
                return self._json({"error": "E_AUTH_001", "message": "登录已过期"}, 401)
            result = core.save_product(CONN, emp, None, self._read_json())
            return self._json(result, 200 if not result.get("error") else 400)
        if path.startswith("/api/erp/orders/") and path.endswith("/approve"):
            emp = self._emp()
            if not emp:
                return self._json({"error": "E_AUTH_001", "message": "登录已过期"}, 401)
            body = self._read_json()
            result = core.approve_order(CONN, emp, path.split("/")[-2],
                                        body.get("decision", "APPROVE"), body.get("comment", ""))
            return self._json(result, 200 if not result.get("error") else 409)
        if path.startswith("/api/erp/orders/") and path.endswith("/cancel"):
            emp = self._emp()
            if not emp:
                return self._json({"error": "E_AUTH_001", "message": "登录已过期"}, 401)
            result = core.cancel_order(CONN, emp, path.split("/")[-2])
            return self._json(result, 200 if not result.get("error") else 409)
        if path.startswith("/api/erp/orders/") and path.endswith("/ship"):
            emp = self._emp()
            if not emp:
                return self._json({"error": "E_AUTH_001", "message": "登录已过期"}, 401)
            result = core.ship_full_order(CONN, emp, path.split("/")[-2])
            return self._json(result, 200 if not result.get("error") else 409)
        if path == "/api/erp/invoices":
            emp = self._emp()
            if not emp:
                return self._json({"error": "E_AUTH_001", "message": "登录已过期"}, 401)
            body = self._read_json()
            result = core.issue_invoice(CONN, emp, body.get("orderNo", ""),
                                        float(body.get("amount") or 0),
                                        body.get("taxType", "VAT_13"))
            return self._json(result, 200 if not result.get("error") else 409)
        if path == "/api/erp/inbounds":
            emp = self._emp()
            if not emp:
                return self._json({"error": "E_AUTH_001", "message": "登录已过期"}, 401)
            result = core.create_inbound(CONN, emp, self._read_json())
            return self._json(result, 200 if not result.get("error") else 400)

        if path == "/api/integration/test":
            emp = self._emp()
            if not emp:
                return self._json({"error": "E_AUTH_001", "message": "登录已过期"}, 401)
            return self._json(core.test_integration())

        if path.startswith("/api/approvals/") and path.endswith("/decide"):
            emp = self._emp()
            if not emp:
                return self._json({"error": "E_AUTH_001", "message": "登录已过期"}, 401)
            approval_id = path.split("/")[-2]
            body = self._read_json()
            result = core.decide_approval(CONN, emp, approval_id,
                                          body.get("decision", "APPROVE"), body.get("comment", ""))
            return self._json(result, 200 if not result.get("error") else 409)

        if path.startswith("/api/orders/") and path.endswith("/ship"):
            emp = self._emp()
            if not emp:
                return self._json({"error": "E_AUTH_001", "message": "登录已过期"}, 401)
            body = self._read_json()
            order_no = path.split("/")[-2]
            return self._json(core.ship_order(CONN, emp, order_no, int(body.get("qty", 0)),
                                              body.get("warehouse", "WH-SH")))

        if path.startswith("/api/orders/") and path.endswith("/invoice"):
            emp = self._emp()
            if not emp:
                return self._json({"error": "E_AUTH_001", "message": "登录已过期"}, 401)
            body = self._read_json()
            order_no = path.split("/")[-2]
            return self._json(core.issue_invoice(CONN, emp, order_no, float(body.get("amount", 0)),
                                                 body.get("taxType", "VAT_13")))

        return self._json({"error": "E_NOT_FOUND", "message": "接口不存在"}, 404)

    def _api_put(self, path):
        emp = self._emp()
        if not emp:
            return self._json({"error": "E_AUTH_001", "message": "登录已过期"}, 401)
        body = self._read_json()

        if path == "/api/integration":
            return self._json(core.save_integration(CONN, body))
        if path.startswith("/api/erp/orders/"):
            result = core.update_pending_order(CONN, emp, path.split("/")[-1], body)
            return self._json(result, 200 if not result.get("error") else 400)
        if path.startswith("/api/erp/customers/"):
            result = core.save_customer(CONN, emp, path.split("/")[-1], body)
            return self._json(result, 200 if not result.get("error") else 400)
        if path.startswith("/api/erp/products/"):
            result = core.save_product(CONN, emp, path.split("/")[-1], body)
            return self._json(result, 200 if not result.get("error") else 400)
        if path.startswith("/api/tools/"):
            name = path.split("/")[-1]
            core.set_tool(CONN, name, bool(body.get("enabled", True)))
            return self._json({"ok": True})
        if path.startswith("/api/risk-rules/"):
            code = path.split("/")[-1]
            core.set_rule(CONN, code, body.get("enabled"), body.get("threshold"))
            return self._json({"ok": True})
        return self._json({"error": "E_NOT_FOUND", "message": "接口不存在"}, 404)

    def _api_delete(self, path):
        emp = self._emp()
        if not emp:
            return self._json({"error": "E_AUTH_001", "message": "登录已过期"}, 401)
        if path.startswith("/api/erp/orders/"):
            result = core.delete_order(CONN, emp, path.split("/")[-1])
            return self._json(result, 200 if not result.get("error") else 409)
        if path.startswith("/api/erp/customers/"):
            result = core.delete_customer(CONN, emp, path.split("/")[-1])
            return self._json(result, 200 if not result.get("error") else 409)
        if path.startswith("/api/erp/products/"):
            result = core.delete_product(CONN, emp, path.split("/")[-1])
            return self._json(result, 200 if not result.get("error") else 409)
        return self._json({"error": "E_NOT_FOUND", "message": "接口不存在"}, 404)

    # ------------------------------------------------------------------ 静态
    def _static(self, path):
        if path in ("/", ""):
            path = "/index.html"
        rel = urllib.parse.unquote(path.lstrip("/"))
        full = os.path.normpath(os.path.join(WEB_DIR, rel))
        if not full.startswith(WEB_DIR) or not os.path.isfile(full):
            self.send_response(404)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write("Not Found".encode("utf-8"))
            return
        ext = os.path.splitext(full)[1].lower()
        with open(full, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", MIME.get(ext, "application/octet-stream"))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    print("ERP-Agent demo server")
    print("  DB :", db.DB_PATH)
    print("  Web:", WEB_DIR)
    print("  URL: http://127.0.0.1:%d/" % PORT)
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
