# 迷你 ERP 内核（可选服务模式）

前端默认离线运行（浏览器 localStorage + `prototype/demo-data.js`），不需要本服务也能完整演示。这个目录是**可选的持久化后端**：SQLite、真实 HTTP 接口、审计哈希链；接口契约与离线引擎保持一致。

## 启动（可选）

```bash
cd erp
python server.py
# 浏览器打开 http://127.0.0.1:8765/
```

首次启动自动建库并写入种子数据；`erp/erp.db` 就是数据库文件，删掉后重启即可重置。

## 演示账号（密码统一 demo123）

| 账号 | 姓名 | 角色 | 数据范围 | 能看到什么 |
| --- | --- | --- | --- | --- |
| `zhangwei` | 张伟 | 销售跟单 | 华东区 | 查询、开单、自己发起的审批 |
| `liming` | 李明 | 销售主管 | 华东区 | 大额货单审批（不能批自己的单） |
| `chenjing` | 陈静 | 财务开票员 | 华东 + 华南 | 发起开票、查应收 |
| `wangmin` | 王敏 | 风控管理员 | 全部 | 全部规则与审计 |

页面右上角头像可切换身份，切换后权限与可见数据立即不同。

## 数据模型

| 表 | 内容 | 说明 |
| --- | --- | --- |
| `employees` | 员工与角色、数据范围 | 会话绑定的主体 |
| `customers` | 客户主数据 | 含区域、授信额度 |
| `products` | 商品 | SKU、标准价 |
| `warehouses` / `inventory` | 仓库与库存 | 现存、锁定、在途 |
| `orders` / `order_items` | 货单与明细 | 状态机：APPROVED → PARTIAL → SHIPPED → INVOICED |
| `shipments` / `invoices` | 出货、发票 | 出货扣现存、释放锁定 |
| `actions` | 一次工具调用的动作 | 风险、参数、幂等键、状态 |
| `approvals` | 审批单 | 按单据类型路由审批人 |
| `audit_events` | 审计事件 | 追加写 + 哈希链（sha256 前序哈希） |
| `risk_rules` / `tools` / `knowledge` | 风控规则、工具契约、知识条目 | 页面可开关、改阈值，立即生效 |

## 已经实现的业务规则（不是装饰）

1. **数据范围**：张伟查华南客户 C2088 → `E_PERM_001`，且查询条件在 SQL 层注入，不是查完再过滤；
2. **仓库范围**：库存只统计当前身份可访问的仓库（华东看不到广州仓）；
3. **库存校验**：下单数量 > 可用库存 → `E_STOCK_001`，草案都生成不了；
4. **风险分级**：金额 > 阈值（默认 5 万）→ 高风险；新客户首单 → 高风险；
5. **本人确认**：中风险草案确认后才写单、锁库存；
6. **审批路由**：高风险 → 销售主管；开票 → 财务总监；**不能审批自己发起的单据**（`E_PERM_003`）；
7. **状态机**：未审核不能出货、未出货不能开票（`E_FLOW_001/002`）；
8. **幂等**：同一动作重复确认只落一张单，第二次返回首次结果；
9. **草案过期**：30 分钟后确认返回 `E_DRAFT_EXPIRED`；
10. **审计链**：每次动作追加事件，哈希链可校验，改一条即可被定位；
11. **工具开关**：在能力配置里停用 `order.create_draft`，Agent 立刻无法开单；
12. **阈值生效**：把中风险阈值改成 2 万，再开 1.7 万的单仍是中风险，开 8.6 万的单升级审批。

## 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/auth/login` | 演示登录，返回 token |
| GET | `/api/bootstrap` | 身份、计数、建议问法 |
| POST | `/api/agent/chat` | 中文请求 → 意图 → 工具 → 卡片 |
| POST | `/api/actions/{id}/confirm` | 确认草案（幂等） |
| GET | `/api/approvals` | 待办 / 我发起的 / 已处理 |
| POST | `/api/approvals/{id}/decide` | 审批通过或驳回 |
| GET | `/api/audit` | 动作链路列表 |
| GET | `/api/traces/{id}` | 单条链路回放 + 哈希校验 |
| GET/PUT | `/api/tools`、`/api/tools/{name}` | 工具契约与启停 |
| GET/PUT | `/api/risk-rules`、`/api/risk-rules/{code}` | 规则启停、阈值 |
| GET | `/api/knowledge` | 知识条目 |
| GET/PUT | `/api/integration` | 外部 ERP 接入配置（厂商、Base URL、认证、接口映射） |
| POST | `/api/integration/test` | 测试外部 ERP 连通性，结果落库 |

## 代码结构

```
erp/
├── db.py        # 建表 + 种子数据 + 哈希链写入
├── core.py      # 权限、库存、应收、风险、落单、审批、审计
├── agent.py     # 中文意图解析 → 调用工具 → 组织卡片
├── server.py    # 标准库 HTTP 服务 + 静态托管
├── erp.db       # SQLite 数据库（运行时生成）
└── README.md
```

依赖：仅 Python 3.10+ 标准库（`http.server` + `sqlite3`），无需 pip 安装。

## 传统 ERP 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/erp/orders` | 订单列表（按状态、关键字、数据范围过滤） |
| GET | `/api/erp/orders/{no}` | 订单详情：明细、出货、发票、动作 |
| POST | `/api/erp/orders` | 表单开单；金额超阈值自动建「订单审核」审批 |
| PUT | `/api/erp/orders/{no}` | 修改待审核订单（明细、交期、仓库，重新校验） |
| POST | `/api/erp/orders/{no}/approve` | 订单审核通过 / 驳回（主管） |
| POST | `/api/erp/orders/{no}/cancel` | 取消已审核订单，释放锁定库存 |
| DELETE | `/api/erp/orders/{no}` | 删除待审核 / 已驳回订单 |
| POST | `/api/erp/orders/{no}/ship` | 整单出货：扣现存、释放锁定、生成出货单 |
| GET/POST | `/api/erp/customers` | 客户列表 / 新增客户 |
| PUT/DELETE | `/api/erp/customers/{code}` | 修改 / 删除客户（有订单时拒绝） |
| GET/POST | `/api/erp/products` | 商品列表 / 新增商品 |
| PUT/DELETE | `/api/erp/products/{sku}` | 修改 / 删除商品（有订单或库存时拒绝） |
| GET | `/api/erp/inventory` | 库存台账（按仓库范围过滤） |
| GET | `/api/erp/inbounds` | 入库单列表 |
| POST | `/api/erp/inbounds` | 登记入库：现存量增加、在途冲减 |
| GET | `/api/erp/shipments` | 出货单列表 |
| GET | `/api/erp/invoices` | 发票列表 |
| POST | `/api/erp/invoices` | 开票（仅财务角色；货单必须已出货） |
| GET | `/api/erp/customers` / `products` | 表单下拉数据 |
| GET | `/api/erp/overview` | 订单/库存概览 |

表单开单与 AI 开单走同一套校验：数据范围、库存、金额阈值、新客户首单。区别只是入口不同。

## 边界

- 这是**演示级 ERP 内核**，不是生产 ERP：没有税务/财务总账、没有并发压测、没有多租户；
- 对话解析是规则 + 正则，不调用大模型（模型接入点已在架构里预留）；
- 出货、开票走传统 ERP 页面（审批中心也可执行）；AI 对话目前只做查询与开单；
- 种子数据中的客户、金额、人员均为虚构。
