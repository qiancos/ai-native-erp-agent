/* ==========================================================================
   ERP-Agent 前端（对接本地 ERP 服务）
   数据全部来自 /api/*：查询、制单、确认、审批、审计、工具与规则
   ========================================================================== */

(function () {
  'use strict';

  const $ = (sel, root) => (root || document).querySelector(sel);
  const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));
  const esc = s => String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');

  const state = {
    token: localStorage.getItem('erp_token') || '',
    employee: null,
    accounts: [],
    bootstrap: null,
    busy: false,
    turns: 0,
    calls: 0,
    approvals: [],
    approvalTab: 'todo',
    selectedApproval: null,
    traces: [],
    selectedTrace: null,
    tools: [],
    rules: [],
    knowledge: [],
    kbCat: '全部',
    pendingDraft: null,
    lastMessage: '',
    offline: false,
    integration: null,
    store: null,
    offlineDrafts: {},
    view: 'workbench',
    auditFilter: { tab: 'all', emp: 'all', tool: 'all', result: 'all' },
    erp: {
      orders: [], inventory: [], inbounds: [], shipments: [], invoices: [],
      customers: [], products: [], orderStatus: 'ALL', orderQ: '', invQ: '',
    },
    master: { tab: 'customers', q: '', customers: [], products: [] },
    demo: new URLSearchParams(location.search).get('demo') || '',
  };

  const ROUTES = {
    workbench: ['智能工作台', '用一句话完成查询与开单 · 所有动作按你的权限执行并留痕'],
    orders: ['销售订单', '传统 ERP 表单开单 · 同样受数据范围、库存与风险约束'],
    inventory: ['库存台账', '可用 = 现存量 − 锁定量 + 可分配在途，开单出货实时联动'],
    inbounds: ['入库管理', '入库增加现存量、冲减在途，动作写入审计'],
    shipments: ['出货管理', '出货扣减现存量、释放锁定量，动作写入审计'],
    invoices: ['发票管理', '开票受角色与状态机双重校验'],
    master: ['基础资料', '客户与商品主数据维护，删除前校验订单与库存'],
    approvals: ['审批中心', '高风险动作在这里被人工确认，依据与链路随单展示'],
    audit: ['审计与回放', '按 trace 还原每一次动作：谁、何时、做了什么、谁确认'],
    tools: ['能力配置', '工具以契约注册，模型只能调用已授权能力'],
    risk: ['风控策略', '风险分级、拦截规则与阈值，全部服务端生效'],
    knowledge: ['业务知识库', '术语与口径的唯一来源，模型只查不改'],
    integration: ['外部 ERP 接入', '配置外部 ERP 地址、认证与接口映射，测试连通性'],
  };

  const AVATAR = {
    SALES_FOLLOWER: 'linear-gradient(145deg,#f0a33c,#de6a2f)',
    SALES_MANAGER: 'linear-gradient(145deg,#7f9cff,#3f5bd9)',
    FINANCE: 'linear-gradient(145deg,#6fc7c0,#0e9384)',
    FINANCE_DIRECTOR: 'linear-gradient(145deg,#a78bfa,#6d28d9)',
    RISK_ADMIN: 'linear-gradient(145deg,#8b9bb4,#475467)',
    WAREHOUSE: 'linear-gradient(145deg,#f4b860,#b25e09)',
  };

  /* ---------------------------------- API --------------------------------- */

  async function api(path, options) {
    const opts = options || {};
    const headers = { 'Content-Type': 'application/json' };
    if (state.token) headers.Authorization = 'Bearer ' + state.token;
    let res;
    try {
      res = await fetch(path, {
        method: opts.method || 'GET',
        headers,
        body: opts.body ? JSON.stringify(opts.body) : undefined,
      });
    } catch (networkError) {
      if (window.DEMO) {
        return offlineApi(path, opts);
      }
      throw networkError;
    }
    let data = {};
    try { data = await res.json(); } catch (e) { data = {}; }
    if (!res.ok) {
      const err = new Error(data.message || ('请求失败（' + res.status + '）'));
      err.code = data.error;
      err.payload = data;
      throw err;
    }
    return data;
  }

  function demoResponse(path, opts) {
    const method = (opts && opts.method) || 'GET';
    if (method !== 'GET') {
      throw new Error('离线演示模式：启动本地服务后可执行此操作');
    }
    const demo = window.DEMO;
    if (!demo) throw new Error('离线数据未加载');
    const url = new URL(path, 'http://offline.local');
    const p = url.pathname;
    const q = url.searchParams;
    const text = (q.get('q') || '').trim();

    if (p === '/api/auth/accounts') return { accounts: demo.accounts };
    if (p === '/api/me') return { employee: demo.employee };
    if (p === '/api/bootstrap') return demo.bootstrap;
    if (p === '/api/erp/overview') return demo.overview;
    if (p === '/api/erp/orders') {
      let items = (demo.orders || []).slice();
      const status = q.get('status');
      if (status && status !== 'ALL') {
        items = status === 'OPEN'
          ? items.filter(o => ['PENDING_APPROVAL', 'APPROVED', 'PARTIAL'].indexOf(o.status) >= 0)
          : items.filter(o => o.status === status);
      }
      if (text) {
        items = items.filter(o => o.order_no.indexOf(text) >= 0 || (o.customer_name || '').indexOf(text) >= 0);
      }
      return { items };
    }
    if (p.indexOf('/api/erp/orders/') === 0) {
      const no = p.split('/').pop();
      if (demo.orderDetails && demo.orderDetails[no]) return demo.orderDetails[no];
      throw new Error('离线演示数据中没有该订单');
    }
    if (p === '/api/erp/inventory') {
      let items = (demo.inventory || []).slice();
      if (text) items = items.filter(i => i.sku.indexOf(text) >= 0 || (i.product_name || '').indexOf(text) >= 0);
      return { items };
    }
    if (p === '/api/erp/inbounds') return { items: demo.inbounds || [] };
    if (p === '/api/erp/shipments') return { items: demo.shipments || [] };
    if (p === '/api/erp/invoices') return { items: demo.invoices || [] };
    if (p === '/api/erp/customers') return { items: demo.customers || [] };
    if (p === '/api/erp/products') return { items: demo.products || [] };
    if (p === '/api/approvals') return { items: demo.approvals || [] };
    if (p.indexOf('/api/approvals/') === 0) {
      const id = p.split('/').pop();
      if (demo.approvalDetails && demo.approvalDetails[id]) return demo.approvalDetails[id];
      throw new Error('离线演示数据中没有该审批单');
    }
    if (p === '/api/audit') return { items: demo.traces || [] };
    if (p.indexOf('/api/traces/') === 0) {
      const id = p.split('/').pop();
      if (demo.traceDetails && demo.traceDetails[id]) return demo.traceDetails[id];
      throw new Error('离线演示数据中没有该链路');
    }
    if (p === '/api/tools') return { items: demo.tools || [] };
    if (p === '/api/risk-rules') return { items: demo.rules || [] };
    if (p === '/api/knowledge') return { items: demo.knowledge || [] };
    if (p === '/api/integration') return demo.integration || {};
    throw new Error('离线演示数据中没有该接口');
  }

  /* ------------------------- 离线演示引擎 ------------------------- */

  const OFFLINE_KEY = 'erp_offline_v1';
  const OFFLINE_STATUS = {
    DRAFT: '草稿', PENDING_APPROVAL: '待审核', APPROVED: '已审核', PARTIAL: '部分出货',
    SHIPPED: '已出货', INVOICED: '已开票', REJECTED: '已驳回', CANCELLED: '已取消',
  };
  const WH_NAME = { 'WH-SH': '上海仓', 'WH-HZ': '杭州仓', 'WH-NJ': '南京仓', 'WH-GZ': '广州仓' };
  const WH_REGION = { 'WH-SH': 'EAST', 'WH-HZ': 'EAST', 'WH-NJ': 'EAST', 'WH-GZ': 'SOUTH' };
  const APPROVER_OF = { '订单审核': 'SALES_MANAGER', '创建货单': 'SALES_MANAGER', '出货登记': 'SALES_MANAGER', '开具发票': 'FINANCE_DIRECTOR', '发票红冲': 'FINANCE_DIRECTOR' };

  function offlineStore() {
    if (!state.store) {
      try {
        const raw = localStorage.getItem(OFFLINE_KEY);
        state.store = raw ? JSON.parse(raw) : JSON.parse(JSON.stringify(window.DEMO));
      } catch (e) {
        state.store = JSON.parse(JSON.stringify(window.DEMO));
      }
    }
    return state.store;
  }

  function offlineSave() {
    try { localStorage.setItem(OFFLINE_KEY, JSON.stringify(state.store)); } catch (e) { /* 忽略 */ }
  }

  function offlineReset() {
    try { localStorage.removeItem(OFFLINE_KEY); } catch (e) { /* 忽略 */ }
    state.store = null;
    location.reload();
  }

  function offlineName(username) {
    const acc = (state.accounts || []).find(a => a.username === username);
    return acc ? acc.name : username;
  }

  function offlineScope() { return state.employee ? state.employee.scope : ['*']; }
  function offlineInScope(region) { return offlineScope().indexOf('*') >= 0 || offlineScope().indexOf(region) >= 0; }
  function offlineWarehouses() {
    if (offlineScope().indexOf('*') >= 0) return Object.keys(WH_REGION);
    return Object.keys(WH_REGION).filter(w => offlineScope().indexOf(WH_REGION[w]) >= 0);
  }
  function offlineNow() {
    const d = new Date();
    const p = n => String(n).padStart(2, '0');
    return d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate()) + 'T' +
      p(d.getHours()) + ':' + p(d.getMinutes()) + ':' + p(d.getSeconds());
  }
  function offlineToday() {
    const d = new Date();
    const p = n => String(n).padStart(2, '0');
    return '' + d.getFullYear() + p(d.getMonth() + 1) + p(d.getDate());
  }
  function offlineFail(code, message) {
    const err = new Error(message);
    err.code = code;
    throw err;
  }
  function offlineStock(s, sku, warehouse) {
    return s.inventory
      .filter(i => i.sku === sku && (!warehouse || i.warehouse === warehouse))
      .reduce((sum, i) => sum + i.on_hand - i.locked + i.inbound_available, 0);
  }
  function offlineNextOrderNo(s) {
    const prefix = 'SO-' + offlineToday() + '-';
    let seq = 1;
    while (s.orders.some(o => o.order_no === prefix + String(seq).padStart(4, '0'))) seq += 1;
    return prefix + String(seq).padStart(4, '0');
  }
  function offlineFindCustomer(text) {
    const s = offlineStore();
    if (!text) return null;
    const code = (text.match(/C\d{4,6}/i) || [])[0];
    if (code) return s.customers.find(c => c.code.toUpperCase() === code.toUpperCase()) || null;
    return s.customers.find(c => text.indexOf(c.name) >= 0 ||
      text.indexOf(c.name.replace(/(机械|电子|实业|汽配|智能|精密)$/, '')) >= 0) || null;
  }
  function offlineFindSku(text) {
    const s = offlineStore();
    const upper = (text || '').toUpperCase();
    const alias = { A12: 'SKU-A12', B07: 'SKU-B07', C33: 'SKU-C33' };
    for (const key of Object.keys(alias)) {
      if (upper.indexOf(key) >= 0) return s.products.find(p => p.sku === alias[key]) || null;
    }
    const words = ['伺服', '编码器', '减速机'];
    for (const w of words) {
      if (text.indexOf(w) >= 0) return s.products.find(p => p.name.indexOf(w) >= 0) || null;
    }
    return null;
  }
  function offlineAppendTrace(s, traceId, tool, result, eventType, payload) {
    s.traces = s.traces || [];
    s.traceDetails = s.traceDetails || {};
    const at = offlineNow();
    s.traces.unshift({
      trace_id: traceId, time: at.slice(11, 19), date: at.slice(0, 10),
      employee: state.employee.name, tool: tool, result: result,
      status: result === '已拦截' ? 'BLOCKED' : result === '待审批' ? 'PENDING_APPROVAL' : 'DONE',
      risk: 'MEDIUM', events: 1, cost: '0.4s',
    });
    s.traceDetails[traceId] = {
      trace_id: traceId,
      meta: { employee: state.employee.name, tool: tool, risk: 'MEDIUM', total: '0.4s' },
      events: [{ type: eventType, time: at.slice(11, 19), payload: payload || {}, hash: 'offline' }],
      hash_ok: true,
    };
  }
  function offlineReceivables(s, customerCode) {
    const rows = s.orders.filter(o => o.customer_code === customerCode &&
      ['SHIPPED', 'PARTIAL', 'INVOICED'].indexOf(o.status) >= 0 &&
      Number(o.paid_amount || 0) < Number(o.total_amount));
    const today = new Date(offlineNow().slice(0, 10));
    return rows.map(o => {
      const open = Number(o.total_amount) - Number(o.paid_amount || 0);
      let due = null;
      if (o.shipped_at) {
        due = new Date(o.shipped_at.slice(0, 10));
        due.setDate(due.getDate() + Number(o.terms_days || 30));
      }
      const overdue = due ? Math.max(0, Math.floor((today - due) / 86400000)) : 0;
      return { order_no: o.order_no, open: open, shipped_at: (o.shipped_at || '').slice(0, 10), overdue: overdue };
    });
  }
  function offlineOverview() {
    const s = offlineStore();
    const orders = s.orders.filter(o => offlineInScope(o.region));
    const open = orders.filter(o => ['PENDING_APPROVAL', 'APPROVED', 'PARTIAL'].indexOf(o.status) >= 0);
    return {
      orders: orders.length,
      open_orders: open.length,
      open_amount: open.reduce((sum, o) => sum + Number(o.total_amount), 0),
      pending_ship: orders.filter(o => ['APPROVED', 'PARTIAL'].indexOf(o.status) >= 0).length,
      pending_approval: orders.filter(o => o.status === 'PENDING_APPROVAL').length,
      skus: new Set(orders.length ? s.inventory.filter(i => offlineWarehouses().indexOf(i.warehouse) >= 0).map(i => i.sku) : []).size,
      stock_qty: s.inventory.filter(i => offlineWarehouses().indexOf(i.warehouse) >= 0).reduce((sum, i) => sum + i.on_hand, 0),
      low_stock: s.inventory.filter(i => offlineWarehouses().indexOf(i.warehouse) >= 0 &&
        i.on_hand - i.locked + i.inbound_available < 100).length,
    };
  }
  function offlineBootstrap() {
    const s = offlineStore();
    const approvals = offlineApprovals();
    return {
      employee: state.employee,
      accounts: s.accounts,
      counts: {
        customers: s.customers.filter(c => offlineInScope(c.region)).length,
        orders: s.orders.filter(o => offlineInScope(o.region)).length,
        pending: approvals.filter(a => a.can_approve).length,
        mine_pending: approvals.filter(a => a.mine && a.status === 'PENDING').length,
        tools: s.tools.filter(t => t.enabled).length,
        audit: (s.traces || []).length,
      },
      server_time: offlineNow(),
      suggestions: s.bootstrap.suggestions,
    };
  }
  function offlineApprovals() {
    const s = offlineStore();
    const emp = state.employee;
    const role = emp.role_code;
    return s.approvals.map(a => {
      const need = APPROVER_OF[a.type];
      const can = a.status === 'PENDING' && a.applicant !== emp.username &&
        (role === 'RISK_ADMIN' || role === need);
      return Object.assign({}, a, {
        applicant_name: offlineName(a.applicant),
        can_approve: can,
        mine: a.applicant === emp.username,
        wait_minutes: Math.max(0, Math.floor((new Date() - new Date(a.created_at)) / 60000)) || 0,
      });
    });
  }
  function offlineRead(path, opts) {
    const s = offlineStore();
    const url = new URL(path, 'http://offline.local');
    const p = url.pathname;
    const q = url.searchParams;
    const text = (q.get('q') || '').trim();

    if (p === '/api/auth/accounts') return { accounts: s.accounts };
    if (p === '/api/me') return { employee: state.employee };
    if (p === '/api/bootstrap') return offlineBootstrap();
    if (p === '/api/erp/overview') return offlineOverview();
    if (p === '/api/erp/orders') {
      let items = s.orders.filter(o => offlineInScope(o.region));
      const status = q.get('status');
      if (status && status !== 'ALL') {
        items = status === 'OPEN'
          ? items.filter(o => ['PENDING_APPROVAL', 'APPROVED', 'PARTIAL'].indexOf(o.status) >= 0)
          : items.filter(o => o.status === status);
      }
      if (text) items = items.filter(o => o.order_no.indexOf(text) >= 0 || (o.customer_name || '').indexOf(text) >= 0);
      return { items: items };
    }
    if (p.indexOf('/api/erp/orders/') === 0) {
      const no = decodeURIComponent(p.split('/').pop());
      if (s.orderDetails && s.orderDetails[no]) return s.orderDetails[no];
      throw new Error('离线演示数据中没有该订单');
    }
    if (p === '/api/erp/inventory') {
      let items = s.inventory.filter(i => offlineWarehouses().indexOf(i.warehouse) >= 0);
      if (text) items = items.filter(i => i.sku.indexOf(text) >= 0 || (i.product_name || '').indexOf(text) >= 0);
      return { items: items };
    }
    if (p === '/api/erp/inbounds') {
      return { items: s.inbounds.filter(i => offlineWarehouses().indexOf(i.warehouse) >= 0) };
    }
    if (p === '/api/erp/shipments') {
      return { items: s.shipments.filter(i => offlineInScope(i.customer_region || 'EAST')) };
    }
    if (p === '/api/erp/invoices') {
      return { items: s.invoices };
    }
    if (p === '/api/erp/customers') {
      return { items: s.customers.filter(c => offlineInScope(c.region)).map(c => Object.assign({}, c, {
        order_count: s.orders.filter(o => o.customer_code === c.code).length,
      })) };
    }
    if (p === '/api/erp/products') return { items: s.products };
    if (p === '/api/approvals') return { items: offlineApprovals() };
    if (p.indexOf('/api/approvals/') === 0) {
      const id = p.split('/').pop();
      const a = offlineApprovals().find(x => x.approval_id === id);
      if (a) return a;
      throw new Error('离线演示数据中没有该审批单');
    }
    if (p === '/api/audit') return { items: s.traces || [] };
    if (p.indexOf('/api/traces/') === 0) {
      const id = p.split('/').pop();
      if (s.traceDetails && s.traceDetails[id]) return s.traceDetails[id];
      return { trace_id: id, meta: {}, events: [], hash_ok: true };
    }
    if (p === '/api/tools') return { items: s.tools };
    if (p === '/api/risk-rules') return { items: s.rules };
    if (p === '/api/knowledge') return { items: s.knowledge };
    if (p === '/api/integration') return s.integration;
    throw new Error('离线演示数据中没有该接口');
  }
  function offlineAdjustStock(s, sku, warehouse, onHandDelta, lockedDelta) {
    const row = s.inventory.find(i => i.sku === sku && i.warehouse === warehouse);
    if (!row) return;
    row.on_hand = Math.max(0, row.on_hand + onHandDelta);
    row.locked = Math.max(0, row.locked + lockedDelta);
    row.available = row.on_hand - row.locked + row.inbound_available;
    row.stock_state = row.available < 100 ? '低库存' : '正常';
  }

  function offlineRisk(s, customer, total) {
    const reasons = [];
    const amt = s.rules.find(r => r.rule_code === 'RISK-AMT-001' && r.enabled);
    const hasOrders = s.orders.some(o => o.customer_code === customer.code && o.status !== 'DRAFT');
    if (amt && total > Number(amt.threshold || 50000)) {
      reasons.push({ rule: 'RISK-AMT-001', text: '金额 ' + moneyText(total) + ' 超过中风险阈值 ' + moneyText(Number(amt.threshold || 50000)) });
    }
    const newRule = s.rules.find(r => r.rule_code === 'RISK-NEW-002' && r.enabled);
    if (newRule && !hasOrders) reasons.push({ rule: 'RISK-NEW-002', text: '新客户首单，需主管确认' });
    return { high: reasons.length > 0, reasons: reasons };
  }

  function offlineMakeApproval(s, order, customer, items, total, reasons) {
    const id = 'AP-' + offlineToday() + '-' + Math.random().toString(16).slice(2, 5).toUpperCase();
    const check = [
      { ok: true, text: '客户在数据范围内（' + customer.name + '）' },
    ];
    items.forEach(i => {
      const avail = offlineStock(s, i.sku, order.warehouse);
      check.push({ ok: avail >= i.qty, text: i.sku + ' 可用 ' + avail + ' 件 ≥ 需求 ' + i.qty + ' 件' });
    });
    s.approvals.unshift({
      approval_id: id, action_id: 'order:' + order.order_no, type: '订单审核',
      applicant: state.employee.username, customer_code: customer.code, customer_name: customer.name,
      amount: total, rule_code: reasons[0] ? reasons[0].rule : 'RISK-AMT-001',
      reason: reasons[0] ? reasons[0].text : '金额超过阈值，需主管审批',
      risk_level: 'HIGH', payload: { order_no: order.order_no, trace_id: order.trace_id, items: items, warehouse: order.warehouse },
      checks: check, status: 'PENDING', created_at: offlineNow(), decided_at: null, comment: null,
    });
    return id;
  }

  function offlineCreateOrder(body, forceApproved) {
    const s = offlineStore();
    const emp = state.employee;
    const customer = s.customers.find(c => c.code === body.customerCode);
    if (!customer) offlineFail('E_PARAM_001', '客户不存在');
    if (!offlineInScope(customer.region)) offlineFail('E_PERM_001', '该客户不在你的数据范围内');
    const warehouse = body.warehouse || 'WH-SH';
    if (offlineWarehouses().indexOf(warehouse) < 0) offlineFail('E_PERM_004', '仓库不在你的数据范围内');

    const items = [];
    for (const raw of body.items || []) {
      const prod = s.products.find(p => p.sku === raw.sku);
      const qty = Number(raw.qty);
      const price = Number(raw.unitPrice);
      if (!prod) offlineFail('E_PARAM_001', '商品不存在或已停用');
      if (!(qty > 0) || !(price > 0)) offlineFail('E_PARAM_001', '数量与单价必须大于 0');
      const avail = offlineStock(s, raw.sku, warehouse);
      if (qty > avail) offlineFail('E_STOCK_001', raw.sku + ' 在所选仓库可用 ' + avail + ' 件，不足 ' + qty + ' 件');
      items.push({ sku: prod.sku, name: prod.name, qty: qty, unit_price: price });
    }
    if (!items.length) offlineFail('E_PARAM_001', '至少需要一行商品');

    const total = items.reduce((sum, i) => sum + i.qty * i.unit_price, 0);
    const risk = offlineRisk(s, customer, total);
    const no = offlineNextOrderNo(s);
    const trace = 'tr-off-' + Math.random().toString(16).slice(2, 8);
    const at = offlineNow();
    const status = (risk.high && !forceApproved) ? 'PENDING_APPROVAL' : 'APPROVED';
    const row = {
      order_no: no, customer_code: customer.code, customer_name: customer.name, region: customer.region,
      status: status, status_label: OFFLINE_STATUS[status], total_amount: total,
      delivery_date: body.deliveryDate || at.slice(0, 10), warehouse: warehouse, warehouse_name: WH_NAME[warehouse],
      created_by: emp.username, created_at: at, approved_at: risk.high ? null : at, shipped_at: null,
      invoiced_at: null, paid_amount: 0, terms_days: 30, trace_id: trace, note: body.note || '',
      total_qty: items.reduce((sum, i) => sum + i.qty, 0), shipped_qty: 0,
    };
    s.orders.unshift(row);
    s.orderDetails[no] = Object.assign({}, row, {
      contact: customer.contact, address: customer.address,
      items: items.map(i => Object.assign({}, i, { shipped_qty: 0 })),
      shipments: [], invoices: [],
      events: [{ event_type: 'ORDER_CREATED', payload: { order_no: no, total: total }, created_at: at }],
    });

    let approvalId = null;
    if (risk.high && !forceApproved) {
      approvalId = offlineMakeApproval(s, row, customer, items, total, risk.reasons);
      s.orderDetails[no].events.push({ event_type: 'APPROVAL_CREATED', payload: { approval_id: approvalId }, created_at: at });
    } else {
      items.forEach(i => offlineAdjustStock(s, i.sku, warehouse, 0, i.qty));
      s.orderDetails[no].events.push({ event_type: 'STOCK_LOCKED', payload: { items: items }, created_at: at });
    }
    offlineAppendTrace(s, trace, 'order.create_draft', '成功', 'ORDER_CREATED', { order_no: no, total: total });
    offlineSave();
    return { order_no: no, status: status, risk: risk.high ? 'HIGH' : 'MEDIUM', total: total,
      trace_id: trace, approval_id: approvalId };
  }

  function offlineUpdateOrder(no, body) {
    const s = offlineStore();
    const order = s.orders.find(o => o.order_no === no);
    if (!order) offlineFail('E_NOT_FOUND', '订单不存在');
    if (!offlineInScope(order.region)) offlineFail('E_PERM_001', '该客户不在你的数据范围内');
    if (order.status !== 'PENDING_APPROVAL') offlineFail('E_FLOW_001', '只有「待审核」订单可以修改');
    if (['SALES_FOLLOWER', 'SALES_MANAGER', 'RISK_ADMIN'].indexOf(state.employee.role_code) < 0) {
      offlineFail('E_PERM_002', '当前角色没有修改订单权限');
    }
    const warehouse = body.warehouse || order.warehouse;
    if (offlineWarehouses().indexOf(warehouse) < 0) offlineFail('E_PERM_004', '仓库不在你的数据范围内');
    const items = [];
    for (const raw of body.items || []) {
      const prod = s.products.find(p => p.sku === raw.sku);
      const qty = Number(raw.qty), price = Number(raw.unitPrice);
      if (!prod) offlineFail('E_PARAM_001', '商品不存在');
      if (!(qty > 0) || !(price > 0)) offlineFail('E_PARAM_001', '数量与单价必须大于 0');
      const avail = offlineStock(s, raw.sku, warehouse);
      if (qty > avail) offlineFail('E_STOCK_001', raw.sku + ' 可用 ' + avail + ' 件，不足 ' + qty + ' 件');
      items.push({ sku: prod.sku, name: prod.name, qty: qty, unit_price: price });
    }
    const total = items.reduce((sum, i) => sum + i.qty * i.unit_price, 0);
    order.total_amount = total;
    order.delivery_date = body.deliveryDate || order.delivery_date;
    order.warehouse = warehouse;
    order.warehouse_name = WH_NAME[warehouse];
    order.note = body.note || '';
    order.total_qty = items.reduce((sum, i) => sum + i.qty, 0);
    const detail = s.orderDetails[no];
    detail.items = items.map(i => Object.assign({}, i, { shipped_qty: 0 }));
    detail.delivery_date = order.delivery_date;
    detail.warehouse = warehouse;
    detail.total_amount = total;
    const approval = s.approvals.find(a => a.status === 'PENDING' && a.payload && a.payload.order_no === no);
    if (approval) {
      approval.amount = total;
      approval.payload.items = items;
      approval.payload.warehouse = warehouse;
      approval.reason = offlineRisk(s, { code: order.customer_code }, total).reasons[0]
        ? offlineRisk(s, { code: order.customer_code }, total).reasons[0].text
        : approval.reason;
    }
    detail.events.push({ event_type: 'ORDER_UPDATED', payload: { total: total }, created_at: offlineNow() });
    offlineAppendTrace(s, order.trace_id, 'order.update', '成功', 'ORDER_UPDATED', { order_no: no, total: total });
    offlineSave();
    return { order_no: no, status: order.status, total: total, trace_id: order.trace_id };
  }

  function offlineApproveOrder(no, decision, comment) {
    const s = offlineStore();
    if (['SALES_MANAGER', 'RISK_ADMIN'].indexOf(state.employee.role_code) < 0) {
      offlineFail('E_PERM_002', '当前角色没有订单审核权限');
    }
    const order = s.orders.find(o => o.order_no === no);
    if (!order) offlineFail('E_NOT_FOUND', '订单不存在');
    if (!offlineInScope(order.region)) offlineFail('E_PERM_001', '该客户不在你的数据范围内');
    if (order.status !== 'PENDING_APPROVAL') offlineFail('E_FLOW_001', '订单当前不是待审核状态');
    const approval = s.approvals.find(a => a.status === 'PENDING' && a.payload && a.payload.order_no === no);
    const detail = s.orderDetails[no];
    const at = offlineNow();

    if (decision === 'REJECT') {
      order.status = 'REJECTED';
      order.status_label = OFFLINE_STATUS.REJECTED;
      if (approval) {
        approval.status = 'REJECTED'; approval.approver = state.employee.username;
        approval.comment = comment || ''; approval.decided_at = at;
      }
      detail.events.push({ event_type: 'ORDER_REJECTED', payload: { by: state.employee.username }, created_at: at });
      offlineAppendTrace(s, order.trace_id, 'order.approve', '已驳回', 'ORDER_REJECTED', { order_no: no });
      offlineSave();
      return { order_no: no, status: 'REJECTED', trace_id: order.trace_id };
    }

    for (const item of detail.items) {
      const avail = offlineStock(s, item.sku, order.warehouse);
      if (avail < item.qty) offlineFail('E_STOCK_001', item.sku + ' 可用库存已变为 ' + avail);
    }
    detail.items.forEach(item => offlineAdjustStock(s, item.sku, order.warehouse, 0, item.qty));
    order.status = 'APPROVED';
    order.status_label = OFFLINE_STATUS.APPROVED;
    order.approved_at = at;
    if (approval) {
      approval.status = 'APPROVED'; approval.approver = state.employee.username;
      approval.comment = comment || ''; approval.decided_at = at;
    }
    detail.events.push({ event_type: 'ORDER_APPROVED', payload: { by: state.employee.username }, created_at: at });
    offlineAppendTrace(s, order.trace_id, 'order.approve', '成功', 'ORDER_APPROVED', { order_no: no });
    offlineSave();
    return { order_no: no, status: 'APPROVED', trace_id: order.trace_id };
  }

  function offlineCancelOrder(no) {
    const s = offlineStore();
    const order = s.orders.find(o => o.order_no === no);
    if (!order) offlineFail('E_NOT_FOUND', '订单不存在');
    if (['APPROVED', 'PARTIAL'].indexOf(order.status) < 0) offlineFail('E_FLOW_001', '只有已审核订单可以取消');
    const detail = s.orderDetails[no];
    detail.items.forEach(item => {
      const remain = item.qty - (item.shipped_qty || 0);
      if (remain > 0) offlineAdjustStock(s, item.sku, order.warehouse, 0, -remain);
    });
    order.status = 'CANCELLED';
    order.status_label = OFFLINE_STATUS.CANCELLED;
    detail.status = order.status;
    detail.status_label = order.status_label;
    detail.events.push({ event_type: 'ORDER_CANCELLED', payload: { by: state.employee.username }, created_at: offlineNow() });
    offlineAppendTrace(s, order.trace_id, 'order.cancel', '成功', 'ORDER_CANCELLED', { order_no: no });
    offlineSave();
    return { order_no: no, status: 'CANCELLED' };
  }

  function offlineDeleteOrder(no) {
    const s = offlineStore();
    const order = s.orders.find(o => o.order_no === no);
    if (!order) offlineFail('E_NOT_FOUND', '订单不存在');
    if (['PENDING_APPROVAL', 'REJECTED'].indexOf(order.status) < 0) {
      offlineFail('E_FLOW_001', '已审核订单请先「取消订单」，不能直接删除');
    }
    if (order.created_by !== state.employee.username &&
      ['SALES_MANAGER', 'RISK_ADMIN'].indexOf(state.employee.role_code) < 0) {
      offlineFail('E_PERM_002', '只能删除自己创建的订单');
    }
    s.orders = s.orders.filter(o => o.order_no !== no);
    delete s.orderDetails[no];
    s.approvals = s.approvals.filter(a => !(a.payload && a.payload.order_no === no));
    offlineAppendTrace(s, order.trace_id, 'order.delete', '成功', 'ORDER_DELETED', { order_no: no });
    offlineSave();
    return { order_no: no, deleted: true };
  }

  function offlineShipOrder(no) {
    const s = offlineStore();
    const order = s.orders.find(o => o.order_no === no);
    if (!order) offlineFail('E_NOT_FOUND', '订单不存在');
    if (['APPROVED', 'PARTIAL'].indexOf(order.status) < 0) offlineFail('E_FLOW_001', '货单当前状态不允许出货');
    const detail = s.orderDetails[no];
    let totalQty = 0;
    detail.items.forEach(item => {
      const remain = item.qty - (item.shipped_qty || 0);
      if (remain <= 0) return;
      item.shipped_qty = item.qty;
      offlineAdjustStock(s, item.sku, order.warehouse, -remain, -remain);
      totalQty += remain;
    });
    if (!totalQty) offlineFail('E_FLOW_001', '没有待出货数量');
    const shipmentNo = 'SH-' + offlineToday() + '-' + Math.random().toString(16).slice(2, 6).toUpperCase();
    s.shipments.unshift({
      shipment_no: shipmentNo, order_no: no, qty: totalQty, warehouse: order.warehouse,
      warehouse_name: order.warehouse_name, status: 'DONE', created_at: offlineNow(),
      created_by: state.employee.username, customer_code: order.customer_code,
      customer_name: order.customer_name, customer_region: order.region,
    });
    detail.shipments.push({ shipment_no: shipmentNo, order_no: no, qty: totalQty, created_at: offlineNow() });
    order.status = 'SHIPPED';
    order.status_label = OFFLINE_STATUS.SHIPPED;
    order.shipped_at = offlineNow();
    order.shipped_qty = order.total_qty;
    detail.status = order.status;
    detail.status_label = order.status_label;
    detail.events.push({ event_type: 'SHIPMENT_CREATED', payload: { shipment_no: shipmentNo, qty: totalQty }, created_at: offlineNow() });
    offlineAppendTrace(s, order.trace_id, 'shipment.create', '成功', 'SHIPMENT_CREATED', { order_no: no, qty: totalQty });
    offlineSave();
    return { shipment_no: shipmentNo, order_no: no, qty: totalQty, status: 'SHIPPED', trace_id: order.trace_id };
  }

  function offlineInvoice(body) {
    const s = offlineStore();
    if (['FINANCE', 'FINANCE_DIRECTOR'].indexOf(state.employee.role_code) < 0) {
      offlineFail('E_PERM_002', '当前角色没有开票权限');
    }
    const order = s.orders.find(o => o.order_no === body.orderNo);
    if (!order) offlineFail('E_NOT_FOUND', '货单不存在');
    if (order.status !== 'SHIPPED') offlineFail('E_FLOW_002', '货单当前为 ' + order.status_label + '，未出货不能开票');
    const invoiceNo = 'INV-' + offlineToday() + '-' + Math.random().toString(16).slice(2, 6).toUpperCase();
    s.invoices.unshift({
      invoice_no: invoiceNo, order_no: order.order_no, amount: Number(body.amount),
      tax_type: body.taxType || 'VAT_13', status: 'ISSUED', created_at: offlineNow(),
      created_by: state.employee.username, customer_code: order.customer_code, customer_name: order.customer_name,
    });
    s.orderDetails[order.order_no].invoices.push({ invoice_no: invoiceNo, amount: Number(body.amount), created_at: offlineNow() });
    order.status = 'INVOICED';
    order.status_label = OFFLINE_STATUS.INVOICED;
    order.invoiced_at = offlineNow();
    s.orderDetails[order.order_no].status = order.status;
    s.orderDetails[order.order_no].status_label = order.status_label;
    s.orderDetails[order.order_no].events.push({ event_type: 'INVOICE_ISSUED', payload: { invoice_no: invoiceNo, amount: body.amount }, created_at: offlineNow() });
    offlineAppendTrace(s, order.trace_id, 'invoice.issue', '成功', 'INVOICE_ISSUED', { invoice_no: invoiceNo });
    offlineSave();
    return { invoice_no: invoiceNo, order_no: order.order_no, status: 'INVOICED', trace_id: order.trace_id };
  }

  function offlineInbound(body) {
    const s = offlineStore();
    if (['WAREHOUSE', 'RISK_ADMIN', 'SALES_MANAGER', 'SALES_FOLLOWER'].indexOf(state.employee.role_code) < 0) {
      offlineFail('E_PERM_002', '当前角色没有入库权限');
    }
    const prod = s.products.find(p => p.sku === body.sku);
    if (!prod) offlineFail('E_PARAM_001', '商品不存在');
    const qty = Number(body.qty);
    if (!(qty > 0)) offlineFail('E_PARAM_001', '入库数量必须大于 0');
    const wh = body.warehouse || 'WH-SH';
    if (offlineWarehouses().indexOf(wh) < 0) offlineFail('E_PERM_004', '仓库不在你的数据范围内');
    const inboundNo = 'IN-' + offlineToday() + '-' + Math.random().toString(16).slice(2, 6).toUpperCase();
    s.inbounds.unshift({
      inbound_no: inboundNo, supplier: body.supplier || '未填写', sku: prod.sku, qty: qty,
      warehouse: wh, warehouse_name: WH_NAME[wh], status: 'DONE', created_at: offlineNow(),
      created_by: state.employee.username, operator_name: state.employee.name, product_name: prod.name,
    });
    const row = s.inventory.find(i => i.sku === prod.sku && i.warehouse === wh);
    if (row) {
      row.on_hand += qty;
      row.inbound = Math.max(0, row.inbound - qty);
      row.available = row.on_hand - row.locked + row.inbound_available;
      row.stock_state = row.available < 100 ? '低库存' : '正常';
    }
    offlineAppendTrace(s, 'tr-off-' + Math.random().toString(16).slice(2, 8), 'inbound.create', '成功', 'INBOUND_CREATED', { inbound_no: inboundNo, qty: qty });
    offlineSave();
    return { inbound_no: inboundNo, qty: qty, sku: prod.sku, warehouse: wh };
  }

  function offlineSaveCustomer(code, body) {
    const s = offlineStore();
    if (['SALES_FOLLOWER', 'SALES_MANAGER', 'RISK_ADMIN'].indexOf(state.employee.role_code) < 0) {
      offlineFail('E_PERM_002', '当前角色没有客户维护权限');
    }
    if (!body.name) offlineFail('E_PARAM_001', '请填写客户名称');
    const region = body.region || 'EAST';
    if (!offlineInScope(region)) offlineFail('E_PERM_001', '不能维护数据范围外的客户');
    let target = code ? s.customers.find(c => c.code === code) : null;
    if (code && !target) offlineFail('E_NOT_FOUND', '客户不存在');
    if (!target) {
      let max = 10000;
      s.customers.forEach(c => { const n = parseInt(String(c.code).replace(/\D/g, ''), 10); if (n > max) max = n; });
      target = { code: 'C' + String(max + 1).padStart(5, '0'), status: 'ACTIVE', order_count: 0 };
      s.customers.push(target);
    }
    target.name = body.name;
    target.region = region;
    target.contact = body.contact || '';
    target.phone = body.phone || '';
    target.address = body.address || '';
    target.credit_limit = Number(body.credit_limit || 0);
    offlineSave();
    return { code: target.code, name: target.name };
  }

  function offlineDeleteCustomer(code) {
    const s = offlineStore();
    if (['SALES_MANAGER', 'RISK_ADMIN'].indexOf(state.employee.role_code) < 0) {
      offlineFail('E_PERM_002', '只有主管可以删除客户');
    }
    const used = s.orders.filter(o => o.customer_code === code).length;
    if (used) offlineFail('E_FLOW_003', '该客户已有 ' + used + ' 张订单，不能删除');
    s.customers = s.customers.filter(c => c.code !== code);
    offlineSave();
    return { code: code, deleted: true };
  }

  function offlineSaveProduct(sku, body) {
    const s = offlineStore();
    if (['SALES_MANAGER', 'RISK_ADMIN'].indexOf(state.employee.role_code) < 0) {
      offlineFail('E_PERM_002', '只有主管可以维护商品');
    }
    if (!body.name || !(Number(body.list_price) > 0)) offlineFail('E_PARAM_001', '请填写商品名称与标准价');
    let target = sku ? s.products.find(p => p.sku === sku) : null;
    if (sku && !target) offlineFail('E_NOT_FOUND', '商品不存在');
    if (!target) {
      let max = 0;
      s.products.forEach(p => { const n = parseInt(String(p.sku).replace(/\D/g, ''), 10); if (n > max) max = n; });
      target = { sku: 'SKU-' + String(max + 1).padStart(3, '0'), status: 'ACTIVE' };
      s.products.push(target);
    }
    target.name = body.name;
    target.category = body.category || '';
    target.unit = body.unit || '件';
    target.list_price = Number(body.list_price);
    offlineSave();
    return { sku: target.sku, name: target.name };
  }

  function offlineDeleteProduct(sku) {
    const s = offlineStore();
    if (['SALES_MANAGER', 'RISK_ADMIN'].indexOf(state.employee.role_code) < 0) {
      offlineFail('E_PERM_002', '只有主管可以删除商品');
    }
    const used = s.orders.some(o => (s.orderDetails[o.order_no].items || []).some(i => i.sku === sku));
    const stocked = s.inventory.some(i => i.sku === sku && i.on_hand > 0);
    if (used || stocked) offlineFail('E_FLOW_003', '商品已有订单或库存，不能删除');
    s.products = s.products.filter(p => p.sku !== sku);
    offlineSave();
    return { sku: sku, deleted: true };
  }

  function offlineDecideApproval(id, decision, comment) {
    const s = offlineStore();
    const a = s.approvals.find(x => x.approval_id === id);
    if (!a) offlineFail('E_NOT_FOUND', '审批单不存在');
    if (a.status !== 'PENDING') offlineFail('E_FLOW_001', '该审批已处理');
    if (a.applicant === state.employee.username) offlineFail('E_PERM_003', '不能审批自己发起的单据');
    const need = APPROVER_OF[a.type];
    if (!(state.employee.role_code === 'RISK_ADMIN' || state.employee.role_code === need)) {
      offlineFail('E_PERM_002', '当前角色无权审批该单据');
    }
    if (decision === 'REJECT') {
      a.status = 'REJECTED'; a.approver = state.employee.username; a.comment = comment || ''; a.decided_at = offlineNow();
      if (a.type === '订单审核' && a.payload && a.payload.order_no) {
        const order = s.orders.find(o => o.order_no === a.payload.order_no);
        if (order && order.status === 'PENDING_APPROVAL') {
          order.status = 'REJECTED'; order.status_label = OFFLINE_STATUS.REJECTED;
        }
      }
      offlineSave();
      return { type: 'rejected', message: '已驳回' };
    }
    if (a.type === '订单审核' && a.payload && a.payload.order_no) {
      const res = offlineApproveOrder(a.payload.order_no, 'APPROVE', comment);
      return { type: 'approved', result: res, message: '已通过并执行' };
    }
    if (a.type === '创建货单' && a.payload) {
      const res = offlineCreateOrder({
        customerCode: a.payload.customer_code,
        warehouse: a.payload.warehouse,
        deliveryDate: a.payload.delivery_date,
        items: (a.payload.items || []).map(i => ({ sku: i.sku, qty: i.qty, unitPrice: i.unit_price })),
      }, true);
      a.status = 'APPROVED';
      a.approver = state.employee.username;
      a.comment = comment || '';
      a.decided_at = offlineNow();
      offlineSave();
      return { type: 'approved', result: res, message: '已通过并执行' };
    }
    if (a.type === '开具发票' && a.payload) {
      const res = offlineInvoice({ orderNo: a.payload.order_no, amount: a.payload.amount,
        taxType: a.payload.tax_type });
      a.status = 'APPROVED'; a.approver = state.employee.username; a.comment = comment || ''; a.decided_at = offlineNow();
      offlineSave();
      return { type: 'approved', result: res, message: '已通过并执行' };
    }
    a.status = 'APPROVED'; a.approver = state.employee.username; a.comment = comment || ''; a.decided_at = offlineNow();
    offlineSave();
    return { type: 'approved', message: '已通过' };
  }

  function offlineWrite(url, method, body) {
    const s = offlineStore();
    const p = url.pathname;
    let m;

    if (p === '/api/erp/orders' && method === 'POST') return offlineCreateOrder(body);
    m = p.match(/^\/api\/erp\/orders\/([^/]+)\/approve$/);
    if (m && method === 'POST') return offlineApproveOrder(decodeURIComponent(m[1]), body.decision || 'APPROVE', body.comment || '');
    m = p.match(/^\/api\/erp\/orders\/([^/]+)\/cancel$/);
    if (m && method === 'POST') return offlineCancelOrder(decodeURIComponent(m[1]));
    m = p.match(/^\/api\/erp\/orders\/([^/]+)\/ship$/);
    if (m && method === 'POST') return offlineShipOrder(decodeURIComponent(m[1]));
    m = p.match(/^\/api\/erp\/orders\/([^/]+)$/);
    if (m && method === 'PUT') return offlineUpdateOrder(decodeURIComponent(m[1]), body);
    if (m && method === 'DELETE') return offlineDeleteOrder(decodeURIComponent(m[1]));
    if (p === '/api/erp/invoices' && method === 'POST') return offlineInvoice(body);
    if (p === '/api/erp/inbounds' && method === 'POST') return offlineInbound(body);
    if (p === '/api/erp/customers' && method === 'POST') return offlineSaveCustomer(null, body);
    m = p.match(/^\/api\/erp\/customers\/([^/]+)$/);
    if (m && method === 'PUT') return offlineSaveCustomer(decodeURIComponent(m[1]), body);
    if (m && method === 'DELETE') return offlineDeleteCustomer(decodeURIComponent(m[1]));
    if (p === '/api/erp/products' && method === 'POST') return offlineSaveProduct(null, body);
    m = p.match(/^\/api\/erp\/products\/([^/]+)$/);
    if (m && method === 'PUT') return offlineSaveProduct(decodeURIComponent(m[1]), body);
    if (m && method === 'DELETE') return offlineDeleteProduct(decodeURIComponent(m[1]));
    m = p.match(/^\/api\/approvals\/([^/]+)\/decide$/);
    if (m && method === 'POST') return offlineDecideApproval(decodeURIComponent(m[1]), body.decision || 'APPROVE', body.comment || '');
    m = p.match(/^\/api\/actions\/([^/]+)\/confirm$/);
    if (m && method === 'POST') return offlineConfirmAction(decodeURIComponent(m[1]));
    m = p.match(/^\/api\/tools\/([^/]+)$/);
    if (m && method === 'PUT') {
      const tool = s.tools.find(t => t.name === decodeURIComponent(m[1]));
      if (!tool) offlineFail('E_NOT_FOUND', '工具不存在');
      tool.enabled = !!body.enabled;
      offlineSave();
      return { ok: true };
    }
    m = p.match(/^\/api\/risk-rules\/([^/]+)$/);
    if (m && method === 'PUT') {
      const rule = s.rules.find(r => r.rule_code === decodeURIComponent(m[1]));
      if (!rule) offlineFail('E_NOT_FOUND', '规则不存在');
      if (body.enabled !== undefined) rule.enabled = !!body.enabled;
      if (body.threshold !== undefined) { rule.threshold = Number(body.threshold); rule.version += 1; }
      offlineSave();
      return { ok: true };
    }
    if (p === '/api/integration' && method === 'PUT') {
      const ig = s.integration;
      ig.vendor = body.vendor || '';
      ig.base_url = (body.base_url || '').replace(/\/+$/, '');
      ig.auth_type = body.auth_type || 'Bearer';
      if (body.credential) ig.credential_set = true;
      if (body.clear_credential) ig.credential_set = false;
      ig.endpoints = Object.assign({}, ig.endpoints, body.endpoints || {});
      ig.enabled = !!body.enabled && !!ig.base_url;
      ig.status = ig.base_url ? (ig.enabled ? 'CONFIGURED' : 'DISABLED') : 'UNCONFIGURED';
      ig.updated_at = offlineNow();
      offlineSave();
      return ig;
    }
    if (p === '/api/integration/test' && method === 'POST') {
      const ig = s.integration;
      if (!ig.base_url) return { ok: false, message: '请先填写外部 ERP 的 Base URL' };
      const ok = /^https?:\/\//.test(ig.base_url);
      ig.status = ok ? 'CONNECTED' : 'FAILED';
      ig.last_test_at = offlineNow();
      ig.last_test_message = ok ? '离线模拟：地址格式有效（未真实发起请求）' : '地址必须以 http(s):// 开头';
      offlineSave();
      return { ok: ok, message: ig.last_test_message, target: ig.base_url + (ig.endpoints.health || ''), tested_at: ig.last_test_at };
    }
    return offlineFail('E_NOT_FOUND', '离线演示暂不支持该操作');
  }

  function offlineTraceId() {
    return 'tr-off-' + Math.random().toString(16).slice(2, 8);
  }

  function offlineClarify(text, options, trace) {
    return { type: 'clarify', text: text, options: options || [], trace_id: trace };
  }

  function offlineBlocked(code, title, subtitle, reason, text, checks, trace) {
    const s = offlineStore();
    offlineAppendTrace(s, trace, 'guard.check', '已拦截', 'BLOCKED', { code: code });
    offlineSave();
    return {
      type: 'blocked', code: code, title: title, subtitle: subtitle, text: text,
      reason: reason, checks: checks || [], trace_id: trace,
    };
  }

  function offlineScopeBlocked(customer) {
    const trace = offlineTraceId();
    const regionLabel = { EAST: '华东大区', SOUTH: '华南大区', NORTH: '华北大区' }[customer.region] || customer.region;
    return offlineBlocked(
      'E_PERM_001', '请求已被拒绝', '规则命中 · 数据范围校验',
      '该客户（' + customer.code + ' ' + customer.name + '）归属' + regionLabel + '，不在你的数据范围内。请求未执行，也未读取任何订单数据。',
      '我尝试执行这个请求，但在数据范围校验环节被拦下了 —— 没有任何数据被读取。',
      [
        { ok: true, text: '身份校验', tail: state.employee.name + ' · ' + state.employee.scope_label },
        { ok: true, text: '工具授权', tail: 'order.query 已授权' },
        { ok: false, text: '数据范围', tail: '目标归属 ' + regionLabel },
      ], trace);
  }

  function offlineReceivableCard(customer) {
    const s = offlineStore();
    const rec = offlineReceivables(s, customer.code);
    const total = rec.reduce((sum, r) => sum + r.open, 0);
    const overdue = rec.reduce((sum, r) => sum + (r.overdue > 0 ? r.open : 0), 0);
    const credit = Number(customer.credit_limit || 0);
    return {
      type: 'query_result',
      title: customer.name + ' · 应收概览',
      subtitle: '在库口径 · 账龄按出货日计算 · 离线演示',
      tag: '只读',
      kpis: [
        { label: '未结应收', value: moneyText(total), note: '共 ' + rec.length + ' 笔' },
        { label: '其中逾期', value: moneyText(overdue), note: '按出货日 + 30 天账期', tone: overdue ? 'warn' : '' },
        { label: '信用额度剩余', value: moneyText(credit - total), note: '授信 ' + moneyText(credit) },
      ],
      columns: [
        { key: 'order_no', label: '单号' }, { key: 'shipped_at', label: '出货日期' },
        { key: 'amount', label: '未结金额', align: 'right' }, { key: 'status', label: '状态' },
      ],
      rows: rec.map(r => ({
        order_no: r.order_no, shipped_at: r.shipped_at || '—', amount: moneyText(r.open),
        status: { text: r.overdue > 0 ? '逾期 ' + r.overdue + ' 天' : '未到期', tone: r.overdue > 0 ? 'high' : 'low' },
      })),
      footnote: '口径：未结 = 订单金额 − 已回款；账龄按出货日 + 30 天账期',
      actions: [{ label: '查全部订单', prompt: customer.name + ' 的订单和出货进度' }],
    };
  }

  function offlineInventoryCard(sku) {
    const s = offlineStore();
    const rows = s.inventory.filter(i => i.sku === sku.sku && offlineWarehouses().indexOf(i.warehouse) >= 0);
    const available = rows.reduce((sum, i) => sum + i.on_hand - i.locked + i.inbound_available, 0);
    const onHand = rows.reduce((sum, i) => sum + i.on_hand, 0);
    const locked = rows.reduce((sum, i) => sum + i.locked, 0);
    const inbound = rows.reduce((sum, i) => sum + i.inbound, 0);
    return {
      type: 'query_result',
      title: '库存 · ' + sku.sku + ' ' + sku.name,
      subtitle: '实时口径 · 可用 = 现存 − 锁定 + 可分配在途',
      tag: '只读',
      kpis: [
        { label: '可用库存', value: available.toLocaleString() + ' 件', note: '现存量 ' + onHand.toLocaleString() },
        { label: '锁定量', value: locked.toLocaleString() + ' 件', note: '已审核未出货占用' },
        { label: '在途', value: inbound.toLocaleString() + ' 件', note: '预计 3 天后到仓' },
      ],
      columns: [
        { key: 'warehouse', label: '仓库' }, { key: 'on_hand', label: '现存量', align: 'right' },
        { key: 'locked', label: '锁定量', align: 'right' }, { key: 'available', label: '可用量', align: 'right' },
      ],
      rows: rows.map(i => ({
        warehouse: i.warehouse_name, on_hand: i.on_hand.toLocaleString(),
        locked: i.locked.toLocaleString(), available: (i.on_hand - i.locked + i.inbound_available).toLocaleString(),
      })),
      footnote: '数据来自本地离线库；开单、入库、出货会实时改变这里的数字',
      actions: [],
    };
  }

  function offlineOrdersCard(customer) {
    const s = offlineStore();
    const rows = s.orders.filter(o => o.customer_code === customer.code).slice(0, 8);
    return {
      type: 'query_result',
      title: customer.name + ' · 最近订单',
      subtitle: '含状态与出货进度',
      tag: '只读',
      kpis: [
        { label: '订单数', value: String(rows.length), note: '最近 8 笔' },
        { label: '未出货金额', value: moneyText(rows.filter(o => ['APPROVED', 'PARTIAL'].indexOf(o.status) >= 0)
          .reduce((sum, o) => sum + Number(o.total_amount), 0)), note: '待发货' },
      ],
      columns: [
        { key: 'order_no', label: '单号' }, { key: 'delivery_date', label: '交期' },
        { key: 'amount', label: '金额', align: 'right' }, { key: 'status', label: '状态' },
      ],
      rows: rows.map(o => ({
        order_no: o.order_no, delivery_date: o.delivery_date, amount: moneyText(o.total_amount),
        status: { text: o.status_label, tone: ORDER_TONE[o.status] || 'ghost' },
      })),
      footnote: '数据来自本地离线库；状态由审核、出货、开票动作实时推进',
      actions: [],
    };
  }

  function offlineOverdueCard(days) {
    const s = offlineStore();
    const rows = [];
    s.customers.filter(c => offlineInScope(c.region)).forEach(c => {
      offlineReceivables(s, c.code).forEach(r => {
        if (r.overdue >= days) {
          rows.push({ order_no: r.order_no, customer: c.name, amount: moneyText(r.open),
            days: { text: r.overdue + ' 天', tone: 'high' } });
        }
      });
    });
    return {
      type: 'query_result', title: '逾期超过 ' + days + ' 天的应收',
      subtitle: '仅统计当前身份数据范围内客户', tag: '只读',
      kpis: [{ label: '笔数', value: String(rows.length), note: '按逾期天数排序' },
        { label: '合计', value: moneyText(rows.reduce((sum, r) => sum + Number(r.amount.replace(/[¥,]/g, '')), 0)), note: '未结金额' }],
      columns: [{ key: 'order_no', label: '单号' }, { key: 'customer', label: '客户' },
        { key: 'amount', label: '未结金额', align: 'right' }, { key: 'days', label: '逾期' }],
      rows: rows, footnote: '口径：出货日 + 30 天账期', actions: [],
    };
  }

  function offlineDraftResponse(text, customer, sku) {
    const s = offlineStore();
    const trace = offlineTraceId();
    if (!customer) {
      return offlineClarify('要开给哪家客户？', s.customers.filter(c => offlineInScope(c.region)).slice(0, 3).map(c => c.name), trace);
    }
    if (!sku) {
      return offlineClarify('要开哪个商品？', s.products.slice(0, 3).map(p => p.sku + ' ' + p.name), trace);
    }
    const qtyMatch = text.match(/(\d{1,6})\s*(件|台|个)/) || text.match(/数量\s*[:：]?\s*(\d{1,6})/);
    const qty = qtyMatch ? Number(qtyMatch[1]) : 0;
    if (!qty) return offlineClarify('数量是多少？例如「A12 200 件」。', [], trace);

    let price = Number(sku.list_price);
    const pm = text.match(/单价\s*[:：]?\s*(\d+(?:\.\d+)?)/);
    if (pm) price = Number(pm[1]);
    let warehouse = 'WH-SH';
    if (text.indexOf('杭州') >= 0) warehouse = 'WH-HZ';
    if (text.indexOf('南京') >= 0) warehouse = 'WH-NJ';

    let delivery;
    const dm = text.match(/(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]/);
    if (dm) {
      const d = new Date();
      delivery = d.getFullYear() + '-' + String(Number(dm[1])).padStart(2, '0') + '-' + String(Number(dm[2])).padStart(2, '0');
    } else {
      const d = new Date();
      d.setDate(d.getDate() + 7);
      delivery = d.toISOString().slice(0, 10);
    }

    const total = qty * price;
    const rec = offlineReceivables(s, customer.code);
    const outstanding = rec.reduce((sum, r) => sum + r.open, 0);
    const creditRemain = Number(customer.credit_limit || 0) - outstanding;
    const avail = offlineStock(s, sku.sku, warehouse);
    const checks = [
      { ok: creditRemain >= total, text: '客户信用额度剩余 ' + moneyText(creditRemain) + '（授信 ' + moneyText(Number(customer.credit_limit || 0)) + '）' },
      { ok: avail >= qty, text: sku.sku + ' 可用 ' + avail.toLocaleString() + ' 件 ≥ 需求 ' + qty.toLocaleString() + ' 件' },
    ];
    const dev = sku.list_price ? Math.abs(price - sku.list_price) / sku.list_price : 0;
    if (dev > 0.15) {
      checks.push({ ok: false, warn: true, text: '单价 ' + moneyText(price) + ' 偏离标准价 ' + Math.round(dev * 100) + '%，需勾选确认' });
    }
    const risk = offlineRisk(s, customer, total);
    const level = risk.high ? 'HIGH' : 'MEDIUM';
    const actionId = 'act-off-' + Math.random().toString(16).slice(2, 10);
    const items = [{ sku: sku.sku, name: sku.name, qty: qty, unit_price: price }];
    state.offlineDrafts[actionId] = {
      customer: { code: customer.code, name: customer.name },
      items: items, warehouse: warehouse, delivery: delivery, total: total, risk: level,
    };

    const threshold = (s.rules.find(r => r.rule_code === 'RISK-AMT-001') || {}).threshold || 50000;
    return {
      type: 'draft', action_id: actionId, draft_no: 'SO-DRAFT-OFF',
      draft: {
        customer: { code: customer.code, name: customer.name },
        items: items, total: total, delivery_date: delivery, warehouse: warehouse,
        checks: checks,
        risk: { level: level, reasons: risk.reasons },
      },
      fields: [
        { label: '客户', value: customer.name, note: customer.code },
        { label: '收货地址', value: customer.address || '—', note: '' },
        { label: '商品', value: sku.sku + ' ' + sku.name, note: '' },
        { label: '数量', value: qty + ' 件', note: '' },
        { label: '单价', value: moneyText(price), note: '含税 13%' },
        { label: '金额', value: moneyText(total), note: '', strong: true },
        { label: '交货日期', value: delivery, note: '' },
        { label: '发货仓', value: WH_NAME[warehouse], note: '可用 ' + avail.toLocaleString() },
      ],
      risk: {
        level: level,
        rule: risk.reasons[0] ? risk.reasons[0].rule : 'RISK-AMT-001',
        text: (level === 'HIGH' ? '高风险' : '中风险') + ' · 金额 ' + moneyText(total) +
          (level === 'HIGH' ? ' 超过阈值，需主管审批' : ' ≤ 阈值 ' + moneyText(Number(threshold))) +
          '｜策略：' + (level === 'HIGH' ? '提交主管审批' : '本人确认后提交'),
      },
      trace_id: trace,
      message: '已生成货单草案，' + checks.length + ' 项校验完成。金额 ' + moneyText(total) + '，判定为' +
        (level === 'HIGH' ? '高' : '中') + '风险。',
    };
  }

  function offlineChat(message) {
    const s = offlineStore();
    const text = String(message || '').trim();
    const trace = offlineTraceId();
    if (/(update\s+\w+\s+set|delete\s+from|insert\s+into|drop\s+table|直连数据库|直接改库)/i.test(text)) {
      return offlineBlocked('E_GUARD_001', '请求已被拒绝', '规则命中 · 危险意图识别',
        '系统不支持直接修改数据库或执行原生 SQL。所有写操作必须通过业务单据完成。',
        '这条指令包含直接改库意图，被安全策略拦截。',
        [{ ok: true, text: '身份校验', tail: state.employee.name },
          { ok: false, text: '危险意图', tail: '命中 BLOCK-SQL-001' }], trace);
    }

    const customer = offlineFindCustomer(text);
    const sku = offlineFindSku(text);

    if (customer && !offlineInScope(customer.region)) return offlineScopeBlocked(customer);
    if (!customer && text.indexOf('华南') >= 0) {
      const south = s.customers.find(c => c.region === 'SOUTH');
      if (south) return offlineScopeBlocked(south);
    }
    if (text.indexOf('导出') >= 0) {
      return offlineBlocked('E_PERM_002', '请求已被拒绝', '规则命中 · 工具授权',
        '批量导出需要数据管理员单独授权，当前角色未开通该能力。',
        '这个导出请求被工具授权校验拦下了。',
        [{ ok: true, text: '身份校验', tail: state.employee.name },
          { ok: false, text: '工具授权', tail: 'export.request 未授权' }], trace);
    }

    const createIntent = /开(一张|一个|个)?(货单|单)|下单|制单|做一张单/.test(text) ||
      (text.indexOf('开') >= 0 && sku && text.indexOf('开票') < 0);
    if (createIntent) return offlineDraftResponse(text, customer, sku);

    const cards = [];
    const texts = [];
    const wantsRec = /应收|欠款|账款|没结|未结|回款/.test(text);
    const wantsInv = /库存|能发|还有多少|余量|存货/.test(text) && (sku || text.indexOf('库存') >= 0);
    const wantsOrders = /订单|出货进度|发完/.test(text);

    if (wantsRec) {
      if (!customer) {
        return offlineClarify('要查哪家客户的应收？',
          s.customers.filter(c => offlineInScope(c.region)).slice(0, 3).map(c => c.name), trace);
      }
      const rec = offlineReceivables(s, customer.code);
      const total = rec.reduce((sum, r) => sum + r.open, 0);
      const overdue = rec.reduce((sum, r) => sum + (r.overdue > 0 ? r.open : 0), 0);
      cards.push(offlineReceivableCard(customer));
      texts.push(customer.name + ' 未结应收 ' + moneyText(total) + '，其中逾期 ' + moneyText(overdue) + '。');
    }
    if (wantsInv) {
      if (!sku) {
        return offlineClarify('要查哪个商品的库存？', s.products.slice(0, 3).map(p => p.sku + ' ' + p.name), trace);
      }
      cards.push(offlineInventoryCard(sku));
      texts.push(sku.sku + ' ' + sku.name + ' 可用 ' + offlineStock(s, sku.sku).toLocaleString() + ' 件。');
    }
    if (wantsOrders && customer) {
      cards.push(offlineOrdersCard(customer));
      texts.push('已列出 ' + customer.name + ' 最近订单与出货状态。');
    }
    if (text.indexOf('逾期') >= 0 && !customer) {
      const dm = text.match(/(\d{2,3})\s*天/);
      cards.push(offlineOverdueCard(dm ? Number(dm[1]) : 60));
      texts.push('按你的数据范围统计，逾期应收如下。');
    }

    if (cards.length) {
      offlineAppendTrace(s, trace, 'agent.query', '成功', 'TOOL_CALL', { message: text });
      offlineSave();
      return { type: 'answer', text: texts.join(''), cards: cards, trace_id: trace, intent: 'QUERY' };
    }
    return offlineClarify('可以告诉我：客户 + 商品 + 数量，例如「给华东精密开 A12 200 件」。',
      ['给华东精密开 A12 200 件', '华东精密还有多少应收', '查 A12 可用库存'], trace);
  }

  function offlineConfirmAction(actionId) {
    const draft = state.offlineDrafts[actionId];
    if (!draft) offlineFail('E_DRAFT_EXPIRED', '草案不存在或已过期，请重新生成');
    const res = offlineCreateOrder({
      customerCode: draft.customer.code,
      warehouse: draft.warehouse,
      deliveryDate: draft.delivery,
      items: draft.items.map(i => ({ sku: i.sku, qty: i.qty, unitPrice: i.unit_price })),
    });
    delete state.offlineDrafts[actionId];
    if (res.status === 'PENDING_APPROVAL') {
      return { type: 'pending_approval', approval_id: res.approval_id, trace_id: res.trace_id,
        text: '金额超过阈值，已提交主管审批；审批通过后自动落单。' };
    }
    return { type: 'receipt', order_no: res.order_no, status: 'APPROVED', amount: res.total,
      message: '货单已创建并锁定库存', trace_id: res.trace_id };
  }

  function offlineApi(path, opts) {
    state.offline = true;
    const method = (opts && opts.method) || 'GET';
    if (method === 'GET') return offlineRead(path, opts);
    return offlineWrite(new URL(path, 'http://offline.local'), method, (opts && opts.body) || {});
  }

  const get = path => api(path);
  const post = (path, body) => api(path, { method: 'POST', body: body || {} });
  const put = (path, body) => api(path, { method: 'PUT', body: body || {} });

  /* -------------------------------- helpers -------------------------------- */

  function toast(text, type) {
    const stack = $('#toasts');
    const el = document.createElement('div');
    el.className = 'toast' + (type === 'warn' ? ' warn' : '');
    el.innerHTML = '<svg class="i sm"><use href="#' + (type === 'warn' ? 'i-alert' : 'i-check') + '"/></svg><span>' + esc(text) + '</span>';
    stack.appendChild(el);
    setTimeout(() => {
      el.classList.add('out');
      setTimeout(() => el.remove(), 220);
    }, 2800);
  }

  function moneyText(n) {
    const v = Number(n || 0);
    return '¥' + v.toLocaleString('zh-CN', { maximumFractionDigits: 2 });
  }

  function timeLabel() {
    const d = new Date();
    return String(d.getHours()).padStart(2, '0') + ':' + String(d.getMinutes()).padStart(2, '0');
  }

  function scrollChat() {
    const sc = $('#chatScroll');
    if (!sc) return;
    sc.style.scrollBehavior = 'auto';
    sc.scrollTop = sc.scrollHeight;
    setTimeout(() => { sc.style.scrollBehavior = ''; }, 30);
  }

  function badgeFor(tone) {
    return { low: 'low', mid: 'mid', high: 'high', ghost: 'ghost', brand: 'brand' }[tone] || 'ghost';
  }

  function renderValue(v) {
    if (v && typeof v === 'object' && ('text' in v)) {
      return '<span class="tag ' + badgeFor(v.tone) + '">' + esc(v.text) + '</span>';
    }
    return esc(v);
  }

  /* --------------------------------- 路由 ---------------------------------- */

  function showView(view, push) {
    if (!ROUTES[view]) view = 'workbench';
    state.view = view;
    $$('.view').forEach(v => v.classList.add('hidden'));
    const el = document.getElementById('view-' + view);
    if (el) el.classList.remove('hidden');
    $$('.nav-item').forEach(n => n.classList.toggle('active', n.dataset.view === view));
    $('#pageTitle').textContent = ROUTES[view][0];
    $('#pageSub').textContent = ROUTES[view][1];
    if (push !== false) history.replaceState(null, '', '#/' + view);
    if (view === 'approvals') loadApprovals();
    if (view === 'audit') loadAudit();
    if (view === 'tools') loadTools();
    if (view === 'risk') loadRules();
    if (view === 'knowledge') loadKnowledge();
    if (view === 'integration') loadIntegration();
    if (view === 'orders') loadOrders();
    if (view === 'inventory') loadInventory();
    if (view === 'inbounds') loadInbounds();
    if (view === 'shipments') loadShipments();
    if (view === 'invoices') loadInvoices();
    if (view === 'master') loadMaster();
  }

  $$('.nav-item').forEach(item => {
    item.addEventListener('click', e => {
      e.preventDefault();
      showView(item.dataset.view);
    });
  });

  window.addEventListener('hashchange', () => {
    showView((location.hash || '#/workbench').replace('#/', '') || 'workbench', false);
  });

  /* --------------------------------- 启动 ---------------------------------- */

  async function waitForServer(attempts, delay) {
    for (let i = 0; i < attempts; i += 1) {
      try {
        const res = await fetch('/api/health', { cache: 'no-store' });
        if (res.ok) return true;
      } catch (e) { /* 还没起来，继续等 */ }
      await new Promise(resolve => setTimeout(resolve, delay));
    }
    return false;
  }

  let reconnectTimer = null;
  function startReconnectLoop() {
    if (reconnectTimer) return;
    reconnectTimer = setInterval(async () => {
      try {
        const res = await fetch('/api/health', { cache: 'no-store' });
        if (res.ok) {
          clearInterval(reconnectTimer);
          reconnectTimer = null;
          location.reload();
        }
      } catch (e) { /* 继续等待 */ }
    }, 3000);
  }

  async function boot() {
    if (state.demo) document.body.classList.add('no-anim');

    // 直接用 file:// 打开时，若本地服务已在运行就自动跳过去
    if (location.protocol === 'file:') {
      try {
        const res = await fetch('http://127.0.0.1:8765/api/health', { cache: 'no-store' });
        if (res.ok) { location.replace('http://127.0.0.1:8765/'); return; }
      } catch (e) { /* 服务未启动，走下面的重试 */ }
    }

    const online = await waitForServer(6, 400);
    if (!online) {
      enterOfflineMode();
      return;
    }
    $('#envState').textContent = '已连接';

    try {
      const accounts = await get('/api/auth/accounts');
      state.accounts = accounts.accounts || [];
    } catch (e) { state.accounts = []; }

    const asUser = new URLSearchParams(location.search).get('as');
    if (asUser) {
      const login = await post('/api/auth/login', { username: asUser });
      state.token = login.token;
      state.employee = login.employee;
      localStorage.setItem('erp_token', login.token);
      localStorage.setItem('erp_user', asUser);
    }

    if (!state.employee && state.token) {
      try {
        const me = await get('/api/me');
        state.employee = me.employee;
      } catch (e) {
        state.token = '';
      }
    }
    if (!state.employee) {
      const user = localStorage.getItem('erp_user') || 'zhangwei';
      const login = await post('/api/auth/login', { username: user });
      state.token = login.token;
      state.employee = login.employee;
      localStorage.setItem('erp_token', login.token);
      localStorage.setItem('erp_user', user);
    }

    await loadBootstrap();
    renderIdentity();
    buildIdentityMenu();
    showView(startView(), false);
    await initConversation();
    if (state.demo === 'approval') {
      setTimeout(() => {
        const target = (state.approvals || []).filter(a => a.can_approve)[0];
        if (target) openApproval(target.approval_id);
      }, 1000);
    }
    if (state.demo === 'new-order') setTimeout(() => { const b = $('#btnNewOrder'); if (b) b.click(); }, 700);
    if (state.demo === 'order') setTimeout(() => {
      const o = (state.erp.orders || [])[0];
      if (o) openOrderDrawer(o.order_no);
    }, 1000);
    if (state.demo === 'new-ship') setTimeout(() => openShipModal(), 800);
    if (state.demo === 'new-invoice') setTimeout(() => openInvoiceModal(), 1000);
    if (state.demo === 'new-inbound') setTimeout(() => { const b = $('#btnNewInbound'); if (b) b.click(); }, 700);
    if (state.demo === 'autoinbound') {
      setTimeout(() => { const b = $('#btnNewInbound'); if (b) b.click(); }, 700);
      setTimeout(() => { const s = $('#nfSubmit'); if (s) s.click(); }, 1600);
      setTimeout(loadInbounds, 2400);
      setTimeout(loadInventory, 2600);
    }
    if (state.demo === 'autocreate') {
      setTimeout(() => { const b = $('#btnNewOrder'); if (b) b.click(); }, 700);
      setTimeout(() => { const s = $('#ofSubmit'); if (s) s.click(); }, 1800);
      setTimeout(loadOrders, 2600);
    }
    if (state.demo === 'autoship') {
      setTimeout(() => openShipModal(), 800);
      setTimeout(() => { const s = $('#sfSubmit'); if (s) s.click(); }, 1800);
      setTimeout(loadShipments, 2600);
    }
    if (state.demo === 'autoinvoice') {
      setTimeout(() => openInvoiceModal(), 900);
      setTimeout(() => { const s = $('#ifSubmit'); if (s) s.click(); }, 1900);
      setTimeout(loadInvoices, 2700);
    }
    startTokenTimer();
  }

  async function loadBootstrap() {
    state.bootstrap = await get('/api/bootstrap');
    const counts = state.bootstrap.counts || {};
    const badge = $('#navBadge');
    const n = (counts.pending || 0) + (counts.mine_pending || 0);
    badge.textContent = n;
    badge.classList.toggle('hidden', !n);
    $('#railPending').textContent = counts.pending || 0;
    $('#railTools').textContent = (counts.tools || 0) + ' / 8';
    $('#statAudit').textContent = counts.audit || 0;
  }

  function renderIdentity() {
    const e = state.employee;
    if (!e) return;
    const color = AVATAR[e.role_code] || AVATAR.SALES_FOLLOWER;
    $('#topAvatar').textContent = e.initials;
    $('#topAvatar').style.background = color;
    $('#topName').textContent = e.name;
    $('#topRole').textContent = e.role_name + ' · ' + e.dept.split(' · ')[0];
    $('#topScope').textContent = e.scope_label.replace('区', '');

    $('#sideAvatar').textContent = e.initials;
    $('#sideAvatar').style.background = color;
    $('#sideName').textContent = e.name;
    $('#sideRole').textContent = e.role_name;

    $('#stripName').textContent = e.name;
    $('#stripScope').textContent = e.scope_label;
    $('#composerName').textContent = e.name;

    $('#railAvatar').textContent = e.initials;
    $('#railAvatar').style.background = color;
    $('#railName').textContent = e.name;
    $('#railRole').textContent = e.role_name + ' · ' + e.dept;
    $('#railScope').textContent = e.scope_label;

    const tags = $('#railTags');
    tags.innerHTML = e.scope.map(s => '<span class="mini-tag on">' +
      ({ EAST: '华东大区', SOUTH: '华南大区', NORTH: '华北大区' }[s] || s) + '</span>').join('') +
      '<span class="mini-tag">' + esc(e.role_name) + '</span>';

    const isSales = ['SALES_FOLLOWER', 'SALES_MANAGER'].indexOf(e.role_code) >= 0;
    const btn = $('#btnNewOrder');
    if (btn) btn.style.display = isSales ? '' : 'none';
  }

  function buildIdentityMenu() {
    const menu = $('#idMenu');
    menu.innerHTML = '<div class="id-menu-head">切换演示身份（真实权限不同）</div>' +
      state.accounts.map(a => {
        const active = state.employee && a.username === state.employee.username;
        return '<div class="id-menu-item' + (active ? ' active' : '') + '" data-user="' + esc(a.username) + '">' +
          '<div class="avatar sm" style="background:' + (AVATAR[a.role_code] || AVATAR.SALES_FOLLOWER) + '">' + esc(a.initials) + '</div>' +
          '<div><div class="name">' + esc(a.name) + '</div><div class="meta">' + esc(a.role_name) + ' · ' + esc(a.scope_label) + '</div></div>' +
          (active ? '<svg class="i sm check"><use href="#i-check"/></svg>' : '') +
          '</div>';
      }).join('');
  }

  $('#identityChip').addEventListener('click', e => {
    e.stopPropagation();
    $('#idMenu').classList.toggle('hidden');
  });

  document.addEventListener('click', e => {
    if (!e.target.closest('#idMenu') && !e.target.closest('#identityChip')) {
      $('#idMenu').classList.add('hidden');
    }
  });

  $('#idMenu').addEventListener('click', async e => {
    const item = e.target.closest('[data-user]');
    if (!item) return;
    const username = item.dataset.user;

    if (state.offline) {
      const acc = (state.accounts || []).find(a => a.username === username);
      if (!acc) return;
      state.employee = acc;
      localStorage.setItem('erp_offline_user', username);
      $('#idMenu').classList.add('hidden');
      renderIdentity();
      buildIdentityMenu();
      showView(state.view, false);
      toast('已切换为 ' + acc.name + '（' + acc.role_name + '）');
      return;
    }

    try {
      const login = await post('/api/auth/login', { username });
      localStorage.setItem('erp_token', login.token);
      localStorage.setItem('erp_user', username);
      location.reload();
    } catch (err) {
      toast(err.message, 'warn');
    }
  });

  const offlineResetLink = $('#offlineReset');
  if (offlineResetLink) {
    offlineResetLink.addEventListener('click', () => {
      if (window.confirm('重置会恢复内置样例数据，当前演示中的修改会丢失，确定继续？')) offlineReset();
    });
  }

  /* --------------------------------- 对话 ---------------------------------- */

  function startView() {
    const map = {
      approvals: 'approvals', approval: 'approvals', audit: 'audit',
      tools: 'tools', risk: 'risk', knowledge: 'knowledge', integration: 'integration',
      master: 'master',
      orders: 'orders', inventory: 'inventory', shipments: 'shipments', invoices: 'invoices',
      inbounds: 'inbounds', 'new-order': 'orders', order: 'orders', 'new-ship': 'shipments',
      'new-invoice': 'invoices', 'new-inbound': 'inbounds',
      autocreate: 'orders', autoship: 'shipments', autoinvoice: 'invoices', autoinbound: 'inbounds',
    };
    return map[state.demo] || (location.hash || '#/workbench').replace('#/', '') || 'workbench';
  }

  function enterOfflineMode() {
    state.offline = true;
    const demoStore = offlineStore();
    state.accounts = demoStore.accounts;
    const params = new URLSearchParams(location.search);
    const asUser = params.get('as');
    const saved = localStorage.getItem('erp_offline_user');
    state.employee = (asUser && demoStore.accounts.find(a => a.username === asUser)) ||
      demoStore.accounts.find(a => a.username === saved) ||
      demoStore.accounts.find(a => a.username === 'zhangwei') || demoStore.employee;
    state.bootstrap = offlineBootstrap();
    const bar = $('#offlineBar');
    if (bar) bar.classList.remove('hidden');
    $('#envState').textContent = '离线演示';
    if (!state.employee) {
      toast('离线数据未加载', 'warn');
      return;
    }
    renderIdentity();
    buildIdentityMenu();
    showView(startView(), false);
    welcome();
    renderQuick();
    const note = agentShell();
    $('.msg-text', note).innerHTML = '当前是<b>离线演示版</b>：数据保存在浏览器本地，' +
      '新增、修改、删除、审核、出货、开票都可以直接操作，刷新后保留；右上角可切换员工演示权限。';
    scrollChat();

    const counts = state.bootstrap.counts || {};
    $('#railTools').textContent = (counts.tools || 0) + ' / 8';
    $('#railPending').textContent = counts.pending || 0;
    $('#statAudit').textContent = counts.audit || 0;
    const badge = $('#navBadge');
    const badgeCount = (counts.pending || 0) + (counts.mine_pending || 0);
    badge.textContent = badgeCount;
    badge.classList.toggle('hidden', !badgeCount);

    const firstQ = (state.bootstrap.suggestions && state.bootstrap.suggestions[0]) ||
      '华东精密上个月还有多少应收没结？顺便看下 A12 的库存。';
    setTimeout(() => sendMessage(firstQ), 500);
    const demo = state.demo;
    if (demo === 'offline-approve') {
      try {
        offlineCreateOrder({
          customerCode: 'C10023', warehouse: 'WH-SH', deliveryDate: '2026-12-20',
          items: [{ sku: 'SKU-A12', qty: 600, unitPrice: 85 }],
        });
      } catch (e) { /* 忽略 */ }
      const manager = state.accounts.find(a => a.username === 'liming');
      if (manager) state.employee = manager;
      renderIdentity();
      buildIdentityMenu();
      showView('orders', false);
      setTimeout(() => {
        const o = (state.erp.orders || [])[0];
        if (o) openOrderDrawer(o.order_no);
      }, 900);
      setTimeout(() => {
        const btn = document.querySelector('[data-order-approve]');
        if (btn) btn.click();
      }, 1800);
      setTimeout(loadOrders, 2600);
      startTokenTimer();
      startReconnectLoop();
      return;
    }
    if (demo === 'new-order' || demo === 'autocreate') setTimeout(() => { const b = $('#btnNewOrder'); if (b) b.click(); }, 700);
    if (demo === 'new-ship' || demo === 'autoship') setTimeout(() => openShipModal(), 800);
    if (demo === 'new-invoice' || demo === 'autoinvoice') setTimeout(() => openInvoiceModal(), 900);
    if (demo === 'new-inbound' || demo === 'autoinbound') setTimeout(() => { const b = $('#btnNewInbound'); if (b) b.click(); }, 700);
    if (demo === 'autocreate') setTimeout(() => { const b = $('#ofSubmit'); if (b) b.click(); }, 1800);
    if (demo === 'autoship') setTimeout(() => { const b = $('#sfSubmit'); if (b) b.click(); }, 1800);
    if (demo === 'autoinvoice') setTimeout(() => { const b = $('#ifSubmit'); if (b) b.click(); }, 1900);
    if (demo === 'autoinbound') setTimeout(() => { const b = $('#nfSubmit'); if (b) b.click(); }, 1600);
    if (demo === 'order') setTimeout(() => {
      const o = (state.erp.orders || [])[0];
      if (o) openOrderDrawer(o.order_no);
    }, 1000);
    if (demo === 'approval') setTimeout(() => {
      const a = (state.approvals || [])[0];
      if (a) openApproval(a.approval_id);
    }, 1000);
    startTokenTimer();
    startReconnectLoop();
  }

  const thread = () => $('#thread');

  function agentShell() {
    const el = document.createElement('div');
    el.className = 'msg';
    el.innerHTML =
      '<div class="avatar agent"><svg class="i"><use href="#i-sparkle"/></svg></div>' +
      '<div class="msg-main">' +
      '<div class="msg-head"><span class="msg-name">ERP-Agent</span><span class="ai-pill">AI</span>' +
      '<span class="msg-time">' + timeLabel() + '</span></div>' +
      '<div class="msg-text"></div>' +
      '</div>';
    thread().appendChild(el);
    scrollChat();
    return el;
  }

  function appendUser(text) {
    const el = document.createElement('div');
    el.className = 'msg user';
    el.innerHTML =
      '<div class="avatar" style="background:' + (AVATAR[state.employee.role_code] || AVATAR.SALES_FOLLOWER) + '">' +
      esc(state.employee.initials) + '</div>' +
      '<div class="msg-main"><div class="msg-head"><span class="msg-name">' + esc(state.employee.name) +
      '</span><span class="msg-time">' + timeLabel() + '</span></div>' +
      '<div class="bubble">' + esc(text) + '</div></div>';
    thread().appendChild(el);
    scrollChat();
  }

  function welcome() {
    const suggestions = (state.bootstrap && state.bootstrap.suggestions) || [];
    const el = agentShell();
    $('.msg-text', el).innerHTML = '下午好，' + esc(state.employee.name) +
      '。我可以查客户、库存、订单和应收，也可以直接开货单；所有操作都按你的身份执行，并写入审计。';
    const row = document.createElement('div');
    row.className = 'suggest-row';
    row.innerHTML = suggestions.map(s =>
      '<button class="chip-btn js-prompt" data-prompt="' + esc(s) + '">' +
      '<svg class="i sm"><use href="#i-search"/></svg>' + esc(s.length > 18 ? s.slice(0, 18) + '…' : s) + '</button>'
    ).join('');
    $('.msg-main', el).appendChild(row);
  }

  function renderQuick() {
    const list = [
      ['查华东精密的应收与库存', '华东精密上个月还有多少应收没结？顺便看下 A12 的库存。'],
      ['给华东精密开一张货单', '给华东精密开一张货单，A12 200 件，单价 85，12 月 5 日前送到。'],
      ['查逾期超过 60 天', '哪些客户逾期超过 60 天？'],
      ['演示越权拦截', '把华南区客户 C2088 的订单全部导出来。'],
    ];
    $('#quickList').innerHTML = list.map(([label, prompt]) =>
      '<button class="quick-item js-prompt" data-prompt="' + esc(prompt) + '">' +
      '<svg class="i sm"><use href="#i-zap"/></svg>' + esc(label) +
      '<svg class="i sm arrow"><use href="#i-arrow-right"/></svg></button>').join('');
  }

  async function initConversation() {
    welcome();
    renderQuick();
    const demo = state.demo;
    if (['approvals', 'approval', 'audit', 'tools', 'risk', 'knowledge', 'integration', 'master',
      'orders', 'inventory', 'shipments', 'invoices', 'inbounds',
      'new-order', 'order', 'new-ship', 'new-invoice', 'new-inbound',
      'autocreate', 'autoship', 'autoinvoice', 'autoinbound'].indexOf(demo) >= 0) return;
    if (demo === 'draft' || demo === 'confirm' || demo === 'autoconfirm') {
      setTimeout(() => sendMessage('给华东精密开一张货单，A12 200 件，单价 85，12 月 5 日前送到。'), 300);
      if (demo === 'confirm') setTimeout(() => { const c = $('.draft-card'); if (c) openConfirm(c); }, 2200);
      if (demo === 'autoconfirm') {
        document.body.classList.add('no-anim');
        setTimeout(() => { const c = $('.draft-card'); if (c) openConfirm(c); }, 2000);
        setTimeout(() => {
          const cb = $('#confirmCheck');
          if (cb) { cb.checked = true; cb.dispatchEvent(new Event('change')); }
        }, 2400);
        setTimeout(() => { $('#modalConfirm').click(); }, 2800);
      }
      return;
    }
    if (demo === 'blocked') {
      setTimeout(() => sendMessage('把华南区客户 C2088 的订单全部导出来。'), 300);
      return;
    }
    const q = (state.bootstrap && state.bootstrap.suggestions && state.bootstrap.suggestions[0]) ||
      '华东精密上个月还有多少应收没结？顺便看下 A12 的库存。';
    setTimeout(() => sendMessage(q), 260);
  }

  function startTokenTimer() {
    let left = 30 * 60;
    setInterval(() => {
      left = Math.max(0, left - 1);
      const m = String(Math.floor(left / 60)).padStart(2, '0');
      const s = String(left % 60).padStart(2, '0');
      const el = $('#stripToken');
      if (el) el.textContent = m + ':' + s;
    }, 1000);
  }

  async function sendMessage(text) {
    const value = String(text || '').trim();
    if (!value || state.busy) return;
    if (state.offline) {
      appendUser(value);
      state.turns += 1;
      renderReply(offlineChat(value));
      updateStats();
      return;
    }
    state.busy = true;
    state.lastMessage = value;
    appendUser(value);
    const shell = agentShell();
    $('.msg-text', shell).innerHTML = '<span class="typing"><i></i><i></i><i></i></span>';
    scrollChat();

    try {
      const res = await post('/api/agent/chat', { message: value });
      shell.remove();
      state.turns += 1;
      state.calls += (res.cards && res.cards.length) || 1;
      renderReply(res);
      await refreshCounts();
    } catch (err) {
      shell.remove();
      renderReply({
        type: 'blocked', code: err.code || 'E_SYS_001', title: '请求失败',
        subtitle: '系统或网络异常', reason: err.message || '请稍后重试。',
        checks: [], trace_id: '',
      });
    } finally {
      state.busy = false;
      updateStats();
    }
  }

  function updateStats() {
    $('#statTurns').textContent = state.turns;
    $('#statCalls').textContent = state.calls;
  }

  async function refreshCounts() {
    try {
      const b = await get('/api/bootstrap');
      state.bootstrap = b;
      const counts = b.counts || {};
      const n = (counts.pending || 0) + (counts.mine_pending || 0);
      $('#navBadge').textContent = n;
      $('#navBadge').classList.toggle('hidden', !n);
      $('#railPending').textContent = counts.pending || 0;
      $('#statAudit').textContent = counts.audit || 0;
    } catch (e) { /* 忽略 */ }
  }

  function renderReply(res) {
    const el = agentShell();
    const body = $('.msg-text', el);
    const text = res.text || res.message || '';
    if (text) body.innerHTML = esc(text).replace(/\n/g, '<br>');

    const main = $('.msg-main', el);

    if (res.type === 'draft') {
      main.appendChild(renderDraft(res));
    } else if (res.type === 'blocked') {
      main.appendChild(renderBlocked(res));
    } else if (res.type === 'clarify') {
      const row = document.createElement('div');
      row.className = 'suggest-row';
      row.innerHTML = (res.options || []).map(o =>
        '<button class="chip-btn js-prompt" data-prompt="' + esc(o) + '">' +
        '<svg class="i sm"><use href="#i-arrow-right"/></svg>' + esc(o) + '</button>').join('');
      if (row.children.length) main.appendChild(row);
    } else if (res.type === 'receipt') {
      main.appendChild(renderReceipt(res));
    } else if (res.type === 'pending_approval') {
      main.appendChild(renderPending(res));
    } else if (res.cards) {
      res.cards.forEach(card => main.appendChild(renderCard(card)));
    }

    if (res.trace_id && res.type !== 'blocked' && res.type !== 'receipt') {
      const foot = document.createElement('div');
      foot.className = 'foot-note';
      foot.style.cssText = 'display:flex;gap:6px;align-items:center;margin-top:6px;font-size:10.5px;color:var(--ink-3)';
      foot.innerHTML = '<svg class="i sm" style="width:12px;height:12px"><use href="#i-activity"/></svg>' +
        'trace <span class="mono">' + esc(res.trace_id) + '</span> · 已写入审计链';
      main.appendChild(foot);
    }
    scrollChat();
    updateStats();
  }

  function kpiBlock(kpis) {
    if (!kpis || !kpis.length) return '';
    return '<div class="kpis">' + kpis.map(k =>
      '<div class="kpi"><div class="kpi-label">' + esc(k.label) + '</div>' +
      '<div class="kpi-value ' + (k.tone === 'warn' ? 'warn' : k.tone === 'danger' ? 'danger' : '') + '">' +
      esc(k.value) + '</div>' +
      '<div class="kpi-note">' + esc(k.note || '') + '</div></div>').join('') + '</div>';
  }

  function renderCard(card) {
    if (!card || !card.type) return document.createElement('div');
    if (card.type === 'query_result') return renderQuery(card);
    if (card.type === 'draft') return renderDraft(card);
    if (card.type === 'blocked') return renderBlocked(card);
    if (card.type === 'receipt') return renderReceipt(card);
    return document.createElement('div');
  }

  function renderQuery(card) {
    const el = document.createElement('div');
    el.className = 'card';
    const actions = (card.actions || []).map(a => {
      if (a.route) return '<button class="btn sm" data-route="' + esc(a.route) + '">' + esc(a.label) + '</button>';
      return '<button class="btn sm js-prompt" data-prompt="' + esc(a.prompt || '') + '">' + esc(a.label) + '</button>';
    }).join('');
    el.innerHTML =
      '<div class="card-head">' +
      '<div class="card-icon"><svg class="i"><use href="#i-database"/></svg></div>' +
      '<div class="card-titles"><div class="card-title">' + esc(card.title) + '</div>' +
      '<div class="card-sub">' + esc(card.subtitle || '') + '</div></div>' +
      '<div class="spacer"></div><span class="tag readonly">' + esc(card.tag || '只读') + '</span></div>' +
      kpiBlock(card.kpis) +
      '<table class="tbl"><thead><tr>' +
      (card.columns || []).map(c => '<th class="' + (c.align === 'right' ? 'num' : '') + '">' + esc(c.label) + '</th>').join('') +
      '</tr></thead><tbody>' +
      (card.rows || []).map(row => '<tr>' + (card.columns || []).map(c =>
        '<td class="' + (c.align === 'right' ? 'num' : '') + '">' + renderValue(row[c.key]) + '</td>').join('') + '</tr>').join('') +
      '</tbody></table>' +
      '<div class="card-foot"><span class="foot-note">' +
      '<svg class="i sm"><use href="#i-database"/></svg>' + esc(card.footnote || '') + '</span>' + actions + '</div>';
    return el;
  }

  function renderDraft(res) {
    const d = res.draft || {};
    const level = (res.risk && res.risk.level) || 'MEDIUM';
    const riskText = (res.risk && res.risk.text) || '';
    const fields = (res.fields || []).map(f =>
      '<div class="field"><div class="field-label">' + esc(f.label) + '</div>' +
      '<div class="field-value' + (f.strong ? ' num' : '') + '">' + esc(f.value) +
      (f.note ? ' <span class="unit">' + esc(f.note) + '</span>' : '') + '</div></div>').join('');
    const checks = (d.checks || []).map(c => {
      const warn = c.warn || c.ok === false;
      return '<div class="check' + (warn ? ' warn' : '') + '">' +
        '<svg class="i sm"><use href="#' + (warn ? 'i-alert' : 'i-check') + '"/></svg>' + esc(c.text) + '</div>';
    }).join('');

    const el = document.createElement('div');
    el.className = 'card draft-card';
    el.dataset.action = res.action_id;
    el.dataset.amount = d.total;
    el.dataset.customer = (d.customer && d.customer.name) || '';
    el.dataset.items = (d.items || []).map(i => i.sku + ' × ' + i.qty).join('，');
    el.dataset.delivery = d.delivery_date || '';
    el.dataset.warehouse = { 'WH-SH': '上海仓', 'WH-HZ': '杭州仓', 'WH-NJ': '南京仓', 'WH-GZ': '广州仓' }[d.warehouse] || d.warehouse || '';
    el.innerHTML =
      '<div class="card-head">' +
      '<div class="card-icon"><svg class="i"><use href="#i-file"/></svg></div>' +
      '<div class="card-titles"><div class="card-title">货单草案 · <span class="mono">' + esc(res.draft_no) + '</span></div>' +
      '<div class="card-sub">' + esc((d.customer && d.customer.name) || '') + ' · 生成于 ' + timeLabel() + '</div></div>' +
      '<div class="spacer"></div><span class="tag ' + (level === 'HIGH' ? 'high' : 'mid') + '">' +
      (level === 'HIGH' ? '高风险' : '中风险') + '</span></div>' +
      '<div class="fields">' + fields + '</div>' +
      '<div class="checks">' + checks + '</div>' +
      '<div class="riskbar' + (level === 'HIGH' ? ' high' : '') + '">' +
      '<svg class="i sm"><use href="#i-shield"/></svg>' + esc(riskText) +
      '<span class="mono muted" style="margin-left:auto;font-size:10px">' + esc((res.risk && res.risk.rule) || '') + '</span></div>' +
      '<div class="card-foot"><span class="foot-note"><svg class="i sm"><use href="#i-lock"/></svg>' +
      '确认后写入 ERP，生成正式单号并锁定库存</span>' +
      '<button class="btn sm js-regen">重新生成</button>' +
      '<button class="btn primary sm js-confirm" data-action="' + esc(res.action_id) + '">' +
      (level === 'HIGH' ? '提交审批' : '确认提交') + '</button></div>';
    return el;
  }

  function renderBlocked(res) {
    const el = document.createElement('div');
    el.className = 'card blocked';
    el.innerHTML =
      '<div class="card-head">' +
      '<div class="card-icon red"><svg class="i"><use href="#i-alert"/></svg></div>' +
      '<div class="card-titles"><div class="card-title">' + esc(res.title || '请求已被拒绝') + '</div>' +
      '<div class="card-sub">' + esc(res.subtitle || '') + '</div></div>' +
      '<div class="spacer"></div><span class="tag high">已拦截</span></div>' +
      '<div class="blocked-body">' +
      (res.code ? '<span class="blocked-code"><svg class="i sm"><use href="#i-lock"/></svg>' + esc(res.code) + '</span>' : '') +
      '<div class="blocked-text">' + esc(res.reason || '') + '</div>' +
      '<div class="blocked-checks">' + (res.checks || []).map(c =>
        '<div class="blocked-check' + (c.ok ? '' : ' bad') + '">' +
        '<svg class="i sm"><use href="#' + (c.ok ? 'i-check' : 'i-x') + '"/></svg>' + esc(c.text) +
        (c.tail ? '<span class="tail">' + esc(c.tail) + '</span>' : '') + '</div>').join('') +
      '</div></div>' +
      '<div class="card-foot"><span class="foot-note">' +
      (res.trace_id ? '<svg class="i sm"><use href="#i-activity"/></svg>已写入安全审计 · <span class="mono">' + esc(res.trace_id) + '</span>' : '') +
      '</span><button class="btn sm" data-route="#/audit">查看审计记录</button></div>';
    return el;
  }

  function renderReceipt(res) {
    const el = document.createElement('div');
    el.className = 'receipt';
    const no = res.order_no || res.invoice_no || res.shipment_no || '';
    el.innerHTML =
      '<div class="receipt-icon"><svg class="i"><use href="#i-check"/></svg></div>' +
      '<div><div class="receipt-title">' + esc(res.message || '操作已完成') +
      (no ? ' <span>' + esc(no) + '</span>' : '') + '</div>' +
      '<div class="receipt-sub">' + (res.status ? esc(res.status) + ' · ' : '') +
      'trace ' + esc(res.trace_id || '') + (res.idempotent ? ' · 幂等返回' : '') + '</div></div>' +
      '<div class="receipt-actions"><button class="btn sm" data-route="#/audit">回放轨迹</button></div>';
    return el;
  }

  function renderPending(res) {
    const el = document.createElement('div');
    el.className = 'pending-card';
    el.innerHTML =
      '<div class="ic"><svg class="i sm"><use href="#i-clock"/></svg></div>' +
      '<div><div class="t">已提交审批</div>' +
      '<div class="d">' + esc(res.text || '高风险动作需主管审批，通过后自动执行。') + '</div>' +
      '<div class="mono">' + esc(res.approval_id || '') + '</div></div>' +
      '<button class="btn sm" style="margin-left:auto" data-route="#/approvals">去审批</button>';
    return el;
  }

  /* ------------------------------- 确认提交 -------------------------------- */

  $('#modalCancel').addEventListener('click', closeConfirm);
  $('#modalBackdrop').addEventListener('click', e => {
    if (e.target === $('#modalBackdrop')) closeConfirm();
  });
  $('#confirmCheck').addEventListener('change', () => {
    $('#modalConfirm').disabled = !$('#confirmCheck').checked;
  });

  function openConfirm(card) {
    if (!card || card.dataset.confirmed === '1') return;
    state.pendingDraft = card;
    state.pendingAction = card.dataset.action;
    $('#modalSummary').innerHTML =
      '<div class="summary-row"><span class="k">客户</span><span class="v">' + esc(card.dataset.customer) + '</span></div>' +
      '<div class="summary-row"><span class="k">商品 / 数量</span><span class="v">' + esc(card.dataset.items) + '</span></div>' +
      '<div class="summary-row"><span class="k">金额</span><span class="v">' + moneyText(card.dataset.amount) + '</span></div>' +
      '<div class="summary-row"><span class="k">交期 / 仓库</span><span class="v">' + esc(card.dataset.delivery) + ' · ' + esc(card.dataset.warehouse) + '</span></div>';
    $('#confirmCheck').checked = false;
    $('#modalConfirm').disabled = true;
    $('#modalConfirm').textContent = card.querySelector('.js-confirm').textContent.trim();
    $('#modalBackdrop').classList.add('show');
  }

  function closeConfirm() {
    $('#modalBackdrop').classList.remove('show');
  }

  $('#modalConfirm').addEventListener('click', async () => {
    const card = state.pendingDraft;
    const actionId = state.pendingAction;
    if (!card || !actionId) return;
    $('#modalConfirm').disabled = true;
    $('#modalConfirm').textContent = '提交中…';
    try {
      const res = await post('/api/actions/' + actionId + '/confirm',
        { idempotencyKey: 'idem-' + Date.now() });
      closeConfirm();
      card.dataset.confirmed = '1';
      const foot = $('.card-foot', card);
      if (res.type === 'receipt') {
        foot.outerHTML =
          '<div class="confirmed"><svg class="i sm"><use href="#i-check"/></svg>' +
          '已确认并提交 · 单号 ' + esc(res.order_no) +
          '<span class="meta">' + timeLabel() + ' · ' + esc(state.employee.name) + ' · trace ' + esc(res.trace_id) + '</span></div>';
        card.insertAdjacentElement('afterend', renderReceipt(res));
        toast('货单 ' + res.order_no + ' 已创建，库存已锁定');
      } else if (res.type === 'pending_approval') {
        foot.outerHTML = '<div class="riskbar high"><svg class="i sm"><use href="#i-alert"/></svg>' +
          '已提交审批 · 审批通过后自动落单 <span class="mono" style="margin-left:auto">' + esc(res.approval_id) + '</span></div>';
        card.insertAdjacentElement('afterend', renderPending(res));
        toast('已提交审批，等待主管确认', 'warn');
      }
      await refreshCounts();
      scrollChat();
    } catch (err) {
      closeConfirm();
      toast(err.message || '提交失败', 'warn');
    } finally {
      $('#modalConfirm').textContent = '确认提交';
    }
  });

  /* -------------------------------- 事件委托 -------------------------------- */

  document.addEventListener('click', e => {
    const prompt = e.target.closest('.js-prompt');
    if (prompt) {
      e.preventDefault();
      sendMessage(prompt.dataset.prompt || '');
      return;
    }
    const route = e.target.closest('[data-route]');
    if (route) {
      e.preventDefault();
      showView((route.dataset.route || '').replace('#/', ''));
      return;
    }
    const confirm = e.target.closest('.js-confirm');
    if (confirm) {
      openConfirm(confirm.closest('.draft-card'));
      return;
    }
    if (e.target.closest('.js-regen')) {
      const card = e.target.closest('.draft-card');
      if (card) card.remove();
      if (state.lastMessage) sendMessage(state.lastMessage);
      return;
    }
    if (e.target.closest('[data-info="perm"]')) {
      openPermissionDrawer();
    }
  });

  /* ------------------------------- 审批中心 -------------------------------- */

  async function loadApprovals() {
    try {
      const data = await get('/api/approvals');
      state.approvals = data.items || [];
      renderApprovals();
    } catch (err) {
      $('#apBody').innerHTML = '<tr><td colspan="8"><div class="empty">加载失败：' + esc(err.message) + '</div></td></tr>';
    }
  }

  function renderApprovals() {
    const items = state.approvals;
    const todo = items.filter(i => i.can_approve);
    const mine = items.filter(i => i.mine);
    const done = items.filter(i => !i.mine && i.status !== 'PENDING');

    $('#apKpiPending').innerHTML = todo.length + ' <small>件</small>';
    $('#apKpiPendingNote').textContent = todo.length ? '最久等待 ' + Math.max.apply(null, todo.map(i => i.wait_minutes || 0)) + ' 分钟' : '暂无待办';
    $('#apKpiMine').innerHTML = mine.length + ' <small>件</small>';
    $('#apKpiMineNote').textContent = mine.filter(i => i.status === 'PENDING').length + ' 件待审批';
    $('#apKpiDone').innerHTML = done.length + ' <small>件</small>';
    $('#apKpiRole').textContent = state.employee.role_name;
    $('#apTodoCount').textContent = todo.length;

    let rows = state.approvalTab === 'todo' ? todo : state.approvalTab === 'mine' ? mine : done;
    $('#apCount').textContent = '共 ' + rows.length + ' 条';

    if (!rows.length) {
      $('#apBody').innerHTML = '<tr><td colspan="8"><div class="empty">' +
        (state.approvalTab === 'todo' ? '<b>当前没有待你审批的单据。</b><br>用右上角切换成“李明（销售主管）”或“陈立（财务总监）”，即可看到对应待办。' : '暂无记录。') +
        '</div></td></tr>';
    } else {
      $('#apBody').innerHTML = rows.map(a => {
        const status = a.status === 'PENDING'
          ? (a.can_approve ? '<button class="btn sm" data-approval="' + esc(a.approval_id) + '">审批</button>'
            : '<span class="tag mid">待审批</span>')
          : (a.status === 'APPROVED' ? '<span class="tag low">已通过</span>' : '<span class="tag high">已驳回</span>');
        return '<tr class="js-approval" data-approval="' + esc(a.approval_id) + '">' +
          '<td class="mono strong">' + esc(a.approval_id) + '</td>' +
          '<td>' + esc(a.type) + '</td>' +
          '<td><div class="who"><div class="avatar sm" style="background:var(--brand-050);color:var(--brand)">' +
          esc((a.applicant_name || '?')[0]) + '</div>' + esc(a.applicant_name || a.applicant) + '</div></td>' +
          '<td>' + esc(a.customer_name || '—') + '</td>' +
          '<td class="num">' + moneyText(a.amount) + '</td>' +
          '<td><span class="risk-dot high">高风险</span></td>' +
          '<td><span class="sla' + (a.wait_minutes > 30 ? ' warn' : '') + '">' +
          '<svg class="i"><use href="#i-clock"/></svg>' + (a.status === 'PENDING' ? (a.wait_minutes || 0) + ' 分钟' : '—') + '</span></td>' +
          '<td><div class="row-act">' + status + '</div></td></tr>';
      }).join('');
    }

    // 负载
    const load = {};
    items.filter(i => i.status === 'PENDING').forEach(i => {
      const k = i.applicant_name || i.applicant;
      load[k] = (load[k] || 0) + 1;
    });
    const max = Math.max.apply(null, Object.values(load).concat([1]));
    $('#apLoad').innerHTML = Object.keys(load).length
      ? Object.keys(load).map(k =>
        '<div class="bar-row"><div class="bar-top"><b>' + esc(k) + '</b><span class="n">' + load[k] + '</span></div>' +
        '<div class="bar"><i style="width:' + Math.round(load[k] / max * 100) + '%"></i></div></div>').join('')
      : '<div class="empty">暂无待处理</div>';

    // 规则命中
    const rules = {};
    items.filter(i => i.status === 'PENDING').forEach(i => {
      const k = i.rule_code || 'OTHER';
      rules[k] = (rules[k] || 0) + 1;
    });
    $('#apRules').innerHTML = Object.keys(rules).length
      ? Object.keys(rules).map(k =>
        '<div class="bar-row"><div class="bar-top"><b class="mono" style="font-size:10.5px">' + esc(k) + '</b>' +
        '<span class="n">' + rules[k] + '</span></div></div>').join('')
      : '<div class="empty">暂无命中</div>';
  }

  $$('#apTabs .tab').forEach(tab => {
    tab.addEventListener('click', () => {
      $$('#apTabs .tab').forEach(t => t.classList.remove('active'));
      tab.classList.add('active');
      state.approvalTab = tab.dataset.tab;
      renderApprovals();
    });
  });

  async function openApproval(id) {
    try {
      const a = await get('/api/approvals/' + id);
      state.selectedApproval = a;
      $('#drawerTitle').textContent = '审批 · ' + a.type;
      $('#drawerSub').textContent = a.approval_id + ' · ' + (a.customer_name || '');
      $('#drawerBody').innerHTML =
        '<div class="riskbar high" style="border:1px solid var(--red-100);border-radius:10px;margin-bottom:14px">' +
        '<svg class="i sm"><use href="#i-alert"/></svg><b>高风险</b> · ' + esc(a.reason || '') + '</div>' +
        '<div class="section-label">审批对象</div>' +
        '<div class="kv">' +
        '<span class="k">客户</span><span class="v">' + esc(a.customer_name) + '（' + esc(a.customer_code) + '）</span>' +
        '<span class="k">发起人</span><span class="v">' + esc(a.applicant_name) + '</span>' +
        '<span class="k">单据</span><span class="v mono">' + esc(a.approval_id) + '</span>' +
        (a.payload && a.payload.order_no
          ? '<span class="k">订单</span><span class="v mono">' + esc(a.payload.order_no) + '</span>' : '') +
        '<span class="k">金额</span><span class="v num">' + moneyText(a.amount) + '</span>' +
        '<span class="k">命中规则</span><span class="v mono">' + esc(a.rule_code || '—') + '</span>' +
        '<span class="k">状态</span><span class="v">' +
        esc({ PENDING: '待审批', APPROVED: '已通过', REJECTED: '已驳回' }[a.status] || a.status) + '</span>' +
        '</div>' +
        '<div class="section-label">风控依据</div>' +
        '<div class="mini-card">' + (a.checks || []).map(c =>
          '<div class="check" style="padding:3px 0"><svg class="i sm"><use href="#' +
          (c.ok ? 'i-check' : 'i-alert') + '"/></svg>' + esc(c.text) + '</div>').join('') + '</div>' +
        '<div class="section-label">审批意见</div>' +
        '<textarea class="select" id="approvalComment" style="width:100%;height:68px;padding:8px;resize:vertical" placeholder="驳回时必填，例如：请补充客户回签的退货单号"></textarea>';
      const can = a.status === 'PENDING' && a.can_approve;
      $('#drawerFoot').innerHTML = '<button class="btn" data-close-drawer>关闭</button><div class="spacer"></div>' +
        (can ? '<button class="btn danger" data-reject="' + esc(a.approval_id) + '">驳回</button>' +
          '<button class="btn primary" data-approve="' + esc(a.approval_id) + '">审批通过</button>'
          : '<span class="muted" style="font-size:11px">' + (a.status === 'PENDING' ? '当前身份无权审批此单' : '该审批已处理') + '</span>');
      openDrawer();
    } catch (err) {
      toast(err.message, 'warn');
    }
  }

  document.addEventListener('click', e => {
    const row = e.target.closest('.js-approval');
    if (row) {
      openApproval(row.dataset.approval);
      return;
    }
    const approve = e.target.closest('[data-approve]');
    if (approve) {
      decide(approve.dataset.approve, 'APPROVE');
      return;
    }
    const reject = e.target.closest('[data-reject]');
    if (reject) {
      decide(reject.dataset.reject, 'REJECT');
    }
  });

  async function decide(id, decision) {
    const comment = ($('#approvalComment') && $('#approvalComment').value) || '';
    if (decision === 'REJECT' && !comment.trim()) {
      toast('驳回需要填写审批意见', 'warn');
      return;
    }
    try {
      const res = await post('/api/approvals/' + id + '/decide', { decision, comment });
      closeDrawer();
      toast(res.message || '已处理');
      await loadApprovals();
      await refreshCounts();
      if (res.result && res.result.order_no) {
        toast('已生成货单 ' + res.result.order_no);
      }
    } catch (err) {
      toast(err.message || '审批失败', 'warn');
    }
  }

  /* ------------------------------ 传统 ERP --------------------------------- */

  const ORDER_TONE = {
    PENDING_APPROVAL: 'mid', APPROVED: 'brand', PARTIAL: 'mid',
    SHIPPED: 'low', INVOICED: 'ghost', REJECTED: 'high', DRAFT: 'ghost',
  };

  function userName(username) {
    const a = (state.accounts || []).find(x => x.username === username);
    return a ? a.name : (username || '—');
  }

  function fmtTime(value) {
    return value ? String(value).replace('T', ' ').slice(0, 16) : '—';
  }

  function todayLabel() {
    const d = new Date();
    const p = n => String(n).padStart(2, '0');
    return d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate());
  }

  function scopedWarehouses() {
    const all = [
      { code: 'WH-SH', name: '上海仓', region: 'EAST' },
      { code: 'WH-HZ', name: '杭州仓', region: 'EAST' },
      { code: 'WH-NJ', name: '南京仓', region: 'EAST' },
      { code: 'WH-GZ', name: '广州仓', region: 'SOUTH' },
    ];
    if (!state.employee) return all;
    if (state.employee.scope.indexOf('*') >= 0) return all;
    return all.filter(w => state.employee.scope.indexOf(w.region) >= 0);
  }

  async function ensureErpMaster() {
    if (!state.erp.customers.length) {
      const d = await get('/api/erp/customers');
      state.erp.customers = d.items || [];
    }
    if (!state.erp.products.length) {
      const d = await get('/api/erp/products');
      state.erp.products = d.items || [];
    }
  }

  async function loadOrders() {
    if (!state.employee) return;
    const params = new URLSearchParams();
    if (state.erp.orderStatus !== 'ALL') params.set('status', state.erp.orderStatus);
    if (state.erp.orderQ) params.set('q', state.erp.orderQ);
    try {
      const [data, ov] = await Promise.all([
        get('/api/erp/orders?' + params.toString()),
        get('/api/erp/overview'),
      ]);
      state.erp.orders = data.items || [];
      state.erp.overview = ov;
      renderOrders();
      $('#ordKpiTotal').textContent = ov.orders;
      $('#ordKpiTotalNote').textContent = ov.open_orders + ' 单未完结';
      $('#ordKpiAmount').textContent = moneyText(ov.open_amount);
      $('#ordKpiShip').textContent = ov.pending_ship;
      $('#ordKpiApprove').textContent = ov.pending_approval;
    } catch (err) {
      $('#ordBody').innerHTML = '<tr><td colspan="9"><div class="empty">' + esc(err.message) + '</div></td></tr>';
    }
  }

  function renderOrders() {
    const rows = state.erp.orders;
    $('#ordCount').textContent = '共 ' + rows.length + ' 条';
    if (!rows.length) {
      $('#ordBody').innerHTML = '<tr><td colspan="9"><div class="empty">没有符合条件的订单。点击「新建订单」用表单开一张。</div></td></tr>';
      return;
    }
    $('#ordBody').innerHTML = rows.map(o =>
      '<tr class="js-order" data-order="' + esc(o.order_no) + '">' +
      '<td class="mono strong">' + esc(o.order_no) + '</td>' +
      '<td>' + esc(o.customer_name) + '</td>' +
      '<td class="num">' + moneyText(o.total_amount) + '</td>' +
      '<td><span class="tag ' + (ORDER_TONE[o.status] || 'ghost') + '">' + esc(o.status_label) + '</span></td>' +
      '<td>' + esc(o.delivery_date || '—') + '</td>' +
      '<td>' + esc(o.warehouse_name || '—') + '</td>' +
      '<td>' + esc(userName(o.created_by)) + '</td>' +
      '<td class="muted">' + esc(fmtTime(o.created_at)) + '</td>' +
      '<td><div class="row-act"><button class="btn sm">查看</button></div></td></tr>').join('');
  }

  async function loadInventory() {
    if (!state.employee) return;
    try {
      const data = await get('/api/erp/inventory?q=' + encodeURIComponent(state.erp.invQ || ''));
      state.erp.inventory = data.items || [];
      renderInventory();
    } catch (err) {
      $('#invBody').innerHTML = '<tr><td colspan="8"><div class="empty">' + esc(err.message) + '</div></td></tr>';
    }
  }

  function renderInventory() {
    const rows = state.erp.inventory;
    $('#invKpiSku').textContent = new Set(rows.map(r => r.sku)).size;
    $('#invKpiOnHand').textContent = rows.reduce((s, r) => s + r.on_hand, 0).toLocaleString();
    $('#invKpiLocked').textContent = rows.reduce((s, r) => s + r.locked, 0).toLocaleString();
    $('#invKpiLow').textContent = rows.filter(r => r.stock_state === '低库存').length;
    $('#invCount').textContent = '共 ' + rows.length + ' 行';
    if (!rows.length) {
      $('#invBody').innerHTML = '<tr><td colspan="8"><div class="empty">没有匹配的库存记录。</div></td></tr>';
      return;
    }
    $('#invBody').innerHTML = rows.map(r =>
      '<tr><td class="mono strong">' + esc(r.sku) + '</td><td>' + esc(r.product_name) + '</td>' +
      '<td>' + esc(r.warehouse_name) + '</td>' +
      '<td class="num">' + r.on_hand.toLocaleString() + '</td>' +
      '<td class="num">' + r.locked.toLocaleString() + '</td>' +
      '<td class="num">' + r.inbound.toLocaleString() + '</td>' +
      '<td class="num"><b>' + r.available.toLocaleString() + '</b></td>' +
      '<td><span class="tag ' + (r.stock_state === '低库存' ? 'high' : 'low') + '">' + esc(r.stock_state) + '</span></td></tr>').join('');
  }

  async function loadInbounds() {
    if (!state.employee) return;
    try {
      const data = await get('/api/erp/inbounds');
      state.erp.inbounds = data.items || [];
      const rows = state.erp.inbounds;
      const month = todayLabel().slice(0, 7);
      $('#inKpiCount').textContent = rows.length;
      $('#inKpiQty').textContent = rows.reduce((s, r) => s + r.qty, 0).toLocaleString();
      $('#inKpiSupplier').textContent = new Set(rows.map(r => r.supplier)).size;
      $('#inKpiMonth').textContent = rows.filter(r => (r.created_at || '').slice(0, 7) === month).length;
      $('#inCount').textContent = '共 ' + rows.length + ' 条';
      if (!rows.length) {
        $('#inBody').innerHTML = '<tr><td colspan="8"><div class="empty">还没有入库单。点击「新建入库」登记一批到货。</div></td></tr>';
        return;
      }
      $('#inBody').innerHTML = rows.map(r =>
        '<tr><td class="mono strong">' + esc(r.inbound_no) + '</td>' +
        '<td>' + esc(r.supplier) + '</td>' +
        '<td class="mono">' + esc(r.sku) + '</td>' +
        '<td>' + esc(r.product_name || '') + '</td>' +
        '<td class="num">' + r.qty.toLocaleString() + '</td>' +
        '<td>' + esc(r.warehouse_name || r.warehouse) + '</td>' +
        '<td>' + esc(r.operator_name || userName(r.created_by)) + '</td>' +
        '<td class="muted">' + esc(fmtTime(r.created_at)) + '</td></tr>').join('');
    } catch (err) {
      $('#inBody').innerHTML = '<tr><td colspan="8"><div class="empty">' + esc(err.message) + '</div></td></tr>';
    }
  }

  $('#btnNewInbound').addEventListener('click', async () => {
    try { await ensureErpMaster(); } catch (e) { toast(e.message, 'warn'); return; }
    $('#nfSku').innerHTML = state.erp.products.map(p =>
      '<option value="' + esc(p.sku) + '">' + esc(p.sku) + ' ' + esc(p.name) + '</option>').join('');
    $('#nfWarehouse').innerHTML = scopedWarehouses().map(w =>
      '<option value="' + esc(w.code) + '">' + esc(w.name) + '</option>').join('');
    $('#nfQty').value = 100;
    openModal('inboundModal');
  });

  $('#nfSubmit').addEventListener('click', async () => {
    const qty = Number($('#nfQty').value);
    if (!qty || qty <= 0) { toast('入库数量必须大于 0', 'warn'); return; }
    $('#nfSubmit').disabled = true;
    try {
      const res = await post('/api/erp/inbounds', {
        supplier: $('#nfSupplier').value,
        sku: $('#nfSku').value,
        qty,
        warehouse: $('#nfWarehouse').value,
      });
      closeModal('inboundModal');
      toast('入库单 ' + res.inbound_no + ' 已完成，现存量 +' + res.qty);
      await loadInbounds();
      await loadInventory();
      await refreshCounts();
    } catch (err) {
      toast(err.message, 'warn');
    } finally {
      $('#nfSubmit').disabled = false;
    }
  });

  async function loadShipments() {
    if (!state.employee) return;
    try {
      const [data, ov] = await Promise.all([get('/api/erp/shipments'), get('/api/erp/overview')]);
      state.erp.shipments = data.items || [];
      const rows = state.erp.shipments;
      const today = todayLabel();
      $('#shKpiCount').textContent = rows.length;
      $('#shKpiQty').textContent = rows.reduce((s, r) => s + r.qty, 0).toLocaleString();
      $('#shKpiToday').textContent = rows.filter(r => (r.created_at || '').slice(0, 10) === today).length;
      $('#shKpiPending').textContent = ov.pending_ship;
      if (!rows.length) {
        $('#shBody').innerHTML = '<tr><td colspan="7"><div class="empty">还没有出货单。先在销售订单里审核一张单，再登记出货。</div></td></tr>';
        return;
      }
      $('#shBody').innerHTML = rows.map(r =>
        '<tr><td class="mono strong">' + esc(r.shipment_no) + '</td>' +
        '<td class="mono">' + esc(r.order_no) + '</td>' +
        '<td>' + esc(r.customer_name) + '</td>' +
        '<td class="num">' + r.qty.toLocaleString() + '</td>' +
        '<td>' + esc(r.warehouse_name || r.warehouse) + '</td>' +
        '<td>' + esc(userName(r.created_by)) + '</td>' +
        '<td class="muted">' + esc(fmtTime(r.created_at)) + '</td></tr>').join('');
    } catch (err) {
      $('#shBody').innerHTML = '<tr><td colspan="7"><div class="empty">' + esc(err.message) + '</div></td></tr>';
    }
  }

  async function loadInvoices() {
    if (!state.employee) return;
    try {
      const [data, shipped] = await Promise.all([
        get('/api/erp/invoices'),
        get('/api/erp/orders?status=SHIPPED'),
      ]);
      state.erp.invoices = data.items || [];
      state.erp.readyOrders = (shipped.items || []).filter(o => o.status === 'SHIPPED');
      const rows = state.erp.invoices;
      $('#ivKpiCount').textContent = rows.length;
      $('#ivKpiAmount').textContent = moneyText(rows.reduce((s, r) => s + r.amount, 0));
      $('#ivKpiReady').textContent = state.erp.readyOrders.length;
      $('#ivKpiRole').textContent = state.employee.role_name;
      $('#btnNewInvoice').style.display = ['FINANCE', 'FINANCE_DIRECTOR'].indexOf(state.employee.role_code) >= 0 ? '' : 'none';
      if (!rows.length) {
        $('#ivBody').innerHTML = '<tr><td colspan="8"><div class="empty">还没有发票记录。</div></td></tr>';
        return;
      }
      $('#ivBody').innerHTML = rows.map(r =>
        '<tr><td class="mono strong">' + esc(r.invoice_no) + '</td>' +
        '<td class="mono">' + esc(r.order_no) + '</td>' +
        '<td>' + esc(r.customer_name) + '</td>' +
        '<td class="num">' + moneyText(r.amount) + '</td>' +
        '<td class="mono">' + esc((r.tax_type || '').replace('VAT_', '')) + '%</td>' +
        '<td><span class="tag low">已开票</span></td>' +
        '<td>' + esc(userName(r.created_by)) + '</td>' +
        '<td class="muted">' + esc(fmtTime(r.created_at)) + '</td></tr>').join('');
    } catch (err) {
      $('#ivBody').innerHTML = '<tr><td colspan="8"><div class="empty">' + esc(err.message) + '</div></td></tr>';
    }
  }

  /* ------------------------------ 基础资料 CRUD ------------------------------ */

  const REGION_LABEL = { EAST: '华东', SOUTH: '华南', NORTH: '华北' };

  async function loadMaster() {
    if (!state.employee) return;
    try {
      const [c, p] = await Promise.all([get('/api/erp/customers'), get('/api/erp/products')]);
      state.master.customers = c.items || [];
      state.master.products = p.items || [];
      renderMaster();
    } catch (err) {
      $('#mdBody').innerHTML = '<tr><td colspan="8"><div class="empty">' + esc(err.message) + '</div></td></tr>';
    }
  }

  function renderMaster() {
    const isCust = state.master.tab === 'customers';
    const role = state.employee.role_code;
    const canManage = ['SALES_MANAGER', 'RISK_ADMIN'].indexOf(role) >= 0;
    $$('#mdTabs .tab').forEach(t => t.classList.toggle('active', t.dataset.md === state.master.tab));
    $('#mdAdd').textContent = isCust ? '+ 新增客户' : '+ 新增商品';
    $('#mdAdd').style.display = (!isCust && !canManage) ? 'none' : '';
    $('#mdSearch').placeholder = isCust ? '名称 / 编码' : 'SKU / 名称';

    const q = (state.master.q || '').trim();
    if (isCust) {
      const rows = state.master.customers.filter(c =>
        !q || c.name.indexOf(q) >= 0 || c.code.indexOf(q) >= 0);
      $('#mdHead').innerHTML =
        '<tr><th>编码</th><th>客户名称</th><th>区域</th><th>联系人</th><th>电话</th>' +
        '<th class="num">授信额度</th><th class="num">订单数</th><th></th></tr>';
      $('#mdBody').innerHTML = rows.length ? rows.map(c =>
        '<tr><td class="mono strong">' + esc(c.code) + '</td>' +
        '<td>' + esc(c.name) + '</td>' +
        '<td>' + esc(REGION_LABEL[c.region] || c.region) + '</td>' +
        '<td>' + esc(c.contact || '—') + '</td>' +
        '<td>' + esc(c.phone || '—') + '</td>' +
        '<td class="num">' + moneyText(c.credit_limit) + '</td>' +
        '<td class="num">' + (c.order_count || 0) + '</td>' +
        '<td><div class="row-act">' +
        '<button class="btn sm" data-edit-customer="' + esc(c.code) + '">修改</button>' +
        (canManage ? '<button class="btn sm danger" data-del-customer="' + esc(c.code) + '">删除</button>' : '') +
        '</div></td></tr>').join('')
        : '<tr><td colspan="8"><div class="empty">没有匹配的客户。</div></td></tr>';
      $('#mdCount').textContent = '共 ' + rows.length + ' 个客户';
      $('#mdKpiCustomers').textContent = state.master.customers.length;
      $('#mdKpiProducts').textContent = state.master.products.length;
      $('#mdKpiActive').textContent = state.master.customers.filter(c => (c.order_count || 0) > 0).length;
      $('#mdKpiCredit').textContent = moneyText(
        state.master.customers.reduce((s, c) => s + Number(c.credit_limit || 0), 0));
    } else {
      const rows = state.master.products.filter(p =>
        !q || p.name.indexOf(q) >= 0 || p.sku.indexOf(q) >= 0);
      $('#mdHead').innerHTML =
        '<tr><th>SKU</th><th>商品名称</th><th>分类</th><th>单位</th>' +
        '<th class="num">标准价</th><th>状态</th><th></th></tr>';
      $('#mdBody').innerHTML = rows.length ? rows.map(p =>
        '<tr><td class="mono strong">' + esc(p.sku) + '</td>' +
        '<td>' + esc(p.name) + '</td>' +
        '<td>' + esc(p.category || '—') + '</td>' +
        '<td>' + esc(p.unit || '件') + '</td>' +
        '<td class="num">' + moneyText(p.list_price) + '</td>' +
        '<td><span class="tag ' + (p.status === 'ACTIVE' ? 'low' : 'ghost') + '">' +
        (p.status === 'ACTIVE' ? '启用' : '停用') + '</span></td>' +
        '<td><div class="row-act">' +
        (canManage
          ? '<button class="btn sm" data-edit-product="' + esc(p.sku) + '">修改</button>' +
            '<button class="btn sm danger" data-del-product="' + esc(p.sku) + '">删除</button>'
          : '<span class="muted" style="font-size:10.5px">仅主管可维护</span>') +
        '</div></td></tr>').join('')
        : '<tr><td colspan="7"><div class="empty">没有匹配的商品。</div></td></tr>';
      $('#mdCount').textContent = '共 ' + rows.length + ' 个商品';
    }
  }

  $$('#mdTabs .tab').forEach(tab => {
    tab.addEventListener('click', () => {
      state.master.tab = tab.dataset.md;
      state.master.q = '';
      $('#mdSearch').value = '';
      renderMaster();
    });
  });

  let mdTimer = null;
  $('#mdSearch').addEventListener('input', e => {
    state.master.q = e.target.value;
    clearTimeout(mdTimer);
    mdTimer = setTimeout(renderMaster, 200);
  });

  let editingCustomer = null;
  let editingProduct = null;

  function openCustomerModal(code) {
    const c = code ? state.master.customers.find(x => x.code === code) : null;
    editingCustomer = code || null;
    $('#cmTitle').textContent = code ? '修改客户 · ' + code : '新增客户';
    $('#cmName').value = c ? c.name : '';
    $('#cmRegion').value = c ? c.region : 'EAST';
    $('#cmContact').value = c ? (c.contact || '') : '';
    $('#cmPhone').value = c ? (c.phone || '') : '';
    $('#cmAddress').value = c ? (c.address || '') : '';
    $('#cmCredit').value = c ? c.credit_limit : 100000;
    openModal('customerModal');
  }

  function openProductModal(sku) {
    const p = sku ? state.master.products.find(x => x.sku === sku) : null;
    editingProduct = sku || null;
    $('#pmTitle').textContent = sku ? '修改商品 · ' + sku : '新增商品';
    $('#pmName').value = p ? p.name : '';
    $('#pmCategory').value = p ? (p.category || '') : '';
    $('#pmUnit').value = p ? (p.unit || '件') : '件';
    $('#pmPrice').value = p ? p.list_price : '';
    openModal('productModal');
  }

  $('#mdAdd').addEventListener('click', () => {
    if (state.master.tab === 'customers') openCustomerModal();
    else openProductModal();
  });

  $('#cmSubmit').addEventListener('click', async () => {
    const body = {
      name: $('#cmName').value.trim(),
      region: $('#cmRegion').value,
      contact: $('#cmContact').value.trim(),
      phone: $('#cmPhone').value.trim(),
      address: $('#cmAddress').value.trim(),
      credit_limit: Number($('#cmCredit').value) || 0,
    };
    if (!body.name) { toast('请填写客户名称', 'warn'); return; }
    $('#cmSubmit').disabled = true;
    try {
      if (editingCustomer) await put('/api/erp/customers/' + editingCustomer, body);
      else await post('/api/erp/customers', body);
      closeModal('customerModal');
      toast(editingCustomer ? '客户已更新' : '客户已新增');
      await loadMaster();
      await ensureErpMaster();
    } catch (err) {
      toast(err.message, 'warn');
    } finally {
      $('#cmSubmit').disabled = false;
    }
  });

  $('#pmSubmit').addEventListener('click', async () => {
    const body = {
      name: $('#pmName').value.trim(),
      category: $('#pmCategory').value.trim(),
      unit: $('#pmUnit').value.trim() || '件',
      list_price: Number($('#pmPrice').value) || 0,
    };
    if (!body.name || body.list_price <= 0) { toast('请填写商品名称与标准价', 'warn'); return; }
    $('#pmSubmit').disabled = true;
    try {
      if (editingProduct) await put('/api/erp/products/' + editingProduct, body);
      else await post('/api/erp/products', body);
      closeModal('productModal');
      toast(editingProduct ? '商品已更新' : '商品已新增');
      await loadMaster();
      await ensureErpMaster();
    } catch (err) {
      toast(err.message, 'warn');
    } finally {
      $('#pmSubmit').disabled = false;
    }
  });

  document.addEventListener('click', async e => {
    const editC = e.target.closest('[data-edit-customer]');
    if (editC) { openCustomerModal(editC.dataset.editCustomer); return; }
    const delC = e.target.closest('[data-del-customer]');
    if (delC) {
      if (!window.confirm('确定删除客户 ' + delC.dataset.delCustomer + '？')) return;
      try {
        await api('/api/erp/customers/' + delC.dataset.delCustomer, { method: 'DELETE' });
        toast('客户已删除');
        await loadMaster();
        await ensureErpMaster();
      } catch (err) { toast(err.message, 'warn'); }
      return;
    }
    const editP = e.target.closest('[data-edit-product]');
    if (editP) { openProductModal(editP.dataset.editProduct); return; }
    const delP = e.target.closest('[data-del-product]');
    if (delP) {
      if (!window.confirm('确定删除商品 ' + delP.dataset.delProduct + '？')) return;
      try {
        await api('/api/erp/products/' + delP.dataset.delProduct, { method: 'DELETE' });
        toast('商品已删除');
        await loadMaster();
        await ensureErpMaster();
      } catch (err) { toast(err.message, 'warn'); }
    }
  });

  async function openOrderDrawer(orderNo) {
    try {
      const o = await get('/api/erp/orders/' + orderNo);
      $('#drawerTitle').textContent = '销售订单 ' + o.order_no;
      $('#drawerSub').textContent = o.customer_name + ' · ' + o.status_label;
      const items = (o.items || []).map(i =>
        '<tr><td class="mono">' + esc(i.sku) + '</td><td>' + esc(i.product_name || '') + '</td>' +
        '<td class="num">' + i.qty + '</td><td class="num">' + moneyText(i.unit_price) + '</td>' +
        '<td class="num">' + moneyText(i.qty * i.unit_price) + '</td></tr>').join('');
      const ships = (o.shipments || []).map(s =>
        '<div class="mini-card" style="font-size:11.5px">' + esc(s.shipment_no) + ' · 数量 ' + s.qty +
        ' · ' + esc(fmtTime(s.created_at)) + '</div>').join('');
      const invoices = (o.invoices || []).map(v =>
        '<div class="mini-card" style="font-size:11.5px">' + esc(v.invoice_no) + ' · ' + moneyText(v.amount) +
        ' · ' + esc(fmtTime(v.created_at)) + '</div>').join('');
      const events = (o.events || []).slice(-6).map(ev =>
        '<div class="safe-item" style="padding:4px 0"><svg class="i sm"><use href="#i-activity"/></svg>' +
        '<span>' + esc(eventTitle(ev.event_type)) + ' · ' + esc(fmtTime(ev.created_at)) + '</span></div>').join('');

      $('#drawerBody').innerHTML =
        '<div class="section-label">订单信息</div>' +
        '<div class="kv">' +
        '<span class="k">客户</span><span class="v">' + esc(o.customer_name) + '（' + esc(o.customer_code) + '）</span>' +
        '<span class="k">金额</span><span class="v num">' + moneyText(o.total_amount) + '</span>' +
        '<span class="k">状态</span><span class="v"><span class="tag ' + (ORDER_TONE[o.status] || 'ghost') + '">' + esc(o.status_label) + '</span></span>' +
        '<span class="k">交期</span><span class="v">' + esc(o.delivery_date || '—') + '</span>' +
        '<span class="k">发货仓</span><span class="v">' + esc(o.warehouse_name || '—') + '</span>' +
        '<span class="k">创建人</span><span class="v">' + esc(userName(o.created_by)) + '</span>' +
        '<span class="k">创建时间</span><span class="v">' + esc(fmtTime(o.created_at)) + '</span>' +
        '</div>' +
        '<div class="section-label">商品明细</div>' +
        '<table class="tbl" style="border:1px solid var(--line);border-radius:8px;overflow:hidden">' +
        '<thead><tr><th>SKU</th><th>品名</th><th class="num">数量</th><th class="num">单价</th><th class="num">小计</th></tr></thead>' +
        '<tbody>' + items + '</tbody></table>' +
        (ships ? '<div class="section-label">出货记录</div>' + ships : '') +
        (invoices ? '<div class="section-label">发票记录</div>' + invoices : '') +
        (events ? '<div class="section-label">最近动作</div>' + events : '');

      const role = state.employee.role_code;
      const canApprove = ['SALES_MANAGER', 'RISK_ADMIN'].indexOf(role) >= 0;
      const canEdit = ['SALES_FOLLOWER', 'SALES_MANAGER', 'RISK_ADMIN'].indexOf(role) >= 0;
      let actions = '<button class="btn" data-close-drawer>关闭</button><div class="spacer"></div>';
      if (o.status === 'PENDING_APPROVAL') {
        if (canEdit) actions += '<button class="btn" data-order-edit="' + esc(o.order_no) + '">修改</button>';
        actions += '<button class="btn danger" data-order-delete="' + esc(o.order_no) + '">删除</button>';
        if (canApprove) {
          actions += '<button class="btn danger" data-order-reject="' + esc(o.order_no) + '">驳回</button>';
          actions += '<button class="btn primary" data-order-approve="' + esc(o.order_no) + '">审核通过</button>';
        }
      } else if (o.status === 'REJECTED') {
        actions += '<button class="btn danger" data-order-delete="' + esc(o.order_no) + '">删除</button>';
      }
      if (o.status === 'APPROVED' || o.status === 'PARTIAL') {
        actions += '<button class="btn" data-order-cancel="' + esc(o.order_no) + '">取消订单</button>';
        actions += '<button class="btn primary" data-ship-order="' + esc(o.order_no) + '">登记出货</button>';
      }
      if (o.status === 'SHIPPED' && ['FINANCE', 'FINANCE_DIRECTOR'].indexOf(role) >= 0) {
        actions += '<button class="btn primary" data-invoice-order="' + esc(o.order_no) + '">开具发票</button>';
      }
      $('#drawerFoot').innerHTML = actions;
      openDrawer();
    } catch (err) {
      toast(err.message, 'warn');
    }
  }

  async function decideOrder(no, decision, comment) {
    try {
      await post('/api/erp/orders/' + no + '/approve', { decision, comment: comment || '' });
      toast(decision === 'APPROVE' ? '订单 ' + no + ' 已审核通过' : '订单 ' + no + ' 已驳回',
        decision === 'APPROVE' ? '' : 'warn');
      closeDrawer();
      await loadOrders();
      await refreshCounts();
    } catch (err) {
      toast(err.message, 'warn');
    }
  }

  async function removeOrder(no) {
    if (!window.confirm('确定删除订单 ' + no + '？')) return;
    try {
      await api('/api/erp/orders/' + no, { method: 'DELETE' });
      toast('订单已删除');
      closeDrawer();
      await loadOrders();
      await refreshCounts();
    } catch (err) {
      toast(err.message, 'warn');
    }
  }

  async function cancelOrderAction(no) {
    if (!window.confirm('取消订单会释放已锁定库存，确定继续？')) return;
    try {
      await post('/api/erp/orders/' + no + '/cancel', {});
      toast('订单已取消，库存已释放');
      closeDrawer();
      await loadOrders();
      await loadInventory();
      await refreshCounts();
    } catch (err) {
      toast(err.message, 'warn');
    }
  }

  function openModal(id) { $('#' + id).classList.add('show'); }
  function closeModal(id) { $('#' + id).classList.remove('show'); }

  let editingOrderNo = null;

  async function openOrderModal(orderNo) {
    try { await ensureErpMaster(); } catch (e) { toast(e.message, 'warn'); return; }
    let detail = null;
    if (orderNo) {
      try { detail = await get('/api/erp/orders/' + orderNo); }
      catch (e) { toast(e.message, 'warn'); return; }
    }
    editingOrderNo = orderNo || null;
    $('#orderModalTitle').textContent = orderNo ? '修改销售订单 · ' + orderNo : '新建销售订单';
    $('#ofSubmit').textContent = orderNo ? '保存修改' : '提交订单';

    const cust = $('#ofCustomer');
    cust.innerHTML = state.erp.customers.map(c =>
      '<option value="' + esc(c.code) + '">' + esc(c.name) + '（' + esc(c.code) + '）</option>').join('');
    const wh = $('#ofWarehouse');
    wh.innerHTML = scopedWarehouses().map(w =>
      '<option value="' + esc(w.code) + '">' + esc(w.name) + '</option>').join('');

    if (detail) {
      cust.value = detail.customer_code;
      wh.value = detail.warehouse;
      $('#ofDate').value = detail.delivery_date || '';
      $('#ofNote').value = detail.note || '';
      $('#ofLines').innerHTML = '';
      (detail.items || []).forEach(it => addOrderLine(it.sku, it.qty, it.unit_price));
      if (!(detail.items || []).length) addOrderLine();
    } else {
      const d = new Date(); d.setDate(d.getDate() + 7);
      $('#ofDate').value = d.toISOString().slice(0, 10);
      $('#ofNote').value = '';
      $('#ofLines').innerHTML = '';
      addOrderLine(state.erp.products[0] && state.erp.products[0].sku,
        1, state.erp.products[0] && state.erp.products[0].list_price);
    }
    openModal('orderModal');
  }

  $('#btnNewOrder').addEventListener('click', () => openOrderModal());

  function addOrderLine(sku, qty, price) {
    const wrap = $('#ofLines');
    const opts = state.erp.products.map(p =>
      '<option value="' + esc(p.sku) + '"' + (p.sku === sku ? ' selected' : '') + '>' +
      esc(p.sku) + ' ' + esc(p.name) + '</option>').join('');
    const row = document.createElement('div');
    row.className = 'li-row';
    row.innerHTML =
      '<select class="input li-sku">' + opts + '</select>' +
      '<input class="input li-qty" type="number" min="1" value="' + (qty || 1) + '" placeholder="数量">' +
      '<input class="input li-price" type="number" min="0" step="0.01" value="' + (price || 0) + '" placeholder="单价">' +
      '<button class="li-remove" title="删除">×</button>';
    wrap.appendChild(row);
    const sel = row.querySelector('.li-sku');
    sel.addEventListener('change', () => {
      const p = state.erp.products.find(x => x.sku === sel.value);
      if (p) row.querySelector('.li-price').value = p.list_price;
      updateOrderTotal();
    });
    row.querySelectorAll('input').forEach(inp => inp.addEventListener('input', updateOrderTotal));
    row.querySelector('.li-remove').addEventListener('click', () => {
      if (wrap.children.length > 1) row.remove();
      updateOrderTotal();
    });
    updateOrderTotal();
  }

  function updateOrderTotal() {
    const total = $$('#ofLines .li-row').reduce((sum, row) => {
      const qty = Number(row.querySelector('.li-qty').value) || 0;
      const price = Number(row.querySelector('.li-price').value) || 0;
      return sum + qty * price;
    }, 0);
    $('#ofTotal').textContent = moneyText(total);
  }

  $('#ofAddLine').addEventListener('click', () => addOrderLine());

  $('#ofSubmit').addEventListener('click', async () => {
    const items = $$('#ofLines .li-row').map(row => ({
      sku: row.querySelector('.li-sku').value,
      qty: Number(row.querySelector('.li-qty').value),
      unitPrice: Number(row.querySelector('.li-price').value),
    })).filter(i => i.sku && i.qty > 0);
    if (!items.length) { toast('至少填写一行有效商品', 'warn'); return; }
    $('#ofSubmit').disabled = true;
    try {
      const payload = {
        customerCode: $('#ofCustomer').value,
        warehouse: $('#ofWarehouse').value,
        deliveryDate: $('#ofDate').value,
        note: $('#ofNote').value,
        items,
      };
      if (editingOrderNo) {
        await put('/api/erp/orders/' + editingOrderNo, payload);
        toast('订单 ' + editingOrderNo + ' 已更新');
      } else {
        const res = await post('/api/erp/orders', payload);
        toast(res.status === 'PENDING_APPROVAL'
          ? '订单 ' + res.order_no + ' 已提交主管审批'
          : '订单 ' + res.order_no + ' 已创建并审核', res.status === 'PENDING_APPROVAL' ? 'warn' : '');
      }
      editingOrderNo = null;
      closeModal('orderModal');
      await loadOrders();
      await refreshCounts();
    } catch (err) {
      toast(err.message, 'warn');
    } finally {
      $('#ofSubmit').disabled = false;
    }
  });

  async function openShipModal(orderNo) {
    try {
      const data = await get('/api/erp/orders?status=OPEN');
      const rows = (data.items || []).filter(o => o.status === 'APPROVED' || o.status === 'PARTIAL');
      if (!rows.length) { toast('没有可出货的已审核货单', 'warn'); return; }
      state.erp.shipOrders = rows;
      const sel = $('#sfOrder');
      sel.innerHTML = rows.map(o =>
        '<option value="' + esc(o.order_no) + '">' + esc(o.order_no) + ' · ' + esc(o.customer_name) +
        ' · ' + moneyText(o.total_amount) + '</option>').join('');
      if (orderNo) sel.value = orderNo;
      const fill = () => {
        const o = rows.find(x => x.order_no === sel.value) || rows[0];
        $('#sfInfo').innerHTML =
          '<span class="k">客户</span><span class="v">' + esc(o.customer_name) + '</span>' +
          '<span class="k">待出数量</span><span class="v">' + (o.total_qty - o.shipped_qty) + ' 件</span>' +
          '<span class="k">发货仓</span><span class="v">' + esc(o.warehouse_name || o.warehouse) + '</span>';
      };
      sel.onchange = fill;
      fill();
      openModal('shipModal');
    } catch (err) {
      toast(err.message, 'warn');
    }
  }

  $('#btnNewShip').addEventListener('click', () => openShipModal());

  $('#sfSubmit').addEventListener('click', async () => {
    const orderNo = $('#sfOrder').value;
    if (!orderNo) return;
    $('#sfSubmit').disabled = true;
    try {
      const res = await post('/api/erp/orders/' + orderNo + '/ship', {});
      closeModal('shipModal');
      toast('已出货 ' + res.shipment_no + ' · ' + res.qty + ' 件');
      await loadShipments();
      await loadOrders();
      await loadInventory();
      await refreshCounts();
    } catch (err) {
      toast(err.message, 'warn');
    } finally {
      $('#sfSubmit').disabled = false;
    }
  });

  async function openInvoiceModal(orderNo) {
    try {
      await loadInvoices();
      const rows = state.erp.readyOrders || [];
      if (!rows.length) { toast('没有已出货待开票的货单', 'warn'); return; }
      state.erp.invoiceOrders = rows;
      const sel = $('#ifOrder');
      sel.innerHTML = rows.map(o =>
        '<option value="' + esc(o.order_no) + '">' + esc(o.order_no) + ' · ' + esc(o.customer_name) +
        ' · ' + moneyText(o.total_amount) + '</option>').join('');
      if (orderNo) sel.value = orderNo;
      const fill = () => {
        const o = rows.find(x => x.order_no === sel.value) || rows[0];
        $('#ifAmount').value = o.total_amount;
      };
      sel.onchange = fill;
      fill();
      openModal('invoiceModal');
    } catch (err) {
      toast(err.message, 'warn');
    }
  }

  $('#btnNewInvoice').addEventListener('click', () => openInvoiceModal());

  $('#ifSubmit').addEventListener('click', async () => {
    const orderNo = $('#ifOrder').value;
    const amount = Number($('#ifAmount').value);
    if (!orderNo || !amount) { toast('请填写开票金额', 'warn'); return; }
    $('#ifSubmit').disabled = true;
    try {
      const res = await post('/api/erp/invoices', {
        orderNo, amount, taxType: $('#ifTax').value,
      });
      closeModal('invoiceModal');
      toast('发票 ' + res.invoice_no + ' 已开具');
      await loadInvoices();
      await loadOrders();
      await refreshCounts();
    } catch (err) {
      toast(err.message, 'warn');
    } finally {
      $('#ifSubmit').disabled = false;
    }
  });

  document.addEventListener('click', e => {
    const close = e.target.closest('[data-close-modal]');
    if (close) {
      closeModal(close.dataset.closeModal);
      return;
    }
    const editO = e.target.closest('[data-order-edit]');
    if (editO) {
      closeDrawer();
      openOrderModal(editO.dataset.orderEdit);
      return;
    }
    const approveO = e.target.closest('[data-order-approve]');
    if (approveO) {
      decideOrder(approveO.dataset.orderApprove, 'APPROVE');
      return;
    }
    const rejectO = e.target.closest('[data-order-reject]');
    if (rejectO) {
      const comment = window.prompt('请输入驳回意见', '价格或库存需复核');
      if (comment === null) return;
      decideOrder(rejectO.dataset.orderReject, 'REJECT', comment);
      return;
    }
    const deleteO = e.target.closest('[data-order-delete]');
    if (deleteO) {
      removeOrder(deleteO.dataset.orderDelete);
      return;
    }
    const cancelO = e.target.closest('[data-order-cancel]');
    if (cancelO) {
      cancelOrderAction(cancelO.dataset.orderCancel);
      return;
    }
    const ship = e.target.closest('[data-ship-order]');
    if (ship) {
      closeDrawer();
      openShipModal(ship.dataset.shipOrder);
      return;
    }
    const invoice = e.target.closest('[data-invoice-order]');
    if (invoice) {
      closeDrawer();
      openInvoiceModal(invoice.dataset.invoiceOrder);
      return;
    }
    const row = e.target.closest('.js-order');
    if (row) {
      openOrderDrawer(row.dataset.order);
    }
  });

  $$('#ordTabs .tab').forEach(tab => {
    tab.addEventListener('click', () => {
      $$('#ordTabs .tab').forEach(t => t.classList.remove('active'));
      tab.classList.add('active');
      state.erp.orderStatus = tab.dataset.status;
      loadOrders();
    });
  });

  let orderSearchTimer = null;
  $('#ordSearch').addEventListener('input', e => {
    state.erp.orderQ = e.target.value.trim();
    clearTimeout(orderSearchTimer);
    orderSearchTimer = setTimeout(loadOrders, 300);
  });

  let invSearchTimer = null;
  $('#invSearch').addEventListener('input', e => {
    state.erp.invQ = e.target.value.trim();
    clearTimeout(invSearchTimer);
    invSearchTimer = setTimeout(loadInventory, 300);
  });

  $$('.modal-backdrop').forEach(bd => {
    bd.addEventListener('click', e => {
      if (e.target === bd) bd.classList.remove('show');
    });
  });

  /* --------------------------------- 审计 ---------------------------------- */

  async function loadAudit() {
    try {
      const data = await get('/api/audit?limit=30');
      state.traces = data.items || [];
      populateAuditFilters();
      renderAudit();
      const blocked = state.traces.filter(t => t.status === 'BLOCKED' || t.result === '已拦截');
      $('#blockedBody').innerHTML = blocked.length ? blocked.map(t =>
        '<tr><td class="mono">' + esc(t.date.slice(5) + ' ' + t.time) + '</td><td>' + esc(t.employee) + '</td>' +
        '<td class="mono">' + esc(t.tool) + '</td><td><span class="tag high">已拦截</span></td>' +
        '<td class="muted">' + esc(t.code || '规则拒绝') + '</td><td class="mono muted">' + esc(t.trace_id) + '</td></tr>').join('')
        : '<tr><td colspan="6"><div class="empty">暂无拦截记录。可在工作台输入“把华南区客户 C2088 的订单全部导出来”制造一条。</div></td></tr>';
      if (state.traces.length) {
        const visible = filteredTraces();
        selectTrace((visible[0] || state.traces[0]).trace_id);
      }
    } catch (err) {
      $('#auditBody').innerHTML = '<tr><td colspan="6"><div class="empty">加载失败：' + esc(err.message) + '</div></td></tr>';
    }
  }

  function populateAuditFilters() {
    const fill = (sel, values, allLabel) => {
      const el = $(sel);
      if (!el) return;
      const current = el.value;
      el.innerHTML = '<option value="all">' + allLabel + '</option>' +
        values.map(v => '<option value="' + esc(v) + '">' + esc(v) + '</option>').join('');
      el.value = values.indexOf(current) >= 0 ? current : 'all';
    };
    fill('#auditEmp', Array.from(new Set(state.traces.map(t => t.employee))).sort(), '全部员工');
    fill('#auditTool', Array.from(new Set(state.traces.map(t => t.tool))).sort(), '全部工具');
  }

  function filteredTraces() {
    const f = state.auditFilter;
    return state.traces.filter(t => {
      if (f.tab === 'success' && t.result !== '成功') return false;
      if (f.tab === 'blocked' && t.result !== '已拦截') return false;
      if (f.emp !== 'all' && t.employee !== f.emp) return false;
      if (f.tool !== 'all' && t.tool !== f.tool) return false;
      if (f.result !== 'all' && t.result !== f.result) return false;
      return true;
    });
  }

  function renderAudit() {
    const body = $('#auditBody');
    const rows = filteredTraces();
    if (!rows.length) {
      body.innerHTML = '<tr><td colspan="6"><div class="empty">没有符合条件的记录。</div></td></tr>';
      return;
    }
    body.innerHTML = rows.map(t => {
      const tone = t.result === '成功' ? 'low' : (t.result === '已拦截' || t.result === '已驳回') ? 'high' : 'mid';
      const riskTone = t.risk === 'HIGH' ? 'high' : t.risk === 'LOW' ? 'low' : '';
      const riskLabel = { HIGH: '高', MEDIUM: '中', LOW: '低' }[t.risk] || '中';
      return '<tr class="js-trace' + (state.selectedTrace === t.trace_id ? ' selected' : '') + '" data-trace="' + esc(t.trace_id) + '">' +
        '<td class="mono">' + esc(t.date.slice(5) + ' ' + t.time) + '</td><td>' + esc(t.employee) + '</td>' +
        '<td class="mono">' + esc(t.tool) + '</td>' +
        '<td><span class="tag ' + tone + '">' + esc(t.result) + '</span></td>' +
        '<td><span class="risk-dot ' + riskTone + '">' + riskLabel + '</span></td>' +
        '<td class="mono muted">' + esc(t.trace_id) + '</td></tr>';
    }).join('');
  }

  $$('#auditTabs .tab').forEach(tab => {
    tab.addEventListener('click', () => {
      $$('#auditTabs .tab').forEach(t => t.classList.remove('active'));
      tab.classList.add('active');
      state.auditFilter.tab = tab.dataset.filter;
      renderAudit();
      const rows = filteredTraces();
      if (rows.length) selectTrace(rows[0].trace_id);
    });
  });

  ['auditEmp', 'auditTool', 'auditResult'].forEach(id => {
    const el = document.getElementById(id);
    if (!el) return;
    el.addEventListener('change', () => {
      if (id === 'auditEmp') state.auditFilter.emp = el.value;
      if (id === 'auditTool') state.auditFilter.tool = el.value;
      if (id === 'auditResult') state.auditFilter.result = el.value;
      renderAudit();
      const rows = filteredTraces();
      if (rows.length) selectTrace(rows[0].trace_id);
    });
  });

  async function selectTrace(traceId) {
    state.selectedTrace = traceId;
    $$('#auditBody .js-trace').forEach(tr => tr.classList.toggle('selected', tr.dataset.trace === traceId));
    $('#traceIdLabel').textContent = traceId;
    try {
      const data = await get('/api/traces/' + traceId);
      const meta = data.meta || {};
      $('#traceEmp').textContent = meta.employee || '—';
      $('#traceTool').textContent = meta.tool || '—';
      $('#traceCost').textContent = meta.total || '—';
      const badge = $('#traceHashBadge');
      badge.className = 'hash-badge';
      badge.style.color = data.hash_ok ? '' : 'var(--red)';
      badge.innerHTML = '<svg class="i sm"><use href="#' + (data.hash_ok ? 'i-shield-check' : 'i-alert') + '"/></svg>' +
        (data.hash_ok ? '哈希链完整' : '校验失败');
      $('#traceTimeline').innerHTML = (data.events || []).map((ev, idx) => {
        const cls = ev.type === 'BLOCKED' || ev.type === 'CHECK_FAILED' ? 'bad'
          : (ev.type.indexOf('RISK') === 0 ? 'warn' : 'ok');
        return '<div class="tl-item ' + cls + '"><span class="tl-dot"></span><div class="tl-body">' +
          '<div class="tl-top"><span class="tl-title">' + esc(eventTitle(ev.type)) + '</span>' +
          '<span class="tl-time">' + esc(ev.time) + '</span></div>' +
          '<div class="tl-meta">' + esc(eventMeta(ev)) + '</div>' +
          (ev.payload && Object.keys(ev.payload).length
            ? '<div class="mono-block">' + esc(JSON.stringify(ev.payload).slice(0, 180)) + '</div>' : '') +
          '</div></div>';
      }).join('');
    } catch (err) {
      $('#traceTimeline').innerHTML = '<div class="empty">' + esc(err.message) + '</div>';
    }
  }

  const EVENT_TITLE = {
    INTENT: '意图识别', TOOL_CALL: '工具调用', DRAFT_CREATED: '草案生成',
    RISK_LOW: '风控判定 · 低', RISK_MEDIUM: '风控判定 · 中', RISK_HIGH: '风控判定 · 高',
    CONFIRM: '本人确认', APPROVAL_CREATED: '生成审批', APPROVAL_APPROVED: '审批通过',
    APPROVAL_REJECTED: '审批驳回', ORDER_CREATED: '业务落单', STOCK_LOCKED: '库存锁定',
    SHIPMENT_CREATED: '出货登记', INVOICE_ISSUED: '开票完成', BLOCKED: '拦截判定',
    CHECK_FAILED: '校验失败', ORDER_APPROVED: '订单审核', ORDER_SHIPPED: '订单出货',
  };

  function eventTitle(type) { return EVENT_TITLE[type] || type; }

  function eventMeta(ev) {
    const p = ev.payload || {};
    if (p.message) return '输入：' + p.message;
    if (p.draft_no) return '草案：' + p.draft_no + (p.total ? ' · ¥' + Number(p.total).toLocaleString() : '');
    if (p.tool) return '工具：' + p.tool + (p.customer ? ' · ' + p.customer : '') + (p.sku ? ' · ' + p.sku : '');
    if (p.order_no) return '单号：' + p.order_no + (p.total ? ' · ¥' + Number(p.total).toLocaleString() : '');
    if (p.code) return '错误码：' + p.code;
    if (p.reason) return '原因：' + (typeof p.reason === 'string' ? p.reason : JSON.stringify(p.reason));
    if (p.by) return '操作人：' + p.by;
    if (p.available !== undefined) return '可用量：' + p.available;
    return '已记录';
  }

  document.addEventListener('click', e => {
    const row = e.target.closest('#auditBody .js-trace');
    if (row) selectTrace(row.dataset.trace);
  });

  /* --------------------------------- 工具 ---------------------------------- */

  async function loadTools() {
    try {
      const data = await get('/api/tools');
      state.tools = data.items || [];
      const enabled = state.tools.filter(t => t.enabled).length;
      $('#toolsSummary').textContent = '共 ' + state.tools.length + ' 个工具 · 运行中 ' + enabled + ' 个';
      $('#toolsBody').innerHTML = state.tools.map(t =>
        '<tr class="js-tool" data-tool="' + esc(t.name) + '">' +
        '<td class="mono strong">' + esc(t.name) + '</td><td>' + esc(t.description) + '</td>' +
        '<td><span class="tag ' + (t.risk === 'HIGH' ? 'high' : t.risk === 'MEDIUM' ? 'mid' : 'low') + '">' +
        ({ HIGH: '高', MEDIUM: '中', LOW: '低' }[t.risk] || t.risk) + '</span></td>' +
        '<td>' + esc((t.roles || []).map(r => ({ '*': '全员', SALES_FOLLOWER: '销售跟单', SALES_MANAGER: '销售主管', FINANCE: '财务', WAREHOUSE: '仓储' }[r] || r)).join(' / ')) + '</td>' +
        '<td class="mono">' + esc(t.version) + '</td><td class="num">' + t.calls + '</td>' +
        '<td><button class="switch' + (t.enabled ? ' on' : '') + '" data-tool-toggle="' + esc(t.name) + '"></button></td></tr>').join('');
      if (state.tools.length) renderSchema(state.tools[0]);
      $$('#toolsBody .js-tool').forEach(row => {
        row.addEventListener('click', e => {
          if (e.target.closest('.switch')) return;
          $$('#toolsBody .js-tool').forEach(r => r.classList.remove('selected'));
          row.classList.add('selected');
          const tool = state.tools.find(t => t.name === row.dataset.tool);
          if (tool) renderSchema(tool);
        });
      });
    } catch (err) {
      $('#toolsBody').innerHTML = '<tr><td colspan="7"><div class="empty">' + esc(err.message) + '</div></td></tr>';
    }
  }

  function renderSchema(tool) {
    $('#schemaName').textContent = tool.name;
    $('#schemaVer').textContent = 'v' + tool.version;
    const tag = $('#schemaRisk');
    tag.className = 'tag ' + (tool.risk === 'HIGH' ? 'high' : tool.risk === 'MEDIUM' ? 'mid' : 'low');
    tag.textContent = ({ HIGH: '高风险', MEDIUM: '中风险', LOW: '低风险' }[tool.risk] || tool.risk);
    $('#schemaCode').innerHTML = highlightJSON(tool.schema || {});
  }

  function highlightJSON(obj) {
    const json = esc(JSON.stringify(obj, null, 2));
    return json
      .replace(/&quot;([^&]+?)&quot;:/g, '<span class="k">"$1"</span>:')
      .replace(/: &quot;([^&]*)&quot;/g, ': <span class="s">"$1"</span>')
      .replace(/: (true|false|null|-?\d+\.?\d*)/g, ': <span class="n">$1</span>');
  }

  document.addEventListener('click', async e => {
    const sw = e.target.closest('[data-tool-toggle]');
    if (!sw) return;
    e.stopPropagation();
    const name = sw.dataset.toolToggle;
    const enabled = !sw.classList.contains('on');
    try {
      await put('/api/tools/' + name, { enabled });
      sw.classList.toggle('on', enabled);
      toast(name + (enabled ? ' 已启用' : ' 已停用'), enabled ? '' : 'warn');
      await loadBootstrap();
    } catch (err) {
      toast(err.message, 'warn');
    }
  });

  /* --------------------------------- 风控 ---------------------------------- */

  async function loadRules() {
    try {
      const data = await get('/api/risk-rules');
      state.rules = data.items || [];
      $('#rulesList').innerHTML = state.rules.map(r => {
        const level = r.level === 'HIGH' ? 'high' : r.level === 'MEDIUM' ? 'mid' : 'low';
        return '<div class="rule-row">' +
          '<span class="rule-code">' + esc(r.rule_code) + '</span>' +
          '<div class="rule-main"><div class="rule-title">' + esc(r.title) + '</div>' +
          '<div class="rule-cond">' + esc(r.condition_text) + '</div></div>' +
          '<span class="tag ' + level + '">' + esc({ HIGH: '高风险', MEDIUM: '警告', LOW: '低' }[r.level] || r.level) + '</span>' +
          '<span class="rule-policy">' + esc(r.policy) + '</span>' +
          '<button class="switch' + (r.enabled ? ' on' : '') + '" data-rule-toggle="' + esc(r.rule_code) + '"></button></div>';
      }).join('');
      const amt = state.rules.find(r => r.rule_code === 'RISK-AMT-001');
      if (amt && amt.threshold) {
        $('#thresholdRange').value = amt.threshold;
        $('#thresholdLabel').textContent = '¥' + Number(amt.threshold).toLocaleString();
      }
    } catch (err) {
      $('#rulesList').innerHTML = '<div class="empty">' + esc(err.message) + '</div>';
    }
  }

  document.addEventListener('click', async e => {
    const sw = e.target.closest('[data-rule-toggle]');
    if (!sw) return;
    const code = sw.dataset.ruleToggle;
    const enabled = !sw.classList.contains('on');
    try {
      await put('/api/risk-rules/' + code, { enabled });
      sw.classList.toggle('on', enabled);
      toast(code + (enabled ? ' 已启用' : ' 已停用'), enabled ? '' : 'warn');
    } catch (err) {
      toast(err.message, 'warn');
    }
  });

  const range = $('#thresholdRange');
  if (range) {
    range.addEventListener('input', () => {
      $('#thresholdLabel').textContent = '¥' + Number(range.value).toLocaleString();
    });
    range.addEventListener('change', async () => {
      try {
        await put('/api/risk-rules/RISK-AMT-001', { enabled: true, threshold: Number(range.value) });
        toast('阈值已保存：¥' + Number(range.value).toLocaleString() + '，下一动作生效');
      } catch (err) {
        toast(err.message, 'warn');
      }
    });
  }

  $$('.js-range').forEach(r => {
    const label = r.closest('.slider-row').querySelector('.v');
    const fmt = v => r.dataset.format === 'pct' ? v + '%' : v + ' 小时';
    r.addEventListener('input', () => { label.textContent = fmt(r.value); });
    r.addEventListener('change', () => toast('已更新：' + fmt(r.value) + '（演示参数）'));
  });

  /* -------------------------------- 知识库 --------------------------------- */

  async function loadKnowledge() {
    try {
      const data = await get('/api/knowledge');
      state.knowledge = data.items || [];
      $('#kbCount').textContent = '知识条目 ' + state.knowledge.length + ' · 生效中';
      renderKnowledge();
    } catch (err) {
      $('#kbList').innerHTML = '<div class="empty">' + esc(err.message) + '</div>';
    }
  }

  function renderKnowledge() {
    const items = state.knowledge.filter(k => state.kbCat === '全部' || k.category === state.kbCat);
    $('#kbList').innerHTML = items.length ? items.map(k =>
      '<div class="kb-item"><div class="kb-head">' +
      '<span class="tag ' + (k.category === '指标' ? 'brand' : k.category === '规则' ? 'mid' : 'ghost') + '">' + esc(k.category) + '</span>' +
      '<span class="kb-title">' + esc(k.title) + '</span>' +
      '<span class="tag low" style="margin-left:auto">生效中</span></div>' +
      '<div class="kb-body">' + esc(k.content) + '</div>' +
      '<div class="kb-meta"><span>v' + k.version + '</span><span>维护人 ' + esc(k.updated_by) +
      '</span><span>更新 ' + esc((k.updated_at || '').slice(0, 10)) + '</span>' +
      '<span class="mono">' + esc(k.metric_key) + '</span></div></div>').join('')
      : '<div class="empty">该分类暂无条目。</div>';
  }

  $$('#kbTabs .tab').forEach(tab => {
    tab.addEventListener('click', () => {
      $$('#kbTabs .tab').forEach(t => t.classList.remove('active'));
      tab.classList.add('active');
      state.kbCat = tab.dataset.cat;
      renderKnowledge();
    });
  });

  $('#kbSearch').addEventListener('click', () => {
    const q = $('#kbQuery').value.trim();
    if (!q) return;
    const hits = state.knowledge.filter(k =>
      (k.title && k.title.indexOf(q) >= 0) || (k.content && k.content.indexOf(q) >= 0) ||
      (k.metric_key && q.indexOf(k.metric_key) >= 0) ||
      (/库存|能发|A12/.test(q) && k.metric_key === 'available_stock') ||
      (/账龄|逾期|应收/.test(q) && k.metric_key === 'ar_aging') ||
      (/状态|出货|开票/.test(q) && k.metric_key === 'order_state'));
    const hit = hits[0] || state.knowledge[0];
    $('#kbHit').innerHTML = hit
      ? '<span class="score">match</span><b>' + esc(hit.title) + '</b> · ' + esc(hit.metric_key) + '<br>' + esc(hit.content.slice(0, 64)) + '…'
      : '没有命中条目。';
    toast('检索完成，命中 ' + (hit ? 1 : 0) + ' 条');
  });

  /* ------------------------------ 外部 ERP 接入 ------------------------------ */

  async function loadIntegration() {
    if (!state.employee) return;
    try {
      const data = await get('/api/integration');
      state.integration = data;
      renderIntegration(data);
    } catch (err) {
      toast(err.message, 'warn');
    }
  }

  function renderIntegration(d) {
    const enabled = !!d.enabled;
    $('#igSource').textContent = '内置 ERP';
    $('#igSourceNote').textContent = enabled ? '外部接入已启用 · 演示仍读内置数据' : '本地 SQLite · 默认数据源';

    const statusMap = {
      UNCONFIGURED: ['未配置', '尚未填写外部地址'],
      CONFIGURED: ['已配置', '尚未测试连接'],
      CONNECTED: ['已连接', '最近一次测试通过'],
      FAILED: ['连接失败', '请检查地址与网络'],
      DISABLED: ['已停用', '地址已保存但未启用'],
    };
    const st = statusMap[d.status] || ['未知', ''];
    $('#igStatus').textContent = st[0];
    $('#igStatusNote').textContent = d.last_test_message ? String(d.last_test_message).slice(0, 42) : st[1];

    $('#igEnabledTag').textContent = enabled ? '已启用' : '未启用';
    $('#igEnabledTag').className = 'tag ' + (enabled ? 'low' : 'ghost');
    $('#igEnabled').classList.toggle('on', enabled);

    $('#igVendor').value = d.vendor || '';
    $('#igBaseUrl').value = d.base_url || '';
    $('#igAuthType').value = d.auth_type || 'Bearer';
    $('#igToken').value = '';
    $('#igToken').placeholder = d.credential_set ? '已配置（留空不修改）' : '留空表示不修改';

    $('#igLastTest').textContent = d.last_test_at ? String(d.last_test_at).replace('T', ' ').slice(5, 16) : '—';
    $('#igLatency').textContent = d.last_test_message ? String(d.last_test_message).slice(0, 40) : '—';
    $('#igMappingCount').textContent = Object.keys(d.endpoints || {}).length;
    $('#igSavedHint').textContent = d.updated_at ? '最近更新 ' + String(d.updated_at).replace('T', ' ').slice(5, 16) : '';

    $$('.map-input').forEach(inp => {
      inp.value = (d.endpoints || {})[inp.dataset.ep] || '';
    });
  }

  $('#igEnabled').addEventListener('click', () => {
    $('#igEnabled').classList.toggle('on');
  });

  $('#igSave').addEventListener('click', async () => {
    const endpoints = {};
    $$('.map-input').forEach(inp => { endpoints[inp.dataset.ep] = inp.value.trim(); });
    const body = {
      vendor: $('#igVendor').value,
      base_url: $('#igBaseUrl').value.trim(),
      auth_type: $('#igAuthType').value,
      credential: $('#igToken').value,
      endpoints,
      enabled: $('#igEnabled').classList.contains('on'),
    };
    try {
      const data = await put('/api/integration', body);
      renderIntegration(data);
      toast('配置已保存');
    } catch (err) {
      toast(err.message, 'warn');
    }
  });

  $('#igTest').addEventListener('click', async () => {
    const box = $('#igTestResult');
    box.className = 'test-result';
    box.classList.remove('hidden');
    box.textContent = '正在测试连接…';
    try {
      const res = await post('/api/integration/test', {});
      box.className = 'test-result ' + (res.ok ? 'ok' : 'fail');
      box.innerHTML = (res.ok ? '连接成功' : '连接失败') + ' · ' + esc(res.message || '') +
        (res.target ? '<br><span class="muted">' + esc(res.target) + '</span>' : '');
      await loadIntegration();
    } catch (err) {
      box.className = 'test-result fail';
      box.textContent = err.message;
    }
  });

  $('#igReset').addEventListener('click', async () => {
    try {
      const data = await put('/api/integration', {
        vendor: '', base_url: '', enabled: false, clear_credential: true,
      });
      renderIntegration(data);
      $('#igTestResult').classList.add('hidden');
      toast('已恢复内置 ERP 数据源');
    } catch (err) {
      toast(err.message, 'warn');
    }
  });

  /* --------------------------------- 抽屉 ---------------------------------- */

  function openDrawer() {
    $('#drawer').classList.add('open');
    $('#backdrop').classList.add('show');
  }

  function closeDrawer() {
    $('#drawer').classList.remove('open');
    $('#backdrop').classList.remove('show');
  }

  $('#drawerClose').addEventListener('click', closeDrawer);
  $('#backdrop').addEventListener('click', closeDrawer);

  document.addEventListener('click', e => {
    if (e.target.closest('[data-close-drawer]')) closeDrawer();
  });

  function openPermissionDrawer() {
    const e = state.employee;
    $('#drawerTitle').textContent = '权限明细';
    $('#drawerSub').textContent = '从 ERP 登录态继承 · 会话内有效';
    $('#drawerBody').innerHTML =
      '<div class="section-label">身份</div><div class="kv">' +
      '<span class="k">员工</span><span class="v">' + esc(e.name) + ' · ' + esc(e.username) + '</span>' +
      '<span class="k">角色</span><span class="v">' + esc(e.role_name) + '</span>' +
      '<span class="k">部门</span><span class="v">' + esc(e.dept) + '</span>' +
      '<span class="k">数据范围</span><span class="v">' + esc(e.scope_label) + '</span></div>' +
      '<div class="section-label">服务端授权</div>' +
      '<div class="mini-card" style="font-size:11.5px;line-height:1.9;color:var(--ink-2)">' +
      '数据范围由查询条件注入，越权返回 E_PERM_001<br>中风险需本人确认，高风险按角色路由审批<br>审批人不能审批自己发起的单据</div>' +
      '<div class="id-note" style="margin-top:14px"><svg class="i sm"><use href="#i-lock"/></svg>' +
      '<span>权限由服务端判定，本页只做展示；切换员工需重新登录。</span></div>';
    $('#drawerFoot').innerHTML = '<button class="btn" data-close-drawer>关闭</button>';
    openDrawer();
  }

  /* --------------------------------- 杂项 ---------------------------------- */

  $('#composer').addEventListener('submit', e => {
    e.preventDefault();
    const input = $('#composerInput');
    const value = input.value.trim();
    if (!value) return;
    input.value = '';
    input.style.height = 'auto';
    sendMessage(value);
  });

  $('#composerInput').addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      $('#composer').dispatchEvent(new Event('submit'));
    }
  });

  $('#composerInput').addEventListener('input', () => {
    const input = $('#composerInput');
    input.style.height = 'auto';
    input.style.height = Math.min(input.scrollHeight, 140) + 'px';
  });

  document.addEventListener('keydown', e => {
    if (e.key === 'Escape') {
      closeDrawer();
      closeConfirm();
    }
  });

  boot();
})();
