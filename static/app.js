'use strict';
const $ = (s, r = document) => r.querySelector(s);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const ICON = { mail: '<svg viewBox="0 0 24 24"><rect x="3" y="5" width="18" height="14" rx="2"/><path d="M3 7l9 7 9-7"/></svg>',
  refresh: '<svg viewBox="0 0 24 24"><path d="M21 12a9 9 0 11-3-6.7L21 8"/><path d="M21 3v5h-5"/></svg>',
  check: '<svg viewBox="0 0 24 24"><path d="M20 6L9 17l-5-5"/></svg>',
  edit: '<svg viewBox="0 0 24 24"><path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 013 3L7 19l-4 1 1-4z"/></svg>',
  ban: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M5.6 5.6l12.8 12.8"/></svg>',
  play: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M10 8l6 4-6 4z"/></svg>',
  trash: '<svg viewBox="0 0 24 24"><path d="M3 6h18"/><path d="M8 6V4h8v2"/><path d="M19 6l-1 14H6L5 6"/></svg>',
  reset: '<svg viewBox="0 0 24 24"><path d="M3 12a9 9 0 109-9 9 9 0 00-6.4 2.6L3 8"/><path d="M3 3v5h5"/></svg>',
};
const TITLE = {
  overview: ['总览', '<svg viewBox="0 0 24 24"><rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/></svg>'],
  generate: ['生图生视频', '<svg viewBox="0 0 24 24"><rect x="3" y="4" width="18" height="16" rx="2.5"/><circle cx="9" cy="10" r="1.6"/><path d="M21 16l-5-5-8 9"/></svg>'],
  chat: ['对话', '<svg viewBox="0 0 24 24"><path d="M4 5.5h16a1.5 1.5 0 0 1 1.5 1.5v8a1.5 1.5 0 0 1-1.5 1.5H9l-4.5 3.5v-3.5H4A1.5 1.5 0 0 1 2.5 15V7A1.5 1.5 0 0 1 4 5.5z"/></svg>'],
  records: ['生成记录', '<svg viewBox="0 0 24 24"><rect x="5" y="3" width="14" height="18" rx="2.5"/><path d="M9 8h6M9 12h6M9 16h4"/></svg>'],
  accounts: ['号池', '<svg viewBox="0 0 24 24"><ellipse cx="12" cy="5.5" rx="8" ry="3"/><path d="M4 5.5v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6"/><path d="M4 11.5v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6"/></svg>'],
  cards: ['卡池', '<svg viewBox="0 0 24 24"><rect x="2.5" y="5" width="19" height="14" rx="2.5"/><path d="M2.5 10h19"/><path d="M6 15h4"/></svg>'],
  refills: ['补号系统', '<svg viewBox="0 0 24 24"><path d="M12 3v3M12 18v3M3 12h3M18 12h3"/><circle cx="12" cy="12" r="6"/></svg>'],
  settings: ['系统设置', '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3"/></svg>'],
};
const STATUS = { ok: '可用', pending_vm: '已注册待分配', cooling: '冷却中', no_quota: '额度用尽', invalid: '异常', disabled: '已停用', unknown: '未检测' };
const HTTP_LABEL = {
  400: '请求无效', 401: '未授权', 403: '拒绝访问', 404: '未找到',
  408: '上游超时', 429: '请求过频', 500: '上游错误', 502: '网关错误',
  503: '服务繁忙', 504: '网关超时',
};
function httpLabel(code, ok) {
  if (!code) return '失败';
  if (ok === false) return code >= 200 && code < 300 ? '未取到图' : ((HTTP_LABEL[code] || '失败') + ' ' + code);
  if (code >= 200 && code < 300) return '成功';
  return (HTTP_LABEL[code] || '失败') + ' ' + code;
}
function friendlyErr(s) {
  s = String(s || '').trim();
  if (!s) return '';
  const pool = (inner) => {
    const map = { total: '共', disabled: '停用', invalid: '失效', no_quota: '无额度', cooling: '冷却', capped: '日限', busy: '忙碌', non_points: '非积分号' };
    const parts = [];
    String(inner).replace(/\b(total|disabled|invalid|no_quota|cooling|capped|busy|non_points)=(\d+)/gi, (_, k, v) => { parts.push((map[k.toLowerCase()] || k) + v); return ''; });
    return parts.length ? parts.join(' · ') : inner;
  };
  let m;
  if ((m = s.match(/^all accounts busy, queue timeout\s*\((.+)\)\s*$/i))) return '账号全忙，排队超时（' + pool(m[1]) + '）';
  if ((m = s.match(/^no usable account\s*\((.+)\)\s*$/i))) return '暂无可用账号（' + pool(m[1]) + '）';
  if (/^server busy/i.test(s)) return '服务器繁忙，请稍后再试';
  if (/^all attempts failed:\s*/i.test(s)) return '全部尝试失败：' + friendlyErr(s.replace(/^all attempts failed:\s*/i, ''));
  if (/^no proxy configured/i.test(s)) return '未配置代理（设置 → 代理池）';
  if (/^acquire arp ticket failed:\s*/i.test(s)) return '获取 ARP 票据失败：' + s.replace(/^acquire arp ticket failed:\s*/i, '');
  if (/^prompt is required$/i.test(s)) return '请输入提示词';
  if (/console_disabled/i.test(s)) return '试调已关闭（设置里打开）';
  if (/upstream_not_configured/i.test(s)) return '上游未配置';
  if (/card_|\bcvv\b|\bpan\b|checkout/i.test(s)) return '禁止卡字段';
  if (/timeout_error|system under load/i.test(s)) return '上游繁忙或超时（408）';
  if (/context canceled|poll aborted/i.test(s)) return '轮询中断：上游已受理，但未取到成图（常见于刷新页面或请求超时）';
  if (/poll finished without image/i.test(s)) return '上游已受理，但未取到成图';
  if (/^HTTP\s+(\d+)$/i.test(s)) return httpLabel(parseInt(RegExp.$1, 10));
  return s.length > 200 ? s.slice(0, 200) + '…' : s;
}

let timer = null;
const GALLERY_MAX = 9;
const jobs = []; // live + recent gallery cards (capped at GALLERY_MAX)

function errText(v) {
  if (v == null || v === '') return '';
  if (typeof v === 'string') return v;
  if (typeof v === 'number' || typeof v === 'boolean') return String(v);
  if (typeof v !== 'object') return String(v);
  if (typeof v.message === 'string' && v.message) return v.message;
  if (typeof v.error === 'string' && v.error) return v.error;
  if (v.error && typeof v.error === 'object') return errText(v.error);
  if (typeof v.detail === 'string' && v.detail) return v.detail;
  if (v.detail && typeof v.detail === 'object') return errText(v.detail);
  if (typeof v.code === 'string' || typeof v.code === 'number') {
    const msg = typeof v.message === 'string' ? v.message : '';
    return msg ? (msg + ' (' + v.code + ')') : String(v.code);
  }
  try { return JSON.stringify(v); } catch (_) { return Object.prototype.toString.call(v); }
}
function apiErr(d, status) {
  if (!d) return httpLabel(status);
  if (typeof d === 'string') return friendlyErr(d);
  const nested = errText(d.error) || errText(d.message) || errText(d.detail) || errText(d);
  if (nested && nested !== '[object Object]') return friendlyErr(nested);
  return httpLabel(status);
}
async function api(path, opt = {}) {
  const o = { method: opt.method || 'GET', headers: {}, credentials: 'same-origin' };
  if (opt.body !== undefined) { o.headers['Content-Type'] = 'application/json'; o.body = JSON.stringify(opt.body); }
  const r = await fetch(path, o);
  if (r.status === 401) { location.replace('/login.html'); throw new Error('未登录或登录已过期'); }
  let d = null;
  try { d = await r.json(); } catch (_) {}
  if (!r.ok && !opt.soft) throw new Error(apiErr(d, r.status));
  if (d && typeof d === 'object' && d.status == null && !r.ok) d.status = r.status;
  if (d && typeof d === 'object' && d.error) d.error = friendlyErr(errText(d.error));
  return d;
}
let consoleToken = null, consoleTokenExp = 0;
async function ensureConsoleToken() {
  if (consoleToken && Date.now() < consoleTokenExp - 5000) return consoleToken;
  const d = await api('/api/generate/token', { method: 'POST', body: {} });
  consoleToken = d.token; consoleTokenExp = Date.now() + (d.expires_in || 600) * 1000;
  return consoleToken;
}
async function v1api(path, opt = {}) {
  const tok = await ensureConsoleToken();
  const o = { method: opt.method || 'GET', headers: { Authorization: 'Bearer ' + tok }, credentials: 'same-origin' };
  if (opt.body !== undefined) { o.headers['Content-Type'] = 'application/json'; o.body = JSON.stringify(opt.body); }
  const r = await fetch(path, o);
  let d = null; try { d = await r.json(); } catch (_) {}
  // Console tokens live only in server memory; restart/expiry yields local 401 — remint once.
  if ((r.status === 401 || r.status === 403) && !opt._authRetried) {
    consoleToken = null; consoleTokenExp = 0;
    return v1api(path, Object.assign({}, opt, { _authRetried: true }));
  }
  if (!r.ok) {
    const msg = apiErr(d, r.status) || httpLabel(r.status);
    if (!opt.soft) throw new Error(msg);
    return {
      ok: false,
      http_status: r.status,
      status: 'error',
      error: msg,
      code: (d && d.detail && d.detail.error && d.detail.error.code) || (d && d.error && d.error.code) || r.status,
    };
  }
  if (d && typeof d === 'object') {
    d.http_status = r.status;
    // Keep upstream string statuses (queued/completed); only fill numeric HTTP when absent
    if (d.status == null || d.status === '') d.status = r.status;
  }
  return d;
}
function mapRecDelete(body) {
  if (!body || typeof body !== 'object') return { mode: 'failed' };
  if (body.mode) return body;
  if (body.failedOnly) return { mode: 'failed' };
  if (body.ids) return { mode: 'ids', ids: body.ids };
  if (body.all) return { mode: 'all' };
  if (body.olderThanDays != null) return { mode: 'before_days', days: body.olderThanDays || 30 };
  return body;
}
function normRecord(r) {
  if (!r) return r;
  const urls = Array.isArray(r.media_urls) ? r.media_urls
    : (Array.isArray(r.imageUrls) ? r.imageUrls
      : (Array.isArray(r.urls) ? r.urls : (r.url ? [r.url] : [])));
  const bucket = r.status_bucket || (r.status == null ? 'running' : (r.status >= 200 && r.status < 300 ? 'success' : 'failed'));
  return Object.assign({}, r, {
    createdAt: r.createdAt || (r.created_at ? Date.parse(r.created_at) : 0),
    prompt: r.prompt || r.prompt_preview || '',
    account: r.account || r.key_prefix || r.source || '-',
    state: r.state || (bucket === 'running' ? 'running' : 'done'),
    status_bucket: bucket,
    ok: r.ok != null ? r.ok : bucket === 'success',
    elapsedMs: r.elapsedMs != null ? r.elapsedMs : r.latency_ms,
    imageUrls: urls, images: r.images != null ? r.images : urls.length,
    resultHref: r.resultHref || urls[0] || '',
    size: r.size || r.api_type || '',
    api_type: r.api_type, key_prefix: r.key_prefix, source: r.source,
    upstream_task_id: r.upstream_task_id, error: r.error || '',
  });
}
function isChatRecord(r) {
  if (!r) return false;
  const t = String(r.api_type || '').toLowerCase();
  if (t === 'chat') return true;
  if (/\/chat\/completions/.test(String(r.path || ''))) return true;
  return false;
}
function absUrl(u) {
  try { return new URL(u, location.origin).href; } catch (_) { return u; }
}
async function copyText(text) {
  try { await navigator.clipboard.writeText(text); }
  catch (_) {
    const ta = document.createElement('textarea');
    ta.value = text; ta.style.position = 'fixed'; ta.style.left = '-9999px';
    document.body.appendChild(ta); ta.select();
    document.execCommand('copy'); ta.remove();
  }
  toast('链接已复制');
}
async function downloadUrl(url) {
  const name = decodeURIComponent((String(url).split('?')[0].split('/').pop()) || 'download');
  try {
    const r = await fetch(url, { credentials: 'include' });
    if (!r.ok) throw new Error(String(r.status));
    const blob = await r.blob();
    const obj = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = obj; a.download = name;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(obj), 2000);
    toast('已开始下载');
    return;
  } catch (_) {}
  const a = document.createElement('a');
  a.href = url; a.download = name; a.target = '_blank'; a.rel = 'noopener';
  document.body.appendChild(a); a.click(); a.remove();
}
function normAccount(a) {
  if (!a) return a;
  const label = a.label || a.name || a.email || a.tag || a.id || '';
  const cookieCount = a.cookie_count != null ? a.cookie_count : (a.cookieCount != null ? a.cookieCount : 0);
  return Object.assign({}, a, {
    label, tag: a.tag || label, account: a.account || label,
    email: a.email || '', name: a.name || label, note: a.note || a.remark || '',
    profile: a.profile || (cookieCount ? cookieCount + ' cookies' : '0'),
    cookie_count: cookieCount,
    hasCookie: a.hasCookie != null ? a.hasCookie : (a.has_cookie != null ? a.has_cookie : cookieCount > 0),
    importedAt: a.importedAt || (a.created_at ? (typeof a.created_at === 'number' ? a.created_at * (a.created_at < 1e12 ? 1000 : 1) : Date.parse(a.created_at)) : 0),
    expires_at: a.expires_at, expires_estimated: !!a.expires_estimated,
    last_used: a.last_used, quota: a.quota || null,
    success: a.success != null ? a.success : (a.use_count || 0),
    fail: a.fail != null ? a.fail : 0, today: a.today != null ? a.today : 0,
    credits: a.credits != null ? a.credits : '-', creditsAt: a.creditsAt || null, points: !!a.points,
    busy: !!a.busy, lastError: a.lastError || a.last_error || '', enabled: a.enabled !== false,
    status: a.status || (a.enabled === false ? 'disabled' : (a.ok === true ? 'ok' : (a.ok === false ? 'invalid' : 'unknown'))),
  });
}
function tsMs(v) {
  if (v == null || v === '') return 0;
  if (typeof v === 'number') return v < 1e12 ? v * 1000 : v;
  const n = Date.parse(v); return Number.isFinite(n) ? n : 0;
}
function agoUnix(v) {
  const ms = tsMs(v); if (!ms) return '—';
  const s = Math.max(0, Math.round((Date.now() - ms) / 1000));
  if (s < 60) return s + ' 秒前';
  if (s < 3600) return Math.floor(s / 60) + ' 分钟前';
  if (s < 86400) return Math.floor(s / 3600) + ' 小时前';
  if (s < 86400 * 30) return Math.floor(s / 86400) + ' 天前';
  return fmtTime(ms);
}
function expiryBadge(a) {
  const ts = a && a.expires_at; if (!ts) return '<span class="chip c-disabled">未知</span>';
  const leftSec = (typeof ts === 'number' ? (ts < 1e12 ? ts : Math.floor(ts / 1000)) : Math.floor(tsMs(ts) / 1000)) - Math.floor(Date.now() / 1000);
  const est = a.expires_estimated ? '（估算）' : '';
  if (leftSec <= 0) return `<span class="chip c-invalid" title="${esc(fmtTime(tsMs(ts)))}${est}">已过期</span>`;
  const days = leftSec / 86400;
  const txt = days >= 1 ? (`剩 ${days >= 10 ? Math.round(days) : days.toFixed(1)} 天`) : (`剩 ${Math.max(1, Math.floor(leftSec / 3600))} 小时`);
  const cls = days < 1 ? 'c-invalid' : days < 3 ? 'c-cooling' : 'c-ok';
  return `<span class="chip ${cls}" title="到期 ${esc(fmtTime(tsMs(ts)))}${est}">${txt}</span>`;
}
function quotaBadge(a) {
  const q = a && a.quota;
  if (!q || !q.found) return '<span class="chip c-disabled" title="点操作「额度」查询">未查</span>';
  const pct = q.weekly_used_pct != null ? q.weekly_used_pct : null;
  const cls = pct == null ? 'c-busy' : pct < 50 ? 'c-ok' : pct < 80 ? 'c-cooling' : 'c-invalid';
  const plan = String(q.plan || '').replace(/\s*plan\s*$/i, '') || '—';
  const main = pct != null ? `${plan} · 已用 ${pct}%` : plan;
  return `<span class="chip ${cls}" title="${esc((q.plan || '') + (pct != null ? ' 周用量 ' + pct + '%' : ''))}">${esc(main)}</span>`;
}
function normSub(a) {
  if (!a) return a;
  const ui = a.ui_status || a.status || 'pending';
  const st = ui === 'awaiting_external_payment' ? 'awaiting_payment' : ui;
  const prof = (a.profile && typeof a.profile === 'object') ? a.profile : {};
  return Object.assign({}, a, {
    status: st, rawStatus: a.status, ui_status: ui,
    mailReady: a.mailReady != null ? a.mailReady : !!a.mail_ready,
    hasAux: !!a.hasAux,
    auxEmailMasked: a.aux_email_masked || a.auxEmailMasked || '',
    country: a.country || a.region || '-',
    profile: prof,
    displayName: a.display_name || a.displayName || [prof.first_name, prof.last_name].filter(Boolean).join(' ') || '',
    fingerprintProfile: a.fingerprint_profile || (a.fingerprint && a.fingerprint.profile_id) || '',
    fingerprintLabel: a.fingerprint_label || (a.fingerprint && a.fingerprint.label_zh) || a.fingerprint_summary || '',
    resultSummary: a.result_summary || a.resultSummary || a.message || '',
    message: a.message || a.error || '',
    createdAt: a.createdAt || (a.created_at ? Date.parse(a.created_at) : 0),
    pooled: a.pooled != null ? a.pooled : (a.status === 'pooled' || ui === 'pooled'),
    hasCookie: a.hasCookie != null ? a.hasCookie : !!(a.cookie_preview || a.hasCookie),
    hasPassword: !!(a.has_muse_login_password || a.has_password || a.hasPassword),
    password_login_available: !!(a.password_login_available || a.has_password),
    flow: a.flow || '',
    needs: a.needs || [],
    upstreamId: a.upstream_account_id || a.upstreamId || '',
    cardMasked: a.card_masked || a.cardMasked || '',
  });
}
function normRefill(a) {
  if (!a) return a;
  return Object.assign({}, a, {
    mailReady: a.mailReady != null ? a.mailReady : !!a.mail_ready,
    hasAux: !!a.hasAux || !!(a.aux_email || a.auxEmail),
    auxEmail: a.auxEmail || a.aux_email || '',
    country: a.country || a.region || '-',
    profile: a.profile || a.mode || '-',
    mode: a.mode || 'signup',
    message: a.message || a.error || a.last_error || '',
    createdAt: a.createdAt || (a.created_at ? Date.parse(a.created_at) : 0),
    pooled: a.pooled != null ? a.pooled : !!a.upstream_account_id,
    hasCookie: a.hasCookie != null ? a.hasCookie : !!a.cookie_preview,
    hasSecret: a.hasSecret != null ? a.hasSecret : (a.has_secret != null ? a.has_secret : true),
    signup_item_id: a.signup_item_id || a.signupItemId || null,
  });
}
function toast(msg, err) {
  const t = document.createElement('div');
  t.className = 'toast' + (err ? ' err' : '');
  t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(() => t.remove(), err ? 4500 : 2200);
}
// Styled replacement for window.confirm(): resolves true / false.
// "（...）" at the end of the message is shown as a muted hint line.
function ask(msg, opt) {
  opt = opt || {};
  return new Promise(resolve => {
    const mm = String(msg).match(/^([\s\S]*?)\s*[（(]([^（()）]*)[)）]\s*$/);
    const main = mm ? mm[1] : String(msg), hint = mm ? mm[2] : '';
    const m = document.createElement('div');
    m.className = 'mask';
    m.innerHTML = '<div class="modal confirm"><div class="ci">!</div><div class="cb"><h3>' + esc(opt.title || '确认操作') + '</h3><p>' + esc(main) + '</p>' +
      (hint ? '<p class="hint">' + esc(hint) + '</p>' : '') + '</div>' +
      '<div class="foot"><button class="btn" data-x="0">取消</button><button class="btn ' + (opt.primary ? 'primary' : 'danger-solid') + '" data-x="1">' + esc(opt.ok || '确定') + '</button></div></div>';
    const done = (v) => { document.removeEventListener('keydown', key, true); m.remove(); resolve(v); };
    const key = (e) => { if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); done(false); } else if (e.key === 'Enter') { e.preventDefault(); e.stopPropagation(); done(true); } };
    m.addEventListener('mousedown', e => { if (e.target === m) done(false); });
    m.querySelectorAll('[data-x]').forEach(b => b.onclick = () => done(b.dataset.x === '1'));
    document.addEventListener('keydown', key, true);
    document.body.appendChild(m);
    m.querySelector('[data-x="1"]').focus();
  });
}
function modal(html, onMount) {
  const m = document.createElement('div');
  m.className = 'mask';
  m.innerHTML = '<div class="modal">' + html + '</div>';
  m.addEventListener('mousedown', e => { if (e.target === m) m.remove(); });
  document.body.appendChild(m);
  if (onMount) onMount(m);
  return m;
}
const fmtTime = (ms) => {
  if (!ms) return '-';
  const d = new Date(ms), p = (n) => String(n).padStart(2, '0');
  return `${p(d.getMonth() + 1)}/${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
};
const fmtClock = (ms) => { const d = new Date(ms), p = (n) => String(n).padStart(2, '0'); return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`; };
const left = (ms) => { const s = Math.max(0, Math.round((ms - Date.now()) / 1000)); return s >= 3600 ? Math.round(s / 3600) + 'h' : s >= 60 ? Math.round(s / 60) + 'm' : s + 's'; };

/* ---------- router ---------- */
function go(page) { location.hash = '#' + page; }
function route() {
  let page = (location.hash || '#overview').slice(1);
  if (page === 'subs') { location.hash = '#refills'; return; }
  const p = TITLE[page] ? page : 'overview';
  document.querySelectorAll('.nav[data-page]').forEach(b => b.classList.toggle('active', b.dataset.page === p));
  clearInterval(timer); timer = null;
  $('#main').classList.toggle('gen-fill', p === 'generate');
  PAGES[p]();
}
document.querySelectorAll('.nav[data-page]').forEach(b => b.addEventListener('click', () => go(b.dataset.page)));
$('#logout').addEventListener('click', async () => { await fetch('/api/logout', { method: 'POST' }); location.replace('/login.html'); });
window.addEventListener('hashchange', route);

const OSS_LINK = '<a class="oss" href="https://github.com/lichao199208/muse2api" target="_blank" rel="noopener noreferrer"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 19c-5 1.5-5-2.5-7-3m14 6v-3.87a3.37 3.37 0 00-.94-2.61c3.14-.35 6.44-1.54 6.44-7A5.44 5.44 0 0020 4.77 5.07 5.07 0 0019.91 1S18.73.65 16 2.48a13.38 13.38 0 00-7 0C6.27.65 5.09 1 5.09 1A5.07 5.07 0 005 4.77a5.44 5.44 0 00-1.5 3.78c0 5.42 3.3 6.61 6.44 7A3.37 3.37 0 009 18.13V22"/></svg>开源地址</a>';

function head(page, actions = '') {
  const [t, ic] = TITLE[page];
  document.title = 'muse-proxy · ' + t;
  return `<div class="page-head"><h1>${ic}${t}</h1>${OSS_LINK}<div class="actions">${actions}</div></div>`;
}

/* ---------- overview ---------- */
function statCard(k, v, s = '') { return `<div class="stat"><div class="k">${k}</div><div class="v">${v}</div><div class="s">${s}</div></div>`; }

/* ---------- charts (dependency-free SVG) ---------- */
const fmtBytes = (n) => n >= 2 ** 30 ? (n / 2 ** 30).toFixed(1) + ' GB' : n >= 2 ** 20 ? Math.round(n / 2 ** 20) + ' MB' : Math.round(n / 1024) + ' KB';
const p2 = (n) => String(n).padStart(2, '0');
let chartSeq = 0;

// o: { ts[], series:[{name,color,data[],fill,dots,fmt(v,i)}], yMax, fmtY(v), fmtX(t) }
function lineChart(el, o) {
  if (!o.ts.length) { el.innerHTML = '<div class="chart-empty">暂无数据</div>'; return; }
  const id = 'cg' + (++chartSeq);
  const W = 620, H = o.h || 190, L = 40, R = 10, T = 10, B = 22, iw = W - L - R, ih = H - T - B;
  const t0 = o.ts[0], t1 = o.ts[o.ts.length - 1], span = Math.max(1, t1 - t0);
  const X = (t) => L + (t - t0) / span * iw;
  const Y = (v) => T + (1 - Math.min(Math.max(v, 0), o.yMax) / o.yMax) * ih;
  let g = '';
  for (let i = 0; i <= 4; i++) {
    const v = o.yMax * i / 4, y = Y(v).toFixed(1);
    g += `<line x1="${L}" x2="${W - R}" y1="${y}" y2="${y}" class="gl"/><text x="${L - 6}" y="${(+y + 3.5).toFixed(1)}" class="gt" text-anchor="end">${o.fmtY(v)}</text>`;
  }
  for (let i = 0; i <= 4; i++) {
    const t = t0 + span * i / 4;
    g += `<text x="${X(t).toFixed(1)}" y="${H - 6}" class="gt" text-anchor="${i === 0 ? 'start' : i === 4 ? 'end' : 'middle'}">${o.fmtX(t)}</text>`;
  }
  let defs = '', body = '';
  const base = Y(0).toFixed(1);
  o.series.forEach((se, si) => {
    const runs = []; let cur = [];
    se.data.forEach((v, i) => { if (v == null) { if (cur.length) runs.push(cur); cur = []; } else cur.push([X(o.ts[i]), Y(v)]); });
    if (cur.length) runs.push(cur);
    if (se.fill) defs += `<linearGradient id="${id}_${si}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="${se.color}" stop-opacity=".28"/><stop offset="1" stop-color="${se.color}" stop-opacity="0"/></linearGradient>`;
    runs.forEach(r => {
      const pts = r.map(p => p[0].toFixed(1) + ',' + p[1].toFixed(1));
      if (r.length > 1) {
        if (se.fill) body += `<path d="M${pts.join(' L')} L${r[r.length - 1][0].toFixed(1)},${base} L${r[0][0].toFixed(1)},${base}Z" fill="url(#${id}_${si})"/>`;
        body += `<path d="M${pts.join(' L')}" fill="none" stroke="${se.color}" stroke-width="1.8" stroke-linejoin="round" stroke-linecap="round"/>`;
      }
      if (r.length === 1 || (se.dots && se.data.length <= 60)) r.forEach(p => body += `<circle cx="${p[0].toFixed(1)}" cy="${p[1].toFixed(1)}" r="2.6" fill="#fff" stroke="${se.color}" stroke-width="1.6"/>`);
    });
  });
  el.innerHTML = `<div class="cb"><svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" style="height:${H}px"><defs>${defs}</defs>${g}${body}<line class="vl" x1="0" x2="0" y1="${T}" y2="${T + ih}" style="display:none"/></svg><div class="tip" style="display:none"></div></div>`;
  const svg = el.querySelector('svg'), vl = el.querySelector('.vl'), tip = el.querySelector('.tip');
  svg.addEventListener('mousemove', (e) => {
    const rc = svg.getBoundingClientRect();
    const px = (e.clientX - rc.left) / rc.width * W;
    let best = 0, bd = 1e18;
    o.ts.forEach((t, i) => { const d = Math.abs(X(t) - px); if (d < bd) { bd = d; best = i; } });
    const x = X(o.ts[best]);
    vl.setAttribute('x1', x); vl.setAttribute('x2', x); vl.style.display = '';
    tip.innerHTML = `<b>${o.fmtT ? o.fmtT(o.ts[best]) : o.fmtX(o.ts[best])}</b>` + o.series.map(se => {
      const v = se.data[best];
      return `<div><i style="background:${se.color}"></i>${se.name}<span>${v == null ? '—' : (se.fmt ? se.fmt(v, best) : o.fmtY(v))}</span></div>`;
    }).join('');
    tip.style.display = '';
    const left = x / W * rc.width;
    tip.style.left = (left > rc.width * 0.6 ? left - tip.offsetWidth - 12 : left + 12) + 'px';
  });
  svg.addEventListener('mouseleave', () => { vl.style.display = 'none'; tip.style.display = 'none'; });
}

const legendDots = (arr) => arr.map(([c, n]) => `<span><i class="dot" style="background:${c}"></i>${n}</span>`).join('');

function genCard(title, color, d) {
  const col = (name, s) => {
    const rate = s.total ? (s.ok / s.total * 100).toFixed(1) + '%' : '—';
    return `<div class="gcol"><div class="k">${name}</div><div class="gnum">${s.total}</div>
      <div class="gline"><span class="gok">成功 ${s.ok}</span><span class="gfail">失败 ${s.fail}</span></div>
      <div class="grate"><div class="gbar"><i style="width:${s.total ? s.ok / s.total * 100 : 0}%;background:${color}"></i></div><b>${rate}</b></div></div>`;
  };
  return `<div class="card gen-card" style="--gc:${color}"><h2><span><i class="dot" style="background:${color}"></i>${title}</span></h2>
    <div class="gcols">${col('今日', d.today)}${col('累计', d.all)}</div></div>`;
}

async function pageOverview() {
  const main = $('#main');
  main.innerHTML = head('overview', '<button class="btn" id="rf">刷新</button>') + `
    <div class="grid g2" style="margin-bottom:14px"><div id="g-img"></div><div id="g-vid"></div></div>
    <div class="grid g2">
      <div class="card"><h2><span>调用成功率</span><span class="rtabs" id="rtabs"><button data-r="6h">6 小时</button><button data-r="24h">24 小时</button><button data-r="7d">7 天</button></span></h2>
        <div id="ch-rate"></div><div class="legend" style="margin-top:6px">${legendDots([['#2563eb', '总体'], ['#10b981', '成功'], ['#ef4444', '失败']])}</div></div>
      <div class="card" id="acc"></div>
    </div>
    <div class="grid g2">
      <div class="card"><h2><span>上游 / 反代</span><small id="up-h"></small></h2><div id="up-box" class="hint">加载中…</div></div>
      <div class="card"><h2><span>Key 概况</span></h2><div id="key-box" class="hint">加载中…</div></div>
    </div>
    <div class="card"><h2>实时事件</h2><div class="events" id="evs"></div></div>`;
  $('#rf').onclick = () => { loadMain(); loadSeries(); };
  let range = localStorage.getItem('ov_range') || '24h';
  const syncTabs = () => document.querySelectorAll('#rtabs button').forEach(b => b.classList.toggle('on', b.dataset.r === range));
  document.querySelectorAll('#rtabs button').forEach(b => b.onclick = () => { range = b.dataset.r; localStorage.setItem('ov_range', range); syncTabs(); loadSeries(); });
  syncTabs();
  const hover = (id) => { const e = document.getElementById(id); return e && e.matches(':hover'); };
  function pickType(by, keys) {
    let tot = 0, ok = 0, fail = 0;
    Object.entries(by || {}).forEach(([k, v]) => {
      const lk = k.toLowerCase();
      if (keys.some(x => lk.includes(x))) { tot += v.total || 0; ok += v.ok || 0; fail += v.fail || 0; }
    });
    return { total: tot, ok, fail };
  }
  async function loadMain() {
    let d; try { d = await api('/api/overview?range=' + range); } catch (_) { return; }
    const today = d.today || {}, all = d.cumulative || {}, byT = today.by_type || {}, byA = all.by_type || {};
    const imgT = pickType(byT, ['image', 'images']); const imgA = pickType(byA, ['image', 'images']);
    const vidT = pickType(byT, ['video', 'videos']); const vidA = pickType(byA, ['video', 'videos']);
    // fallback: if typed empty, put all into image card
    const imgToday = imgT.total ? imgT : { total: today.total || 0, ok: today.ok || 0, fail: today.fail || 0 };
    const imgAll = imgA.total ? imgA : { total: all.total || 0, ok: all.ok || 0, fail: all.fail || 0 };
    $('#g-img').innerHTML = genCard('图片 / 对话调用', '#2563eb', { today: imgToday, all: imgAll });
    $('#g-vid').innerHTML = genCard('视频调用', '#8b5cf6', { today: vidT.total ? vidT : { total: 0, ok: 0, fail: 0 }, all: vidA.total ? vidA : { total: 0, ok: 0, fail: 0 } });
    let pool = {}, stats = {};
    try {
      const acc = await api('/api/accounts?page=1&page_size=1');
      stats = acc.stats || {};
      pool = { total: acc.total || 0, ok: stats.ok || stats.active || 0, cooling: stats.cooling || 0, invalid: stats.invalid || 0, disabled: stats.disabled || 0, no_quota: stats.no_quota || 0, busy: stats.busy || 0 };
    } catch (_) { pool = { total: 0, ok: 0, cooling: 0, invalid: 0, disabled: 0, no_quota: 0, busy: 0 }; }
    if (!pool.total && d.upstream && d.upstream.pool) {
      const ps = d.upstream.pool.stats || {};
      pool.total = d.upstream.pool.account_count || ps.total || 0;
      pool.ok = ps.ok || ps.active || 0; pool.cooling = ps.cooling || 0; pool.invalid = ps.invalid || 0; pool.disabled = ps.disabled || 0;
    }
    const tot = Math.max(1, pool.total || 1);
    const seg = (n, c) => `<i style="width:${(n / tot * 100).toFixed(2)}%;background:${c}"></i>`;
    $('#acc').innerHTML = `<h2><span>账号概况</span><small>共 ${pool.total || 0} 个（上游号池）</small></h2>
      <div class="bar">${seg(pool.ok||0,'#10b981')}${seg(pool.cooling||0,'#f59e0b')}${seg(pool.no_quota||0,'#8b5cf6')}${seg(pool.invalid||0,'#ef4444')}${seg(pool.disabled||0,'#9ca3af')}</div>
      <div class="legend">
        <span><i class="dot" style="background:#10b981"></i>启用 <b>${pool.ok||0}</b></span>
        <span><i class="dot" style="background:#f59e0b"></i>冷却 <b>${pool.cooling||0}</b></span>
        <span><i class="dot" style="background:#ef4444"></i>失效 <b>${pool.invalid||0}</b></span>
        <span><i class="dot" style="background:#9ca3af"></i>停用 <b>${pool.disabled||0}</b></span>
      </div>
      <div class="kv-row"><div><span>今日调用</span><b>${today.total||0}</b></div><div><span>今日成功率</span><b>${today.success_rate != null ? today.success_rate + '%' : '—'}</b></div>
        <div><span>累计</span><b>${all.total||0}</b></div><div><span>上游</span><b>${(d.upstream && d.upstream.health && d.upstream.health.ok) ? '健康' : '异常'}</b></div></div>`;
    const evs = d.recent_failures || [];
    $('#evs').innerHTML = evs.map(e => `<div class="ev ${esc(e.level||'error')}"><time>${esc(fmtClock(e.created_at ? Date.parse(e.created_at) : Date.now()))}</time><span class="m">${esc(e.message||'')}</span></div>`).join('') || '<div class="hint">暂无事件</div>';
    const up = d.upstream || {};
    $('#up-h').textContent = up.health && up.health.ok ? '健康' : '需检查';
    $('#up-box').innerHTML = `<div class="kv-row"><div><span>Health</span><b>${up.health && up.health.ok ? 'ok' : 'fail'}</b></div><div><span>Browser</span><b>${(up.pool && up.pool.browser_running) ? '运行中' : '-'}</b></div><div><span>媒体</span><b>${(up.pool && up.pool.media_count) != null ? up.pool.media_count : '-'}</b></div><div><span>号池</span><b>${(up.pool && up.pool.account_count) != null ? up.pool.account_count : (pool.total||0)}</b></div></div>` + (up.pool && up.pool.error ? `<div class="hint" style="color:var(--err)">${esc(up.pool.error)}</div>` : '');
    const k = d.keys || {};
    $('#key-box').innerHTML = `<div class="kv-row"><div><span>库内 Key</span><b>${k.db_total||0}</b></div><div><span>已启用</span><b>${k.db_enabled||0}</b></div><div><span>环境 Key</span><b>${k.env_count||0}</b></div><div><span>时区</span><b>${esc(d.tz||'Asia/Shanghai')}</b></div></div>`;
  }
  async function loadSeries() {
    let d; try { d = await api('/api/stats/series?range=' + range); } catch (_) { return; }
    if (hover('ch-rate')) return;
    const pts = d.points || [];
    const fx = (t) => { const x = new Date(t); return range === '7d' ? `${x.getMonth() + 1}/${x.getDate()} ${p2(x.getHours())}时` : p2(x.getHours()) + ':' + p2(x.getMinutes()); };
    const fxt = (t) => { const x = new Date(t); return `${x.getMonth() + 1}/${x.getDate()} ${p2(x.getHours())}:${p2(x.getMinutes())}`; };
    lineChart($('#ch-rate'), {
      ts: pts.map(p => Date.parse(p.t) || 0), yMax: 100, fmtY: v => Math.round(v) + '%', fmtX: fx, fmtT: fxt,
      series: [
        { name: '成功率', color: '#2563eb', fill: true, dots: true, data: pts.map(p => p.success_rate != null ? p.success_rate : (p.total ? 100 * p.ok / p.total : null)), fmt: (v, i) => `${(v||0).toFixed(1)}% (${pts[i].ok}/${pts[i].total})` },
        { name: '成功数', color: '#10b981', dots: false, data: pts.map(p => p.total ? 100 * (p.ok||0) / Math.max(1, Math.max(...pts.map(x => x.total||0))) : null), fmt: (v, i) => String(pts[i].ok||0) },
        { name: '失败数', color: '#ef4444', dots: false, data: pts.map(p => p.total ? 100 * (p.fail||0) / Math.max(1, Math.max(...pts.map(x => x.total||0))) : null), fmt: (v, i) => String(pts[i].fail||0) },
      ],
    });
  }
  await Promise.all([loadMain(), loadSeries()]);
}

/* ---------- generate ---------- */
let MODELS = null;
const isVideoUrl = (u, j) => (j && j.kind === 'video') || /\.(mp4|webm|mov)(\?|$)/i.test(u || '');

// shrink big reference photos in the browser (keeps request small and fast)
function fileToRef(file) {
  return new Promise((resolve, reject) => {
    const fr = new FileReader();
    fr.onerror = () => reject(new Error('读取失败'));
    fr.onload = () => {
      const data = fr.result;
      if (file.size <= 5 * 1024 * 1024) return resolve({ name: file.name, data });
      const img = new Image();
      img.onerror = () => resolve({ name: file.name, data });
      img.onload = () => {
        const k = Math.min(1, 3072 / Math.max(img.width, img.height));
        const c = document.createElement('canvas');
        c.width = Math.round(img.width * k); c.height = Math.round(img.height * k);
        c.getContext('2d').drawImage(img, 0, 0, c.width, c.height);
        resolve({ name: file.name, data: c.toDataURL('image/jpeg', 0.92) });
      };
      img.src = data;
    };
    fr.readAsDataURL(file);
  });
}

async function pageGenerate() {
  const FALLBACK_MODELS = [
    { id: 'muse-image', name: 'Muse Image', label: 'Muse Image', group: 'image', refImages: 4, resolutions: ['1K','2K'], aspects: ['1:1','3:4','4:3','9:16','16:9'], duration: 0, pointsRes: [], sizes: {}, qualities: [], autoAspect: false },
    { id: 'muse-video', name: 'Muse Video', label: 'Muse Video', group: 'video', refImages: 1, resolutions: ['720p','1080p'], aspects: ['1:1','9:16','16:9'], duration: 10, pointsRes: [], sizes: {}, qualities: [], autoAspect: false },
  ];
  function normModel(m) {
    const id = (m && (m.id || m.name)) || 'model';
    const lid = String(id).toLowerCase();
    let group = 'image', refImages = 4, resolutions = ['1K','2K'], aspects = ['1:1','3:4','4:3','9:16','16:9'], duration = 0;
    if (/video|vid|veo|seedance|omni/.test(lid)) { group = 'video'; refImages = 1; resolutions = ['720p','1080p']; aspects = ['1:1','9:16','16:9']; duration = 10; }
    else if (/chat|spark|gpt|claude|llm|text/.test(lid) && !/image|img|vision/.test(lid)) { group = 'chat'; refImages = 0; }
    const label = (m && (m.label || m.name)) || id;
    return {
      id, name: label, label, group,
      refImages: (m && m.refImages != null) ? m.refImages : refImages,
      resolutions: (m && m.resolutions) || resolutions,
      aspects: (m && m.aspects) || aspects,
      duration: (m && m.duration) || duration,
      pointsRes: Array.isArray(m && m.pointsRes) ? m.pointsRes : [],
      sizes: (m && m.sizes && typeof m.sizes === 'object') ? m.sizes : {},
      qualities: Array.isArray(m && m.qualities) ? m.qualities : [],
      autoAspect: !!(m && m.autoAspect),
    };
  }
  try {
    const md = await api('/api/generate/models', { soft: true });
    const data = (md && (md.data || md.models || md.items)) || [];
    MODELS = data.map(normModel).filter(m => m.group === 'image' || m.group === 'video');
    if (!MODELS.length) MODELS = FALLBACK_MODELS.slice();
  } catch (_) { MODELS = FALLBACK_MODELS.slice(); }
  const REFS = [];
  const byId = (id) => MODELS.find(m => m.id === id);
  // mode -> [label, group, needs reference image]
  const MODES = { t2i: ['文生图', 'image', false], i2i: ['图生图', 'image', true], t2v: ['文生视频', 'video', false], i2v: ['图生视频', 'video', true] };
  const saved = JSON.parse(localStorage.getItem('gen_sel') || '{}');
  const S = { mode: MODES[saved.mode] ? saved.mode : 't2i', model: saved.model, res: saved.res, asp: saved.asp, custom: !!saved.custom, cw: saved.cw || 1024, ch: saved.ch || 1024, q: saved.q || 'medium' };
  const modelsFor = (mode) => { const [, grp, need] = MODES[mode]; return MODELS.filter(m => m.group === grp && (!need || m.refImages > 0)); };
  const cur = () => byId(S.model);
  const canCustom = (m) => m.id.startsWith('gpt-image');

  $('#main').innerHTML = head('generate') + `
  <div class="split gen">
    <div class="card genform">
      <div class="mtabs" id="mtabs">${Object.entries(MODES).map(([k, v]) => `<button data-m="${k}">${v[0]}</button>`).join('')}</div>

      <label class="f">提示词</label>
      <textarea id="prompt" style="min-height:92px;font-family:inherit;font-size:13px" placeholder="描述你想生成的画面…">A cinematic 4K aerial view of snow mountains at golden sunrise, photorealistic</textarea>

      <div id="refbox" class="hidden">
        <label class="f">参考图 <span id="refcnt" class="muted"></span></label>
        <div class="refs" id="refs"></div>
        <input type="file" id="rfile" accept="image/*" multiple class="hidden" />
      </div>

      <label class="f">模型</label>
      <select id="model"></select>
      <div class="hint" id="minfo"></div>

      <label class="f">分辨率</label>
      <div class="seg" id="res"></div>

      <div id="qbox"><label class="f">质量</label>
      <div class="seg" id="qual"></div></div>

      <label class="f">画面比例 <span class="sz" id="sizeinfo"></span></label>
      <div class="asp-grid" id="asp"></div>
      <div id="cbox" class="row2 hidden" style="margin-top:8px">
        <div><label class="f">宽 (px)</label><input type="number" id="cw" min="256" max="3840" step="16" /></div>
        <div><label class="f">高 (px)</label><input type="number" id="ch" min="256" max="3840" step="16" /></div>
      </div>

      <div id="vbox" class="hidden">
        <div class="row2">
          <div><label class="f">时长 (秒)</label><input type="number" id="dur" value="5" min="1" /></div>
          <div><label class="f">音频</label><label class="chk"><input type="checkbox" id="aud" /> 生成音频</label></div>
        </div>
        <label class="f">负面提示词（可选）</label><input type="text" id="neg" placeholder="留空使用默认" />
      </div>

      <div class="row2">
        <div><label class="f" id="cntlbl">生成数量</label><input type="number" id="count" value="1" min="1" max="64" /></div>
        <div><label class="f">结果</label><label class="chk"><input type="checkbox" id="wait" checked /> 等待并返回链接</label></div>
      </div>

      <details class="adv" id="adv">
        <summary>自定义 Cookie（可选）</summary>
        <textarea id="ck" spellcheck="false" placeholder="粘贴完整 Cookie，填写后将直接使用该账号生成，不从号池选号；留空则自动选号"></textarea>
      </details>
      <button class="btn primary block gen-go" id="go">立即生成</button>
    </div>
    <div class="card gengallery">
      <div class="card-h"><span>任务与画廊</span><button class="btn" id="clr">清空</button></div>
      <div class="jobs" id="jobs"></div>
    </div>
  </div>`;

  /* ---- reference images ---- */
  function drawRefs() {
    const m = cur(), box = $('#refs');
    $('#refcnt').textContent = `${REFS.length} / ${m.refImages}`;
    box.innerHTML = REFS.map((r, i) => `<div class="rth"><img src="${r.data}" title="${esc(r.name)}" /><button data-i="${i}" title="移除">×</button></div>`).join('') +
      (REFS.length < m.refImages ? '<div class="radd" id="radd" title="添加参考图（也可拖入或粘贴）">+</div>' : '');
    box.querySelectorAll('button[data-i]').forEach(b => b.onclick = () => { REFS.splice(+b.dataset.i, 1); drawRefs(); });
    const add = $('#radd'); if (add) add.onclick = () => $('#rfile').click();
  }
  async function addFiles(files) {
    const [, , need] = MODES[S.mode];
    if (!need) { toast('请先切换到「图生图 / 图生视频」', true); return; }
    const m = cur();
    for (const f of files) {
      if (!f.type.startsWith('image/')) continue;
      if (REFS.length >= m.refImages) { toast(`该模型最多 ${m.refImages} 张参考图`, true); break; }
      try { REFS.push(await fileToRef(f)); } catch (e) { toast(e.message, true); }
    }
    drawRefs();
  }
  $('#rfile').onchange = (e) => { addFiles([...e.target.files]); e.target.value = ''; };
  const rbox = $('#refbox');
  rbox.ondragover = (e) => { e.preventDefault(); $('#refs').classList.add('drag'); };
  rbox.ondragleave = () => $('#refs').classList.remove('drag');
  rbox.ondrop = (e) => { e.preventDefault(); $('#refs').classList.remove('drag'); addFiles([...e.dataTransfer.files]); };
  $('#prompt').onpaste = (e) => { const fs = [...(e.clipboardData?.files || [])]; if (fs.length && MODES[S.mode][2]) { e.preventDefault(); addFiles(fs); } };

  /* ---- form state -> UI ---- */
  const ratioBox = (a) => {
    const [w, h] = a.split(':').map(Number); const k = 20 / Math.max(w, h);
    return `<i style="width:${Math.max(3, Math.round(w * k))}px;height:${Math.max(3, Math.round(h * k))}px"></i>`;
  };
  function sizeText() {
    const m = cur();
    if (!m) return '-';
    if (S.custom) return `${S.cw}×${S.ch}`;
    if (S.asp === 'auto') return '跟随参考图比例';
    const sizes = m.sizes || {};
    const px = (sizes[S.res] || {})[S.asp];
    return px ? `${px[0]}×${px[1]}` : '-';
  }
  function drawInfo() {
    const m = cur();
    if (!m) return;
    const ptsRes = m.pointsRes || [];
    const pts = (!S.custom && ptsRes.includes(S.res)) || (S.custom && ptsRes.includes('4K') && Math.max(S.cw, S.ch) > 2560);
    $('#sizeinfo').textContent = '输出 ' + sizeText();
    $('#minfo').innerHTML = [pts ? '<span class="chip c-pts">需积分号</span>' : '', m.group === 'video' ? `最长 ${m.duration}s` : '', m.refImages ? `参考图最多 ${m.refImages} 张` : '不支持参考图'].filter(Boolean).join(' · ');
  }
  function drawRes() {
    const m = cur();
    if (!m) return;
    const ptsRes = m.pointsRes || [];
    const resolutions = m.resolutions || ['1K'];
    if (!resolutions.includes(S.res)) S.res = m.group === 'video' ? (resolutions.includes('720p') ? '720p' : resolutions[0]) : (resolutions.includes('1K') ? '1K' : resolutions[0]);
    $('#res').innerHTML = resolutions.map(r => `<button data-r="${r}" class="${!S.custom && r === S.res ? 'on' : ''}">${r}${ptsRes.includes(r) ? '<em>积分</em>' : ''}</button>`).join('');
    $('#res').querySelectorAll('button').forEach(b => b.onclick = () => { S.res = b.dataset.r; S.custom = false; drawRes(); drawAsp(); drawInfo(); });
  }
  const QUAL_NAMES = { low: '低', medium: '标准', high: '高', extraHigh: '超高', max: '极致' };
  function qualOptions(m) {
    return (m.qualities || []).map(q => [q, QUAL_NAMES[q] || q]);
  }
  function drawQual() {
    const list = qualOptions(cur());
    $('#qbox').classList.toggle('hidden', !list.length);
    if (!list.find(q => q[0] === S.q)) S.q = 'medium';
    $('#qual').innerHTML = list.map(([v, t]) => `<button data-q="${v}" class="${v === S.q ? 'on' : ''}">${t}</button>`).join('');
    $('#qual').querySelectorAll('button').forEach(b => b.onclick = () => { S.q = b.dataset.q; drawQual(); });
  }
  function drawAsp() {
    const m = cur();
    if (!m) return;
    const aspects = m.aspects || ['1:1'];
    const list = ((m.autoAspect && MODES[S.mode][2]) ? ['auto'] : []).concat(aspects);
    if (!S.custom && !list.includes(S.asp)) S.asp = m.group === 'video' ? '16:9' : (list.includes('auto') ? 'auto' : '1:1');
    const okCustom = canCustom(m);
    if (!okCustom) S.custom = false;
    const sizes = m.sizes || {};
    $('#asp').innerHTML = list.map(a => {
      const px = a !== 'auto' && (sizes[S.res] || {})[a];
      return `<button data-a="${a}" class="${!S.custom && a === S.asp ? 'on' : ''}" title="${px ? px[0] + '×' + px[1] : ''}">${a === 'auto' ? '<i class="auto">A</i>' : ratioBox(a)}<span>${a === 'auto' ? '自动' : a}</span></button>`;
    }).join('') + (okCustom ? `<button data-a="custom" class="${S.custom ? 'on' : ''}"><i class="auto">⤢</i><span>自定义</span></button>` : '');
    $('#asp').querySelectorAll('button').forEach(b => b.onclick = () => {
      if (b.dataset.a === 'custom') S.custom = true; else { S.custom = false; S.asp = b.dataset.a; }
      drawRes(); drawAsp(); drawInfo();
    });
    $('#cbox').classList.toggle('hidden', !S.custom);
    $('#cw').value = S.cw; $('#ch').value = S.ch;
  }
  function drawAll() {
    const [, grp, need] = MODES[S.mode];
    document.querySelectorAll('#mtabs button').forEach(b => b.classList.toggle('on', b.dataset.m === S.mode));
    const list = modelsFor(S.mode);
    if (!list.length) {
      $('#model').innerHTML = '<option value="">暂无可用模型</option>';
      S.model = '';
      return;
    }
    if (!list.find(m => m.id === S.model)) S.model = list[0].id;
    $('#model').innerHTML = list.map(m => `<option value="${m.id}">${esc(m.label || m.name || m.id)}</option>`).join('');
    $('#model').value = S.model;
    const m = cur();
    $('#vbox').classList.toggle('hidden', grp !== 'video');
    $('#refbox').classList.toggle('hidden', !need);
    $('#cntlbl').textContent = grp === 'video' ? '生成数量（视频较慢）' : '生成数量';
    $('#go').textContent = need ? '上传参考图并生成' : '立即生成';
    if (!need) REFS.length = 0;
    while (m && REFS.length > m.refImages) REFS.pop();
    if (grp === 'video' && m) { $('#dur').max = m.duration || 10; $('#dur').value = Math.min(parseInt($('#dur').value || '5', 10), m.duration || 10) || 5; }
    drawRes(); drawQual(); drawAsp(); drawInfo(); drawRefs();
  }
  document.querySelectorAll('#mtabs button').forEach(b => b.onclick = () => { S.mode = b.dataset.m; drawAll(); });
  $('#model').onchange = () => { S.model = $('#model').value; drawAll(); };
  $('#cw').oninput = () => { S.cw = parseInt($('#cw').value || '0', 10); drawInfo(); };
  $('#ch').oninput = () => { S.ch = parseInt($('#ch').value || '0', 10); drawInfo(); };
  $('#ck').value = localStorage.getItem('gen_ck') || '';
  $('#adv').open = !!$('#ck').value;
  // Bind actions before first paint so a draw crash never leaves 立即生成 dead
  $('#clr').onclick = () => { jobs.length = 0; render(); };
  $('#go').onclick = submit;
  try { drawAll(); } catch (e) { console.error('generate drawAll', e); toast('生图页初始化失败：' + (e && e.message || e), true); }
  const sleep = (ms) => new Promise(r => setTimeout(r, ms));
  function extractJobUrls(d) {
    const out = [];
    const add = (u) => {
      if (typeof u === 'string' && u.trim() && !out.includes(u)) out.push(u.trim());
    };
    if (!d || typeof d !== 'object') return out;
    if (Array.isArray(d.image_urls)) d.image_urls.forEach(add);
    if (Array.isArray(d.urls)) d.urls.forEach(add);
    if (Array.isArray(d.media_urls)) d.media_urls.forEach(add);
    add(d.url);
    const nest = [];
    if (d.result && typeof d.result === 'object') {
      nest.push(d.result);
      add(d.result.url);
      if (Array.isArray(d.result.data)) nest.push(...d.result.data);
      else if (d.result.data) nest.push(d.result.data);
    }
    if (Array.isArray(d.data)) nest.push(...d.data);
    else if (d.data && typeof d.data === 'object') nest.push(d.data);
    for (const item of nest) {
      if (!item) continue;
      if (typeof item === 'string') add(item);
      else if (typeof item === 'object') {
        add(item.url); add(item.image_url);
        if (Array.isArray(item.urls)) item.urls.forEach(add);
      }
    }
    return out;
  }
  function applyJobDone(j, d, t0) {
    d = d || {};
    const urls = extractJobUrls(d);
    const stRaw = String(d.status ?? d.state ?? '').toLowerCase();
    const doneOk = /^(completed|succeeded|success|done)$/.test(stRaw);
    const failed = /^(failed|error|cancelled|canceled)$/.test(stRaw);
    let httpStatus = null;
    if (typeof d.http_status === 'number') httpStatus = d.http_status;
    else if (typeof d.upstream_status === 'number') httpStatus = d.upstream_status;
    else if (typeof d.status === 'number') httpStatus = d.status;
    if (httpStatus == null) {
      if (doneOk || (urls.length && !failed && !d.error)) httpStatus = 200;
      else if (failed || d.error) httpStatus = 0;
      else httpStatus = d.ok ? 200 : 200;
    }
    let err = failed ? (d.error || d.message || stRaw || '失败')
      : (urls.length || doneOk ? (d.error || null) : (d.error || null));
    if (httpStatus >= 400 && !err) err = apiErr(d, httpStatus);
    err = err ? errText(err) : null;
    Object.assign(j, {
      state: 'done',
      status: httpStatus,
      href: d.result_href || urls[0] || j.href,
      urls, kind: d.kind || j.kind,
      account: d.account, error: err,
      ms: d.elapsed_ms || (t0 ? performance.now() - t0 : j.ms),
      at: Date.now(), recId: d.record_id || d.task_id || d.id || j.recId,
    });
  }
  function trimJobs() {
    const run = jobs.filter(j => j.state === 'run');
    const done = jobs.filter(j => j.state !== 'run');
    jobs.length = 0;
    jobs.push(...run, ...done.slice(0, Math.max(0, GALLERY_MAX - run.length)));
  }
  function jobTime(j) {
    const t = j.at || j.finishedAt || j.createdAt;
    return t ? fmtFull(t).slice(11, 19) : (j.ms ? (j.ms / 1000).toFixed(1) + 's' : '…');
  }
  function render() {
    const bx = $('#jobs');
    if (!bx) return;
    trimJobs();
    if (!jobs.length) { bx.innerHTML = '<div class="hint" style="grid-column:1/-1;text-align:center;padding:50px 0">暂无任务</div>'; return; }
    bx.innerHTML = jobs.map((j, i) => {
      let th;
      const waiting = j.state === 'run' || (!(j.urls && j.urls.length) && !j.error && j.status >= 200 && j.status < 400);
      if (waiting) th = '<div class="spin"></div>';
      else if (j.urls && j.urls.length) th = isVideoUrl(j.urls[0], j) ? `<video src="${esc(j.urls[0])}" muted preload="metadata"></video>` : `<img src="${esc(j.urls[0])}" />`;
      else th = `<div style="color:var(--err)">${esc(httpLabel(j.status))}<br>${esc(friendlyErr(j.error || '').slice(0, 110))}</div>`;
      const ok = !waiting && j.status >= 200 && j.status < 300 && !j.error && j.urls && j.urls.length;
      const chip = waiting ? 'c-busy' : ok ? 'c-ok' : 'c-invalid';
      return `<div class="job" data-i="${i}" title="点击查看"><div class="thumb">${th}</div><div class="meta"><div class="p">${esc(j.prompt)}</div>
        <div style="display:flex;justify-content:space-between;color:var(--muted)"><span class="mono">${jobTime(j)}</span><span class="chip ${chip}">${waiting ? '进行中' : httpLabel(j.status, !!ok)}</span></div>
        <div style="color:var(--muted);margin-top:3px">${esc(j.model)} · ${esc(j.desc)} · ${j.ms ? (j.ms / 1000).toFixed(1) + 's' : '...'}</div></div></div>`;
    }).join('');
    bx.querySelectorAll('.job[data-i]').forEach(el => { el.onclick = () => openJob(jobs[+el.dataset.i]); });
  }
  function openJob(j) {
    if (!j) return;
    const urls = (j.urls && j.urls.length ? j.urls : (j.href ? [j.href] : [])).filter(Boolean);
    const primary = urls[0] || '';
    const media = !primary
      ? `<div class="hint" style="color:#cbd5e1;padding:28px">${j.state === 'run' ? '还在生成…' : esc(friendlyErr(j.error || '还没有结果'))}</div>`
      : (isVideoUrl(primary, j)
        ? `<video src="${esc(primary)}" controls autoplay playsinline></video>`
        : `<img src="${esc(primary)}" alt="" />`);
    const rows = urls.map((u, i) => `<div class="media-link"><code title="${esc(absUrl(u))}">${urls.length > 1 ? (i + 1) + '. ' : ''}${esc(absUrl(u))}</code><button class="btn" data-cp="${i}">复制链接</button><button class="btn" data-dl="${i}">下载</button></div>`).join('');
    const m = modal(`<div class="media-pop">
      <h3>${esc(j.prompt || '任务')}</h3>
      <div class="media-stage">${media}</div>
      <div class="hint" style="margin:8px 0 0">${esc(j.model || '')}${j.desc ? ' · ' + esc(j.desc) : ''}</div>
      ${rows}
      <div class="foot"><button class="btn primary" id="cl">关闭</button></div>
    </div>`, box => {
      $('#cl', box).onclick = () => box.remove();
      box.querySelectorAll('[data-cp]').forEach(b => b.onclick = () => copyText(absUrl(urls[+b.dataset.cp])));
      box.querySelectorAll('[data-dl]').forEach(b => b.onclick = () => downloadUrl(urls[+b.dataset.dl]));
    });
    return m;
  }
  function recToJob(r) {
    const urls = r.imageUrls || [];
    const waiting = r.state === 'running' || r.running || (!urls.length && !r.error && r.status >= 200 && r.status < 400);
    return {
      recId: r.id, taskId: r.upstream_task_id || r.task_id || '', prompt: r.prompt, model: r.model, desc: r.size || '',
      state: waiting ? 'run' : 'done', status: r.status,
      href: urls[0] || '', urls, kind: r.kind || (/seedance|veo|omni|video|vid_/.test(r.model || '') || r.api_type === 'video' ? 'video' : 'image'),
      at: r.finishedAt || r.createdAt, createdAt: r.createdAt, finishedAt: r.finishedAt,
      ms: r.elapsedMs, error: waiting ? '' : r.error, account: r.account,
    };
  }
  function jobKey(j) {
    return String(j.taskId || j.upstream_task_id || j.recId || '');
  }
  function preferJob(a, b) {
    const am = !!(a.urls && a.urls.length), bm = !!(b.urls && b.urls.length);
    if (am !== bm) return am ? a : b;
    if ((a.state === 'run') !== (b.state === 'run')) return a.state === 'run' ? b : a;
    return (a.at || 0) >= (b.at || 0) ? a : b;
  }
  async function loadGallery() {
    try {
      let items = [];
      try {
        const g = await api('/api/generate/gallery?limit=' + GALLERY_MAX, { soft: true });
        items = (g && (g.items || g.data)) || [];
      } catch (_) {}
      if (!items.length) {
        const d = await api('/api/records?page_size=' + GALLERY_MAX, { soft: true });
        items = ((d && d.items) || []).map(normRecord);
      } else {
        items = items.filter(x => !isChatRecord(x)).map(x => normRecord(Object.assign({}, x, {
          created_at: x.created_at || x.createdAt,
          media_urls: x.media_urls || x.urls || x.imageUrls || (x.url ? [x.url] : []),
          prompt_preview: x.prompt || x.prompt_preview,
          status: x.status,
          state: x.running ? 'running' : x.state,
          upstream_task_id: x.upstream_task_id,
        })));
      }
      items = items.filter(r => !isChatRecord(r));
      const merged = [];
      const seen = new Map();
      const put = (j) => {
        if (!j) return;
        if (j.state !== 'run' && !(j.urls && j.urls.length) && !j.error) return;
        const key = jobKey(j);
        if (!key) { merged.push(j); return; }
        if (!seen.has(key)) { seen.set(key, merged.length); merged.push(j); return; }
        merged[seen.get(key)] = preferJob(merged[seen.get(key)], j);
      };
      jobs.filter(j => j.state === 'run' || (j.urls && j.urls.length) || j.error).forEach(put);
      items.map(recToJob).forEach(put);
      merged.sort((a, b) => (a.state === 'run') === (b.state === 'run') ? (b.at || 0) - (a.at || 0) : (a.state === 'run' ? -1 : 1));
      jobs.length = 0;
      jobs.push(...merged);
      render();
    } catch (_) { render(); }
  }
  async function watchTask(j, tid, t0, kind) {
    j.recId = tid;
    const deadline = Date.now() + 20 * 60 * 1000;
    const pollPath = kind === 'video' ? ('/v1/videos/' + tid) : ('/v1/images/tasks/' + tid);
    while (j.state === 'run' && Date.now() < deadline) {
      await sleep(1500);
      if (j.state !== 'run') return;
      let t;
      try { t = await v1api(pollPath, { soft: true }); } catch (_) { continue; }
      if (!t) continue;
      // Local auth/proxy HTTP failures are not task terminal states.
      if (typeof t.http_status === 'number' && t.http_status >= 400) {
        if (t.http_status === 401 || t.http_status === 403) {
          applyJobDone(j, { status: 'error', error: errText(t.error) || ('本地鉴权失败 ' + t.http_status + '（控制台 Token 失效，请刷新页面重试）'), http_status: t.http_status }, t0);
          render();
          return;
        }
        if (t.http_status === 429) {
          applyJobDone(j, { status: 'error', error: '请求过频 429，已停止轮询', http_status: 429 }, t0);
          render();
          return;
        }
        continue;
      }
      const st = String(t.status || t.state || '').toLowerCase();
      if (!st || st === 'queued' || st === 'running' || st === 'in_progress' || st === 'processing' || st === '401' || st === '403') continue;
      if (/^(completed|succeeded|success|done)$/.test(st) && !extractJobUrls(t).length) continue;
      applyJobDone(j, Object.assign({}, t, { http_status: t.http_status || 200 }), t0);
      render();
      loadGallery();
      return;
    }
    if (j.state === 'run') {
      try {
        const r0 = await api('/api/records/' + tid, { soft: true });
        const r = normRecord((r0 && r0.item) || r0);
        if (r && r.state && r.state !== 'running') { Object.assign(j, recToJob(r)); render(); return; }
      } catch (_) {}
      Object.assign(j, { state: 'done', error: '等待结果超时，请到生成记录查看', at: Date.now(), ms: performance.now() - t0 });
      render();
    }
  }
  async function submit() {
    const prompt = $('#prompt').value.trim();
    if (!prompt) return toast('请输入提示词', true);
    const m = cur(), need = MODES[S.mode][2];
    if (!m) return toast('请选择模型', true);
    if (need && !REFS.length) return toast('请先添加参考图', true);
    if (S.custom && (!(S.cw >= 256 && S.cw <= 3840 && S.cw % 16 === 0) || !(S.ch >= 256 && S.ch <= 3840 && S.ch % 16 === 0))) return toast('自定义尺寸需为 16 的倍数，范围 256–3840', true);
    const n = Math.min(8, Math.max(1, parseInt($('#count').value || '1', 10)));
    const ck = $('#ck').value.trim();
    localStorage.setItem('gen_ck', ck);
    localStorage.setItem('gen_sel', JSON.stringify({ mode: S.mode, model: S.model, res: S.res, asp: S.asp, custom: S.custom, cw: S.cw, ch: S.ch, q: S.q }));
    const isVid = m.group === 'video';
    const base = { model: m.id, prompt };
    if (S.custom) base.size = `${S.cw}x${S.ch}`; else { if (S.res) base.resolution = S.res; if (S.asp) base.aspect_ratio = S.asp; base.aspect = S.asp; }
    const qual = qualOptions(m).length && S.q !== 'medium' ? S.q : '';
    if (qual) base.quality = qual;
    if (ck) base.cookie = ck;
    if (need) { base.images = REFS.map(r => r.data); }
    if (isVid) { base.duration = parseInt($('#dur').value || '5', 10); base.audio = $('#aud').checked; if ($('#neg').value.trim()) base.negative_prompt = $('#neg').value.trim(); }
    const path = isVid ? '/v1/videos' : (need ? '/v1/images/edits' : '/v1/images/tasks');
    const kind = isVid ? 'video' : 'image';
    const desc = `${S.custom ? S.cw + '×' + S.ch : S.res + ' ' + S.asp}${qual ? ' ' + qual : ''}${need ? ' · 参考图' + REFS.length : ''}`;
    for (let i = 0; i < n; i++) {
      const j = { prompt, model: m.id, desc, state: 'run', at: Date.now(), kind };
      jobs.unshift(j);
      const t0 = performance.now();
      v1api(path, { method: 'POST', body: base, soft: true }).then(d => {
        d = d || {};
        const tid = d.task_id || d.id || d.record_id || (d.data && (d.data.id || d.data.task_id));
        if (!tid) {
          // sync response with urls
          if (extractJobUrls(d).length) {
            applyJobDone(j, d, t0); render(); loadGallery(); return;
          }
          applyJobDone(j, { error: apiErr(d, d.http_status || 400) || '未拿到任务 ID', http_status: d.http_status || 0, ok: false }, t0);
          render();
          return;
        }
        j.recId = tid;
        j.taskId = tid;
        render();
        return watchTask(j, tid, t0, kind);
      }).catch(e => { Object.assign(j, { state: 'done', error: String(e), ms: performance.now() - t0, at: Date.now() }); render(); });
    }
    render();
  }
  await loadGallery();
  timer = setInterval(() => { if (jobs.some(j => j.state === 'run')) loadGallery(); }, 2000);
}

/* ---------- chat ---------- */
const CHAT = { model: '', messages: [], busy: false };

async function pageChat() {
  const main = $('#main');
  main.innerHTML = head('chat', '<button class="btn" id="cnew">新对话</button>') + `
  <div class="card" style="display:flex;flex-direction:column;height:calc(100vh - 178px);padding:12px">
    <div class="tools" style="margin-bottom:8px">
      <div class="l"><select id="cm"></select><span class="hint" id="cst" style="margin:0"></span></div>
      <div class="r"><label class="inl"><input type="checkbox" id="cstream" checked /> 流式输出</label></div>
    </div>
    <div id="cmsgs" style="flex:1;overflow:auto;padding:10px;display:flex;flex-direction:column;gap:12px;background:var(--bg,#f8fafc);border:1px solid var(--line,#e2e8f0);border-radius:10px"></div>
    <div style="display:flex;gap:8px;margin-top:10px;align-items:stretch">
      <textarea id="cin" rows="2" placeholder="输入消息，Enter 发送，Shift+Enter 换行" style="flex:1;resize:none"></textarea>
      <button class="btn primary" id="csend" style="padding:0 18px">发送</button>
    </div>
  </div>`;

  const box = $('#cmsgs');
  const input = $('#cin');
  const sendBtn = $('#csend');

  const draw = () => {
    box.innerHTML = CHAT.messages.length ? CHAT.messages.map(m => `
      <div style="display:flex;justify-content:${m.role === 'user' ? 'flex-end' : 'flex-start'}">
        <div style="max-width:82%;padding:10px 12px;border-radius:10px;white-space:pre-wrap;word-break:break-word;
          background:${m.role === 'user' ? '#2563eb' : '#ffffff'};color:${m.role === 'user' ? '#fff' : '#0f172a'};
          border:1px solid ${m.role === 'user' ? '#2563eb' : '#e2e8f0'};font-size:13.5px;line-height:1.6">${esc(m.content || '')}${
          m.error ? `<div style="color:#dc2626;margin-top:6px;font-size:12px">${esc(m.error)}</div>` : ''
        }${m.streaming ? '<span style="opacity:.55">▍</span>' : ''}</div></div>`).join('')
      : '<div class="hint" style="margin:auto;text-align:center">还没有消息，问点什么吧（模型：muse-spark）</div>';
    box.scrollTop = box.scrollHeight;
  };

  const setBusy = (b) => { CHAT.busy = b; sendBtn.disabled = b; sendBtn.textContent = b ? '生成中…' : '发送'; };

  $('#cnew').onclick = () => { CHAT.messages = []; draw(); input.focus(); };
  draw();

  try {
    const d = await api('/api/generate/models', { soft: true });
    const all = (d && d.data) || [];
    const chatModels = all.filter(m => !/image|video|seedance|veo|omni/i.test(m.id || ''));
    const sel = $('#cm');
    sel.innerHTML = (chatModels.length ? chatModels : all).map(m => `<option value="${esc(m.id)}">${esc(m.id)}</option>`).join('');
    CHAT.model = sel.value || 'muse-spark';
    sel.onchange = () => { CHAT.model = sel.value; };
  } catch (e) { $('#cst').textContent = '模型列表加载失败：' + e.message; }

  async function send() {
    const text = input.value.trim();
    if (!text || CHAT.busy) return;
    input.value = '';
    CHAT.messages.push({ role: 'user', content: text });
    const reply = { role: 'assistant', content: '', streaming: true };
    CHAT.messages.push(reply);
    draw();
    setBusy(true);
    $('#cst').textContent = '';
    try {
      const tok = await ensureConsoleToken();
      const stream = $('#cstream').checked;
      const body = { model: CHAT.model || 'muse-spark', stream, messages: CHAT.messages.filter(m => !m.error && !m.streaming).map(m => ({ role: m.role, content: m.content })) };
      const r = await fetch('/v1/chat/completions', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + tok },
        body: JSON.stringify(body),
      });
      if (!r.ok) {
        const t = await r.text();
        throw new Error(apiErr((() => { try { return JSON.parse(t); } catch (_) { return { message: t }; } })(), r.status));
      }
      if (!stream) {
        const d = await r.json();
        reply.content = (d.choices && d.choices[0] && d.choices[0].message && d.choices[0].message.content) || '';
        reply.streaming = false;
        draw();
      } else {
        const reader = r.body.getReader();
        const dec = new TextDecoder();
        let buf = '';
        let streamError = '';
        const take = (line) => {
          const t = line.trim();
          if (!t.startsWith('data:')) return;
          const payload = t.slice(5).trim();
          if (!payload || payload === '[DONE]') return;
          const ev = JSON.parse(payload);
          const delta = ev.choices && ev.choices[0] && (ev.choices[0].delta || ev.choices[0].message);
          if (delta && typeof delta.content === 'string') { reply.content += delta.content; draw(); }
          if (ev.error) streamError = (ev.error.message || JSON.stringify(ev.error));
        };
        for (;;) {
          const { done, value } = await reader.read();
          if (done) break;
          buf += dec.decode(value, { stream: true });
          const parts = buf.split('\n');
          buf = parts.pop();
          for (const line of parts) {
            try { take(line); } catch (e) { if (!(e instanceof SyntaxError)) streamError = String(e.message || e); }
          }
          if (streamError) break;
        }
        if (buf.trim()) {
          try { take(buf); } catch (e) { if (!(e instanceof SyntaxError)) streamError = String(e.message || e); }
        }
        reply.streaming = false;
        draw();
        if (streamError) throw new Error(streamError);
      }
      if (!reply.content) { reply.content = '（空回复）'; draw(); }
    } catch (e) {
      reply.streaming = false;
      reply.error = String(e.message || e);
      draw();
    } finally {
      setBusy(false);
      input.focus();
    }
  }

  sendBtn.onclick = send;
  input.onkeydown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); }
  };
}

/* ---------- accounts ---------- */
const AS = { status: 'all', q: '', sort: 'imported', page: 1, size: parseInt(localStorage.getItem('pgsize') || '12', 10), sel: new Set(), items: [], total: 0, pages: 1 };

async function pageAccounts() {
  const main = $('#main');
  main.innerHTML = head('accounts', '<button class="btn" id="rf">刷新</button><button class="btn primary" id="imp">导入账号</button>') + `
  <div class="chips" id="asum" style="margin-bottom:12px"></div>
  <div class="card">
    <div class="tools">
      <div class="l">
        <select id="fs"><option value="all">状态：全部</option><option value="ok">可用</option><option value="pending_vm">已注册待分配</option><option value="unknown">未检测</option><option value="invalid">异常</option><option value="disabled">已停用</option></select>
        <select id="so"><option value="imported">按导入时间</option><option value="used">按最近使用</option><option value="success">按成功数</option><option value="name">按名称</option></select>
        <input type="text" id="q" placeholder="搜索 邮箱 / 名称 / ID / 备注" />
      </div>
      <div class="r">
        <button class="btn" data-b="credits">刷新额度</button>
        <button class="btn" data-b="check">检测选中</button>
        <button class="btn" data-x="sel">导出选中</button>
        <button class="btn" data-x="all">导出全部</button>
        <button class="btn" data-b="enable">启用</button>
        <button class="btn" data-b="disable">停用</button>
        <button class="btn" data-b="reset">解除冷却</button>
        <button class="btn danger" data-b="delete">删除</button>
      </div>
    </div>
    <div class="tablewrap"><table class="acc-table">
      <thead><tr><th style="width:36px"><input type="checkbox" id="all" /></th><th>标签</th><th>账号</th><th class="hide-m">ID</th><th>状态</th><th>有效期</th><th>额度</th><th>启用</th><th>Cookie</th><th class="hide-m">最后使用</th><th style="min-width:220px">操作</th></tr></thead>
      <tbody id="tb"></tbody></table></div>
    <div class="pager">
      <span id="info"></span>
      <div class="mid"><button class="btn" id="p1">首页</button><button class="btn" id="pp">上一页</button><button class="btn" id="pn">下一页</button><button class="btn" id="pl">尾页</button></div>
      <div class="mid">每页 <input type="number" id="sz" min="1" max="200" /> 条 <button class="btn" id="svsz">保存</button></div>
    </div>
  </div>`;
  $('#fs').value = AS.status; $('#so').value = AS.sort; $('#q').value = AS.q; $('#sz').value = AS.size;
  $('#fs').onchange = () => { AS.status = $('#fs').value; AS.page = 1; load(); };
  $('#so').onchange = () => { AS.sort = $('#so').value; AS.page = 1; load(); };
  let qt; $('#q').oninput = () => { clearTimeout(qt); qt = setTimeout(() => { AS.q = $('#q').value.trim(); AS.page = 1; load(); }, 300); };
  $('#rf').onclick = load; $('#imp').onclick = importDlg;
  $('#p1').onclick = () => { AS.page = 1; load(); };
  $('#pp').onclick = () => { AS.page = Math.max(1, AS.page - 1); load(); };
  $('#pn').onclick = () => { AS.page = Math.min(AS.pages, AS.page + 1); load(); };
  $('#pl').onclick = () => { AS.page = AS.pages; load(); };
  $('#svsz').onclick = () => { AS.size = Math.min(200, Math.max(1, parseInt($('#sz').value || '12', 10))); localStorage.setItem('pgsize', AS.size); AS.page = 1; load(); };
  $('#all').onchange = (e) => { AS.items.forEach(a => e.target.checked ? AS.sel.add(a.id) : AS.sel.delete(a.id)); draw(); };
  document.querySelectorAll('[data-b]').forEach(b => b.onclick = () => batch(b.dataset.b));
  document.querySelectorAll('[data-x]').forEach(b => b.onclick = () => {
    if (b.dataset.x === 'all') location.href = '/api/accounts/export?all=1';
    else if (!AS.sel.size) toast('请先勾选账号', true);
    else location.href = '/api/accounts/export?ids=' + [...AS.sel].join(',');
  });

  async function loadSum(stats, total) {
    const a = stats || {};
    $('#asum').innerHTML = [
      ['总计', total != null ? total : (a.total || 0), 'c-disabled'],
      ['可用', a.ok || a.active || a.healthy || 0, 'c-ok'],
      ['待分配', a.pending_vm || 0, 'c-pending_vm'],
      ['使用中', a.busy || 0, 'c-busy'],
      ['冷却', a.cooling || 0, 'c-cooling'],
      ['额度用尽', a.no_quota || 0, 'c-no_quota'],
      ['失效', a.invalid || 0, 'c-invalid'],
      ['停用', a.disabled || 0, 'c-disabled'],
    ].map(([k, v, c]) => `<span class="chip ${c}">${k} ${v}</span>`).join('');
  }
  async function load() {
    const qs = new URLSearchParams({ status: AS.status, q: AS.q, page: AS.page, size: AS.size, page_size: AS.size });
    let d; try { d = await api('/api/accounts?' + qs); } catch (_) { return; }
    AS.items = (d.items || []).map(normAccount); AS.total = d.total; AS.pages = d.pages; AS.page = d.page;
    draw();
    loadSum(d.stats || {}, d.total);
  }
  function draw() {
    $('#tb').innerHTML = AS.items.map(a => {
      const st = a.busy
        ? '<span class="chip c-busy">使用中</span>'
        : `<span class="chip c-${a.status || 'unknown'}" title="${esc(a.lastError || a.note || '')}">${STATUS[a.status] || a.status || '未检测'}</span>`;
      const miss = a.essential_ok === false ? ' <span class="chip c-invalid" title="缺少核心 Cookie">缺核心</span>' : '';
      const en = `<label class="en-sw" title="${a.enabled ? '点击停用' : '点击启用'}"><input type="checkbox" data-en="${a.id}" ${a.enabled ? 'checked' : ''} /><span>${a.enabled ? '开' : '关'}</span></label>`;
      const ck = a.hasCookie
        ? `<span class="mono" title="Cookie 条数">${a.cookie_count}</span>${miss}`
        : `<span class="sub">无</span>${miss}`;
      return `<tr>
        <td><input type="checkbox" data-id="${a.id}" ${AS.sel.has(a.id) ? 'checked' : ''}></td>
        <td class="acc-cell"><b>${esc(a.tag || a.label || '—')}</b></td>
        <td class="acc-cell">${esc(a.account || a.email || a.label || '—')}</td>
        <td class="hide-m mono">${esc(a.id)}</td>
        <td>${st}</td>
        <td>${expiryBadge(a)}</td>
        <td>${quotaBadge(a)}</td>
        <td>${en}</td>
        <td>${ck}</td>
        <td class="hide-m mono" title="${a.last_used ? fmtTime(tsMs(a.last_used)) : ''}">${agoUnix(a.last_used)}</td>
        <td><div class="acts">
          <button class="ab" data-a="check" data-id="${a.id}" title="检测 Cookie 并刷新额度">检测</button>
          <button class="ab" data-a="quota" data-id="${a.id}" title="查询额度">额度</button>
          <button class="ab" data-a="edit" data-id="${a.id}">编辑</button>
          <button class="ab" data-a="reset" data-id="${a.id}" title="重新检测 / 恢复">恢复</button>
          <button class="ab danger" data-a="del" data-id="${a.id}">删除</button>
        </div></td></tr>`;
    }).join('') || '<tr><td colspan="11" style="padding:40px;color:var(--muted)">暂无账号，点击右上角「导入账号」</td></tr>';
    $('#info').textContent = `第 ${AS.page} / ${AS.pages} 页，共 ${AS.total} 条` + (AS.sel.size ? ` · 已选 ${AS.sel.size}` : '');
    $('#all').checked = AS.items.length > 0 && AS.items.every(a => AS.sel.has(a.id));
    document.querySelectorAll('#tb input[type=checkbox][data-id]').forEach(c => c.onchange = () => { c.checked ? AS.sel.add(c.dataset.id) : AS.sel.delete(c.dataset.id); draw(); });
    document.querySelectorAll('#tb input[data-en]').forEach(c => c.onchange = async () => {
      const id = c.dataset.en; const a = AS.items.find(x => x.id === id);
      try { await api('/api/accounts/' + id, { method: 'PATCH', body: { enabled: !!c.checked } }); load(); }
      catch (e) { toast(e.message, true); if (a) c.checked = !!a.enabled; }
    });
    document.querySelectorAll('#tb .ab').forEach(b => b.onclick = () => rowAction(b.dataset.a, b.dataset.id));
  }
  async function rowAction(act, id) {
    const a = AS.items.find(x => x.id === id);
    try {
      if (act === 'check') {
        toast('检测中…');
        const r = await api(`/api/accounts/${id}/check`, { method: 'POST' });
        const pending = (r.note || r.message || '工作区尚未分配（等 VM 供货）').replace(/^登录态有效[，,·]?\s*/, '');
        toast(r.ok
          ? (r.assigned === false ? ('登录态有效 · ' + pending) : '登录有效，工作区已分配')
          : ('检测失败: ' + (r.error || r.message || r.status)), !r.ok);
      }
      else if (act === 'quota') { toast('查询额度…'); const r = await api(`/api/accounts/${id}/quota`, { method: 'POST' }); toast(r.found ? (`额度：${r.plan || '?'}，周用量已用 ${r.weekly_used_pct ?? '?'}%`) : ('未读到额度' + (r.error || r.message ? ': ' + (r.error || r.message) : '')), !r.found); }
      else if (act === 'toggle') await api('/api/accounts/' + id, { method: 'PATCH', body: { enabled: !a.enabled } });
      else if (act === 'reset') await api('/api/accounts/batch', { method: 'POST', body: { ids: [id], action: 'reset' } });
      else if (act === 'del') { if (!await ask('删除账号 ' + (a.label || a.name) + ' ？')) return; await api('/api/accounts/batch', { method: 'POST', body: { ids: [id], action: 'delete' } }); AS.sel.delete(id); }
      else if (act === 'edit') return editDlg(a);
      load();
    } catch (e) { toast(e.message, true); }
  }
  async function batch(action) {
    if (action === 'credits' && !AS.sel.size) {
      try {
        toast('查询额度中，请稍候…');
        const r = await api('/api/accounts/batch', { method: 'POST', body: { all: true, action: 'credits' } });
        toast(`额度已刷新 ${r.valid} · 失败 ${r.failed || r.invalid || 0}`);
        load();
      } catch (e) { toast(e.message, true); }
      return;
    }
    if (!AS.sel.size) return toast('请先勾选账号', true);
    if (action === 'delete' && !await ask(`删除选中的 ${AS.sel.size} 个账号？`)) return;
    try {
      if (action === 'check' || action === 'credits') toast('处理中，请稍候…');
      const r = await api('/api/accounts/batch', { method: 'POST', body: { ids: [...AS.sel], action } });
      toast(action === 'check' ? `有效 ${r.valid} · 失效 ${r.invalid}` : action === 'credits' ? `额度已刷新 ${r.valid} · 失败 ${r.failed || r.invalid || 0}` : `已处理 ${r.affected}`);
      if (action === 'delete') AS.sel.clear();
      load();
    } catch (e) { toast(e.message, true); }
  }
  function importDlg() {
    modal(`<h3>导入账号</h3>
      <p class="hint" style="margin:0 0 8px">每行一个账号：<code>Cookie</code>、<code>备注名----Cookie</code> 或 <code>label | Cookie</code>。导入后推送到上游 muse2api 号池；完整 Cookie 不会回显到表格。</p>
      <div style="display:flex;gap:8px;align-items:center;margin-bottom:6px"><button class="btn" id="pick">选择文件导入（txt / csv）</button><span class="hint" id="fn" style="margin:0"></span><input type="file" id="ff" accept=".txt,.csv,.json,text/plain" class="hidden" /></div>
      <textarea id="txt" style="min-height:220px" placeholder="粘贴账号 Cookie…"></textarea>
      <label class="f" style="display:flex;gap:8px;align-items:center;cursor:pointer"><input type="checkbox" id="rep" /> 覆盖现有号池（先清空）</label>
      <div class="foot"><button class="btn" id="cx">取消</button><button class="btn primary" id="ok">导入</button></div>`, m => {
      $('#pick', m).onclick = () => $('#ff', m).click();
      $('#ff', m).onchange = async (e) => { const f = e.target.files[0]; if (!f) return; const tx = await f.text(); $('#txt', m).value = ($('#txt', m).value ? $('#txt', m).value.replace(/\s+$/, '') + '\n' : '') + tx; $('#fn', m).textContent = f.name + ' · ' + tx.split(/\r?\n/).filter(x => x.trim()).length + ' 行'; };
      $('#cx', m).onclick = () => m.remove();
      $('#ok', m).onclick = async () => {
        try {
          const r = await api('/api/accounts/import', { method: 'POST', body: { text: $('#txt', m).value, replace: $('#rep', m).checked } });
          toast(`新增 ${r.added}，重复 ${r.duplicate}，现有 ${r.total}`); m.remove(); AS.page = 1; load();
        } catch (e) { toast(e.message, true); }
      };
    });
  }
  async function editDlg(a) {
    let full; try { full = await api('/api/accounts/' + a.id); } catch (e) { return toast(e.message, true); }
    full = full.item || full;
    const email0 = full.email || full.account || a.email || a.account || a.label || '';
    const name0 = full.name || a.name || '';
    const pwd0 = full.password || '';
    const ck0 = full.cookie || '';
    const fpText = full.fingerprint_text || (full.fingerprint ? JSON.stringify(full.fingerprint, null, 2) : '') || full.fingerprint_summary || '';
    const fpSummary = full.fingerprint_label || full.fingerprint_summary || full.fingerprint_profile || '无';
    modal(`<h3>编辑账号 <span class="mono" style="color:var(--muted)">${esc(a.id)}</span></h3>
      <label class="f">账号（邮箱）</label><input type="text" id="em" value="${esc(email0)}" placeholder="注册邮箱" />
      <label class="f">名字</label><input type="text" id="nm" value="${esc(name0)}" placeholder="显示名字" />
      <label class="f">密码</label><input type="text" id="pw" value="${esc(pwd0)}" placeholder="${full.has_password ? '已保存，可修改' : '登录密码'}" autocomplete="off" />
      <label class="f">备注</label><input type="text" id="nt" value="${esc(full.note || a.note || '')}" placeholder="可选备注" />
      <label class="f" style="display:flex;justify-content:space-between;align-items:center"><span>Cookie（留空不改；完整粘贴可更换）</span><button class="btn" id="cp" style="height:24px;padding:0 10px;font-size:12px">复制框内</button></label>
      <textarea id="ck" spellcheck="false" style="min-height:120px;word-break:break-all" placeholder="完整 Cookie，留空表示不修改"></textarea>
      <label class="f">注册指纹信息（只读，不可编辑）</label>
      <textarea id="fp" readonly spellcheck="false" style="min-height:110px;word-break:break-all;opacity:.85;background:var(--panel2,#0f1117)">${esc(fpText || fpSummary)}</textarea>
      <div class="hint">Cookie 数: <span class="mono">${esc(String(full.cookie_count ?? a.cookie_count ?? 0))}</span> · 指纹: <span class="mono">${esc(fpSummary)}</span> · 有效期: ${expiryBadge(a)}</div>
      <div class="foot"><button class="btn" id="cx">取消</button><button class="btn primary" id="ok">保存</button></div>`, m => {
      $('#cx', m).onclick = () => m.remove();
      $('#cp', m).onclick = async () => {
        const v = $('#ck', m).value;
        try { await navigator.clipboard.writeText(v); } catch (_) { $('#ck', m).select(); document.execCommand('copy'); }
        toast(v ? 'Cookie 已复制' : '框内为空（服务端不回显完整 Cookie）');
      };
      $('#ok', m).onclick = async () => {
        const ck = $('#ck', m).value.trim();
        const body = {
          email: $('#em', m).value.trim(),
          name: $('#nm', m).value.trim(),
          note: $('#nt', m).value,
          password: $('#pw', m).value,
          label: $('#em', m).value.trim(),
        };
        if (ck) body.cookie = ck;
        try { await api('/api/accounts/' + a.id, { method: 'PATCH', body }); toast('已保存'); m.remove(); load(); }
        catch (e) { toast(e.message, true); }
      };
    });
  }
  await load();
}

/* ---------- settings ---------- */
async function pageSettings() {
  const main = $('#main');
  main.innerHTML = head('settings', '<button class="btn" id="tst">测引擎</button><button class="btn primary" id="save">保存设置</button>') + '<div id="st"></div>';
  let wrap; try { wrap = await api('/api/settings'); } catch (_) { return; }
  const s = wrap.config || wrap;
  const num = (id, label, v, _hint = '', min = 0, max = 100000, step = 1) => `<div><label class="f">${label}</label><input type="number" id="${id}" value="${v}" min="${min}" max="${max}" step="${step}" /></div>`;
  const bool = (id, label, v, off, on) => `<div class="span2"><label class="f">${label}</label><select id="${id}"><option value="false">${off}</option><option value="true">${on}</option></select></div>`;
  const card = (title, sub, body, wide = false) => `<div class="card${wide ? ' wide' : ''}"><div class="scard-h"><h2>${title}</h2>${sub ? `<p>${sub}</p>` : ''}</div>${body}</div>`;
  const svg = d => `<svg viewBox="0 0 24 24">${d}</svg>`;
  const IC = {
    engine: svg('<rect x="4" y="4" width="16" height="16" rx="2.5"/><rect x="9" y="9" width="6" height="6" rx="1"/><path d="M9 1.5V4M15 1.5V4M9 20v2.5M15 20v2.5M1.5 9H4M1.5 15H4M20 9h2.5M20 15h2.5"/>'),
    sched: svg('<circle cx="9" cy="8" r="3.2"/><path d="M3 20c0-3.3 2.7-6 6-6s6 2.7 6 6"/><path d="M16 4.5a3.2 3.2 0 010 6.3M18.5 14.4c1.5.9 2.5 2.6 2.5 4.6"/>'),
    signup: svg('<circle cx="10" cy="8" r="3.5"/><path d="M3.5 20c0-3.6 2.9-6.5 6.5-6.5 1.4 0 2.6.4 3.7 1.1"/><path d="M18 14v6M15 17h6"/>'),
    net: svg('<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c2.5 2.7 3.8 5.7 3.8 9s-1.3 6.3-3.8 9c-2.5-2.7-3.8-5.7-3.8-9S9.5 5.7 12 3z"/>'),
    r2: svg('<path d="M7 18a4.5 4.5 0 01-.6-9A6 6 0 0118 8.5a4.8 4.8 0 01-.5 9.5z"/><path d="M12 11v6M9.5 14.5L12 17l2.5-2.5"/>'),
    sec: svg('<path d="M12 2.5l7.5 3v6c0 4.6-3.2 8.6-7.5 10-4.3-1.4-7.5-5.4-7.5-10v-6z"/><path d="M9 12l2.2 2.2L15.5 10"/>'),
  };
  const groups = [];
  const tab = (id, label, desc, icon, cards) => groups.push({ id, label, desc, icon, cards });
  tab('engine', '引擎与性能', '生成引擎、超时并发与媒体策略', IC.engine, [
    card('生成引擎', '纯协议 Noise 或旧 CDP 引擎，以及对外访问地址', `
      <div class="form-grid">
        <div class="span2"><label class="f">引擎后端</label>
          <select id="engine_backend"><option value="protocol">纯协议 protocol（Noise + chat.stream，无 Chromium）</option><option value="cdp">旧浏览器 cdp / local</option></select></div>
        <div class="span2"><label class="f">Chromium 路径（空=自动探测）</label><input type="text" id="chromium_path" value="${esc(s.chromium_path || '')}" placeholder="/usr/bin/google-chrome" /></div>
        <div><label class="f">CDP 端口</label><input type="number" id="cdp_port" value="${s.cdp_port ?? 19211}" min="1024" max="65535" /></div>
        <div><label class="f">引擎并发</label><input type="number" id="engine_concurrency" value="${s.engine_concurrency ?? 2}" min="1" max="32" /></div>
        <div class="span2"><label class="f">本机公网地址 Public Base（改写媒体链接）</label><input type="text" id="public_base" value="${esc(s.public_base || '')}" placeholder="http://127.0.0.1:8090" /></div>
      </div>`),
    card('超时与并发', '请求生命周期与排队控制', `<div class="form-grid">
      ${num('connect_timeout_sec', '连接超时 (秒)', s.connect_timeout_sec ?? 10, '', 1, 120, 0.5)}
      ${num('request_timeout_sec', '请求超时 (秒)', s.request_timeout_sec ?? 120, '', 5, 600, 1)}
      ${num('stream_idle_timeout_sec', 'SSE 空闲超时 (秒)', s.stream_idle_timeout_sec ?? 60, '', 5, 600, 1)}
      ${num('max_concurrency', '最大并发请求', s.max_concurrency ?? 32, '', 1, 500)}
      ${num('queue_wait_sec', '排队最长等待 (秒)', s.queue_wait_sec ?? 25, '', 0, 600)}
      ${num('record_keep_days', '记录保留 (天)', s.record_keep_days ?? 30, '0 = 不自动清理', 0, 3650)}
    </div>`),
    card('媒体与试调', '媒体链接访问、试调开关与计费口径', `<div class="form-grid two">
      ${bool('media_auth_required', '媒体链接鉴权', !!s.media_auth_required, '关闭：/v1/media 可裸链', '开启：需 Bearer')}
      ${bool('rewrite_media_urls', '改写媒体到本机', s.rewrite_media_urls !== false, '关闭：保留上游 URL', '开启：改写到 Public Base')}
      ${bool('console_generate_enabled', '管理台试调', s.console_generate_enabled !== false, '关闭：生图页不可用', '开启：发短时 Token')}
      ${bool('charge_on_upstream_error', '上游错误也扣 Key 额度', !!s.charge_on_upstream_error, '关闭：仅成功扣减', '开启：失败也计次')}
    </div>`, true),
  ]);
  tab('signup', '注册补号', '注册默认值、并发与每日看门狗', IC.signup, [
    card('注册 / 补号默认', '新注册账号的地区、入池与保活', `<div class="form-grid">
      <div><label class="f">默认地区</label><input type="text" id="default_region" value="${esc(s.default_region || 'US')}" /></div>
      ${bool('default_into_pool', '成功后入池', s.default_into_pool !== false, '只保存任务', '写入本地号池')}
      ${bool('proxy_scheduled_keepalive', '定时保活', !!s.proxy_scheduled_keepalive, '关闭', '开启（本地号池 renew）')}
      <div class="span2"><label class="f">注册客户端标识 Signup Client</label><input type="text" id="signup_client" value="${esc(s.signup_client || '')}" placeholder="协议客户端标识" /></div>
      <div class="span2"><label class="f">Muse 协议基址 Signup Muse Base</label><input type="text" id="signup_muse_base" value="${esc(s.signup_muse_base || '')}" placeholder="可选覆盖" /></div>
    </div>`),
    card('补号系统', '补号并发、间隔与每日定时补号', `<div class="form-grid">
      ${num('signup_concurrency', '注册并发数 (1-10)', s.signup_concurrency ?? 2, '', 1, 10)}
      ${num('signup_interval_sec', '同线程间隔 (秒)', s.signup_interval_sec ?? 30, '', 0, 600)}
      ${bool('signup_daily_enabled', '看门狗日补', !!s.signup_daily_enabled, '关闭：只手动补号', '开启：每天定时补')}
      ${num('signup_daily_hour', '每天几点 (0-23)', s.signup_daily_hour ?? 10, '', 0, 23)}
      ${num('signup_daily_minute', '几分 (0-59)', s.signup_daily_minute ?? 0, '', 0, 59)}
      ${num('signup_daily_count', '每天补几个 (0=全部待补)', s.signup_daily_count ?? 20, '', 0, 100000)}
    </div>`),
  ]);
  tab('sched', '账号调度', '防关联节奏、额度与异常冷却', IC.sched, [
    card('防关联 / 账号调度', '控制号池中每个账号的使用节奏，降低关联与风控风险', `<div class="form-grid">
      ${num('acct_min_interval_sec', '同号提交间隔 (秒)', s.acct_min_interval_sec ?? 20, '', 0, 3600)}
      ${num('acct_interval_jitter_pct', '间隔抖动 (%)', s.acct_interval_jitter_pct ?? 40, '', 0, 200)}
      ${num('acct_global_daily_cap', '全号每日上限 (0=不限)', s.acct_global_daily_cap ?? 0, '', 0, 100000)}
      ${num('acct_daily_quota', '单号 24h 额度 (0=不限)', s.acct_daily_quota ?? 3, '', 0, 10000)}
      ${num('acct_rotate_attempts', '单请求换号次数', s.acct_rotate_attempts ?? 5, '', 1, 8)}
      ${num('acct_408_cooldown_min', '408 冷却 (分)', s.acct_408_cooldown_min ?? 3, '', 0, 600)}
      ${num('acct_408_disable_streak', '连续 408 自动禁用 (次)', s.acct_408_disable_streak ?? 3, '', 0, 100)}
      ${num('acct_cooldown_cap_min', '冷却上限 (分)', s.acct_cooldown_cap_min ?? 30, '', 0, 1440)}
      ${num('acct_403_stop_hours', '403 停用 (小时)', s.acct_403_stop_hours ?? 24, '', 0, 720)}
    </div>`, true),
  ]);
  tab('net', '代理与结果', '出口代理列表与生成结果回传方式', IC.net, [
    card('代理与结果模式', '注册与生成请求使用的出口代理，以及结果的交付方式', `<div class="form-grid">
      <div class="span2"><label class="f">代理列表（每行一个，支持 http/socks5，可含账号，支持 {sid} 占位；没写协议时按 SOCKS5）</label>
        <textarea id="proxies" style="min-height:110px" placeholder="us.udealproxy.com:6666:userId-7992-region-us-session-{sid}-sessTime-5:自己的密码&#10;socks5h://user:pass@host:3010&#10;支持 host:port:user:pass / user:pass@host:port / 完整 URL，{sid} 自动换会话">${esc(s.proxies || '')}</textarea>
        <div class="chips" style="margin-top:8px"><button class="btn" id="ptest">测试代理</button><span class="hint" style="margin:0" id="ptestres"></span></div></div>
      <div class="span2"><label class="f">生成结果模式 result_mode</label>
        <select id="result_mode"><option value="direct">直链 direct（默认）</option><option value="r2">R2 回传</option></select></div>
    </div>`, true),
  ]);
  tab('r2', 'R2 存储', 'R2 回传模式使用的存储账号池', IC.r2, ['<div id="r2host"></div>']);
  tab('sec', '安全与密钥', '管理员密码与对外 API Key', IC.sec, [
    card('安全', '修改管理台登录密码与对外调用密钥', `<div class="form-grid">
      <div><label class="f">管理员用户名</label><input type="text" id="admin_user" value="${esc(s.admin_user || 'admin')}" readonly /></div>
      <div><label class="f">新密码（≥6，留空不改）</label><input type="password" id="admin_password" autocomplete="new-password" /></div>
      <div class="span2"><label class="f">确认新密码</label><input type="password" id="admin_password_confirm" autocomplete="new-password" /></div>
    
      <div class="span2"><label class="f">API Key（外部调用 /v1/images/generate：Authorization: Bearer …）</label><div style="display:flex;gap:8px"><input type="text" id="apikey" readonly value="${esc(s.outbound_key || '')}" style="flex:1" /><button class="btn" id="kcopy" type="button">复制</button><button class="btn" id="krot" type="button">重新生成</button></div></div></div>`, true),
  ]);
  $('#st').innerHTML = `<div class="stabs-layout">
    <aside class="stabs" role="tablist">${groups.map(g => `<button class="stab" type="button" role="tab" data-tab="${g.id}">
      <span class="ic">${g.icon}</span><span class="t"><b>${g.label}</b><small>${g.desc}</small></span></button>`).join('')}</aside>
    <section class="stab-body">${groups.map(g => `<div class="stab-panel" role="tabpanel" data-panel="${g.id}">
      <div class="stab-hero"><span class="ic">${g.icon}</span><div><h3>${g.label}</h3><p>${g.desc}</p></div></div>
      <div class="stab-cards">${g.cards.join('')}</div></div>`).join('')}</section></div>`;
  const showTab = id => {
    if (!groups.some(g => g.id === id)) id = groups[0].id;
    document.querySelectorAll('#st .stab').forEach(b => { const on = b.dataset.tab === id; b.classList.toggle('on', on); b.setAttribute('aria-selected', on); });
    document.querySelectorAll('#st .stab-panel').forEach(p => p.classList.toggle('on', p.dataset.panel === id));
    $('#tst').style.display = id === 'engine' ? '' : 'none';
    $('#save').style.display = id === 'r2' ? 'none' : '';
    try { localStorage.setItem('settings_tab', id); } catch (_) {}
  };
  document.querySelectorAll('#st .stab').forEach(b => b.onclick = () => showTab(b.dataset.tab));
  let lastTab = ''; try { lastTab = localStorage.getItem('settings_tab') || ''; } catch (_) {}
  showTab(lastTab);
  const rm = $('#result_mode'); if (rm) rm.value = (s.result_mode === 'r2' ? 'r2' : 'direct');
  ['media_auth_required','rewrite_media_urls','console_generate_enabled','charge_on_upstream_error','default_into_pool','proxy_scheduled_keepalive'].forEach(id => {
    const el = $('#' + id); if (el) el.value = String(!!$('#' + id.replace(/^/,'')));
  });
  $('#media_auth_required').value = String(!!s.media_auth_required);
  $('#rewrite_media_urls').value = String(s.rewrite_media_urls !== false);
  $('#console_generate_enabled').value = String(s.console_generate_enabled !== false);
  $('#charge_on_upstream_error').value = String(!!s.charge_on_upstream_error);
  $('#default_into_pool').value = String(s.default_into_pool !== false);
  $('#proxy_scheduled_keepalive').value = String(!!s.proxy_scheduled_keepalive);
  const eb=$('#engine_backend'); if(eb) eb.value=(s.engine_backend === 'local' || s.engine_backend === 'cdp') ? 'cdp' : 'protocol';

  async function loadKeys() {
    let d; try { d = await api('/api/settings/keys'); } catch (e) { $('#ktb').innerHTML = `<tr><td colspan="6">${esc(e.message)}</td></tr>`; return; }
    const items = d.items || [];
    $('#ksum').innerHTML = `<span class="chip c-disabled">共 ${items.length}</span><span class="chip c-ok">启用 ${items.filter(x => x.enabled !== false).length}</span><span class="chip c-busy">环境 Key ${s.env_outbound_keys || 0}</span>`;
    $('#ktb').innerHTML = items.map(k => `<tr>
      <td>${esc(k.name || '-')}<span class="sub mono">#${k.id}</span></td>
      <td class="mono">${esc(k.prefix || k.key_prefix || '')}…</td>
      <td class="mono">${k.quota != null ? (k.used || 0) + ' / ' + k.quota : (k.used || 0) + ' / ∞'}</td>
      <td class="mono">${k.daily_limit != null ? (k.daily_used || 0) + ' / ' + k.daily_limit : '∞'}</td>
      <td>${k.enabled === false ? '<span class="chip c-disabled">停用</span>' : '<span class="chip c-ok">启用</span>'}</td>
      <td><div class="acts">
        <button class="ab" data-a="toggle" data-id="${k.id}">${k.enabled === false ? '启用' : '停用'}</button>
        <button class="ab" data-a="rotate" data-id="${k.id}">轮换</button>
        <button class="ab danger" data-a="del" data-id="${k.id}">删除</button>
      </div></td></tr>`).join('') || '<tr><td colspan="6" style="padding:28px;color:var(--muted)">暂无 Key，点击「新建 Key」</td></tr>';
    document.querySelectorAll('#ktb .ab').forEach(b => b.onclick = () => keyAct(b.dataset.a, +b.dataset.id));
  }
  async function keyAct(act, id) {
    try {
      if (act === 'del') { if (!await ask('删除这把 Key？')) return; await api('/api/settings/keys/' + id, { method: 'DELETE' }); }
      else if (act === 'rotate') { if (!await ask('轮换后旧密钥立即失效，继续？')) return; const r = await api('/api/settings/keys/' + id + '/rotate', { method: 'POST' }); if (r.key || r.token) showNewKey(r); }
      else if (act === 'toggle') {
        const row = (await api('/api/settings/keys')).items.find(x => x.id === id);
        await api('/api/settings/keys/' + id, { method: 'PATCH', body: { enabled: !(row && row.enabled !== false) } });
      }
      loadKeys();
    } catch (e) { toast(e.message, true); }
  }
  function showNewKey(r) {
    const raw = r.key || r.token || r.api_key || '';
    modal(`<h3>新 Key（只显示一次）</h3><textarea readonly style="min-height:80px" id="nk">${esc(raw)}</textarea>
      <div class="foot"><button class="btn" id="cp">复制</button><button class="btn primary" id="ok">关闭</button></div>`, m => {
      $('#cp', m).onclick = async () => { try { await navigator.clipboard.writeText(raw); toast('已复制'); } catch (_) { toast('请手动复制', true); } };
      $('#ok', m).onclick = () => m.remove();
    });
  }
  if ($('#kadd')) $('#kadd').onclick = () => modal(`<h3>新建对外 Key</h3>
    <label class="f">名称</label><input id="kn" value="default" />
    <label class="f">总额度（空=不限）</label><input type="number" id="kq" min="0" />
    <label class="f">日限（空=不限）</label><input type="number" id="kd" min="0" />
    <label class="f">模型白名单（逗号分隔，空=全部）</label><input id="km" placeholder="muse-image,muse-video" />
    <div class="foot"><button class="btn" id="cx">取消</button><button class="btn primary" id="ok">创建</button></div>`, m => {
    $('#cx', m).onclick = () => m.remove();
    $('#ok', m).onclick = async () => {
      try {
        const body = { name: $('#kn', m).value.trim() || 'default' };
        const q = $('#kq', m).value; if (q !== '') body.quota = parseInt(q, 10);
        const d = $('#kd', m).value; if (d !== '') body.daily_limit = parseInt(d, 10);
        const models = $('#km', m).value.trim(); if (models) body.models_allow = models.split(/[,，\s]+/).filter(Boolean);
        const r = await api('/api/settings/keys', { method: 'POST', body });
        m.remove(); showNewKey(r); loadKeys();
      } catch (e) { toast(e.message, true); }
    };
  });

  $('#tst').onclick = async () => {
    try {
      const r = await api('/api/settings/test-engine', { method: 'POST' });
      const local = r.local || {};
      const msg = r.ok
        ? ('引擎 OK · ' + (local.backend || 'unknown') + ' · ' + (local.backend === 'protocol' ? '无 Chromium' : ('chrome=' + (local.chromium || ''))) + ' · 号池 ' + ((local.pool && local.pool.total) || 0))
        : ('引擎异常: ' + (local.error || local.message || ''));
      toast(msg, !r.ok);
    } catch (e) { toast(e.message, true); }
  };
  if ($('#ptest')) $('#ptest').onclick = async () => {
    const raw = ($('#proxies') && $('#proxies').value || '').trim();
    $('#ptestres').textContent = '测试中…（最多 30 秒）';
    try {
      const r = await api('/api/settings/proxy-check', { method: 'POST', body: { proxies: raw } });
      if (!(r.results || []).length) { $('#ptestres').textContent = ''; return toast(r.hint || '未配置代理', true); }
      const rows = (r.results || []).map(x => `<tr>
        <td class="mono" style="max-width:220px;word-break:break-all">${esc(x.proxy || '-')}</td>
        <td class="mono">${esc(x.ip || '-')}</td>
        <td>${esc(x.country_code || '-')} ${esc(x.city || '')}</td>
        <td class="mono">${esc((x.isp || '').slice(0, 22))}</td>
        <td>${x.muse_open ? '<span class="chip c-ok">可用</span>' : '<span class="chip c-invalid">不可用</span>'}</td>
        <td>${x.proxy_flag ? '代理' : ''}${x.hosting ? ' 机房' : ''}</td></tr>`).join('');
      modal(`<h3>代理测试结果</h3>
        <div class="tablewrap"><table><thead><tr><th>代理</th><th>出口 IP</th><th>地区</th><th>ISP</th><th>判定</th><th>标记</th></tr></thead><tbody>${rows || '<tr><td colspan="6">无结果</td></tr>'}</tbody></table></div>
        <div class="foot"><button class="btn primary" id="cx">关闭</button></div>`, m => { $('#cx', m).onclick = () => m.remove(); });
      const ok = (r.results || []).filter(x => x.muse_open).length;
      $('#ptestres').textContent = `可用 ${ok} / ${(r.results || []).length}`;
    } catch (e) { $('#ptestres').textContent = ''; toast(e.message, true); }
  };

  if ($('#kcopy')) $('#kcopy').onclick = async () => {
    const v = $('#apikey').value || '';
    if (!v) return toast('还没有 Key，点「重新生成」', true);
    try { await navigator.clipboard.writeText(v); toast('已复制 API Key'); }
    catch (_) { $('#apikey').select(); document.execCommand('copy'); toast('已复制'); }
  };
  if ($('#krot')) $('#krot').onclick = async () => {
    if (!await ask('重新生成后旧 Key 立即失效，确定？')) return;
    try {
      const r = await api('/api/settings/outbound-key/regenerate', { method: 'POST', body: {} });
      $('#apikey').value = r.token || '';
      toast('已生成新的 API Key');
    } catch (e) { toast(e.message, true); }
  };

  $('#save').onclick = async () => {
    const body = {
      public_base: $('#public_base').value.trim(),
      engine_backend: ($('#engine_backend') && $('#engine_backend').value) || 'protocol',
      chromium_path: ($('#chromium_path') && $('#chromium_path').value.trim()) || '',
      cdp_port: parseInt(($('#cdp_port') && $('#cdp_port').value) || '19211', 10),
      engine_concurrency: parseInt(($('#engine_concurrency') && $('#engine_concurrency').value) || '2', 10),
      upstream_base_url: '',
      upstream_api_key: '',
      connect_timeout_sec: parseFloat($('#connect_timeout_sec').value || '10'),
      request_timeout_sec: parseFloat($('#request_timeout_sec').value || '120'),
      stream_idle_timeout_sec: parseFloat($('#stream_idle_timeout_sec').value || '60'),
      max_concurrency: parseInt($('#max_concurrency').value || '32', 10),
      record_keep_days: parseInt($('#record_keep_days').value || '30', 10),
      media_auth_required: $('#media_auth_required').value === 'true',
      rewrite_media_urls: $('#rewrite_media_urls').value === 'true',
      console_generate_enabled: $('#console_generate_enabled').value === 'true',
      charge_on_upstream_error: $('#charge_on_upstream_error').value === 'true',
      proxy_scheduled_keepalive: $('#proxy_scheduled_keepalive').value === 'true',
      signup_client: $('#signup_client').value.trim(),
      signup_muse_base: $('#signup_muse_base').value.trim(),
      default_region: $('#default_region').value.trim(),
      default_into_pool: $('#default_into_pool').value === 'true',
      queue_wait_sec: parseInt($('#queue_wait_sec').value || '25', 10),
      signup_concurrency: Math.min(10, Math.max(1, parseInt($('#signup_concurrency').value || '2', 10))),
      signup_interval_sec: parseInt($('#signup_interval_sec').value || '30', 10),
      signup_daily_enabled: $('#signup_daily_enabled').value === 'true',
      signup_daily_hour: parseInt($('#signup_daily_hour').value || '10', 10),
      signup_daily_minute: parseInt($('#signup_daily_minute').value || '0', 10),
      signup_daily_count: parseInt($('#signup_daily_count').value || '20', 10),
      acct_min_interval_sec: parseInt($('#acct_min_interval_sec').value || '20', 10),
      acct_interval_jitter_pct: parseInt($('#acct_interval_jitter_pct').value || '40', 10),
      acct_global_daily_cap: parseInt($('#acct_global_daily_cap').value || '0', 10),
      acct_daily_quota: parseInt($('#acct_daily_quota').value || '3', 10),
      acct_rotate_attempts: parseInt($('#acct_rotate_attempts').value || '5', 10),
      acct_408_cooldown_min: parseInt($('#acct_408_cooldown_min').value || '3', 10),
      acct_408_disable_streak: parseInt($('#acct_408_disable_streak').value || '3', 10),
      acct_cooldown_cap_min: parseInt($('#acct_cooldown_cap_min').value || '30', 10),
      acct_403_stop_hours: parseInt($('#acct_403_stop_hours').value || '24', 10),
      proxies: ($('#proxies') && $('#proxies').value) || '',
      result_mode: ($('#result_mode') && $('#result_mode').value) || 'direct',
    };
    const pwd = $('#admin_password').value;
    if (pwd) { body.admin_password = pwd; body.admin_password_confirm = $('#admin_password_confirm').value; }
    try { await api('/api/settings', { method: 'PATCH', body }); toast('已保存'); $('#admin_password').value = ''; $('#admin_password_confirm').value = ''; pageSettings(); }
    catch (e) { toast(e.message, true); }
  };
  if ($('#ktb')) await loadKeys();
  r2Init($('#r2host'));
}

const R2S = { status: 'all', q: '', page: 1, size: 10, items: [], total: 0, pages: 1, sel: new Set(), sum: {} };
function r2Init(host) {
  if (!host) return;
  host.innerHTML = `<div class="r2-section">
    <div class="card">
      <h2><span>R2 存储账号</span>
        <span class="r2-actions">
          <button class="btn" id="r2rf">刷新用量</button>
          <button class="btn" id="r2imp">导入 JSON</button>
          <button class="btn primary" id="r2add">添加账号</button>
        </span>
      </h2>
      <div class="tools">
        <div class="l">
          <select id="r2st"><option value="all">状态：全部</option><option value="enabled">启用</option><option value="disabled">停用</option><option value="full">将满</option><option value="error">异常</option></select>
          <input type="text" id="r2q" placeholder="搜索 名称 / Bucket / Endpoint" />
        </div>
        <div class="r">
          <button class="btn" data-r2="enable">启用选中</button>
          <button class="btn" data-r2="disable">停用选中</button>
          <button class="btn danger" data-r2="delete">删除选中</button>
        </div>
      </div>
      <div class="chips" id="r2sum" style="margin-bottom:12px"></div>
      <div class="tablewrap"><table>
        <thead><tr><th style="width:36px"><input type="checkbox" id="r2all" /></th><th>名称</th><th>Bucket</th><th>用量</th><th>对象</th><th>状态</th><th>操作</th></tr></thead>
        <tbody id="r2tb"></tbody>
      </table></div>
      <div class="pager"><span id="r2info"></span>
        <div class="mid"><button class="btn" id="r2pp">上一页</button><button class="btn" id="r2pn">下一页</button></div>
      </div>
    </div>
  </div>`;
  $('#r2st', host).onchange = () => { R2S.status = $('#r2st', host).value; R2S.page = 1; r2Load(); };
  let qt; $('#r2q', host).oninput = () => { clearTimeout(qt); qt = setTimeout(() => { R2S.q = $('#r2q', host).value.trim(); R2S.page = 1; r2Load(); }, 280); };
  $('#r2rf', host).onclick = async () => {
    try {
      const ids = [...R2S.sel];
      const r = await api('/api/r2/refresh', { method: 'POST', body: { ids } });
      toast('已刷新 ' + (r.refreshed || 0) + ' 个'); r2Load();
    } catch (e) { toast(e.message, true); }
  };
  $('#r2imp', host).onclick = r2ImportDlg;
  $('#r2add', host).onclick = () => r2Dlg(null);
  $('#r2pp', host).onclick = () => { R2S.page = Math.max(1, R2S.page - 1); r2Load(); };
  $('#r2pn', host).onclick = () => { R2S.page = Math.min(R2S.pages, R2S.page + 1); r2Load(); };
  $('#r2all', host).onchange = (e) => { R2S.items.forEach(a => e.target.checked ? R2S.sel.add(a.id) : R2S.sel.delete(a.id)); r2Draw(); };
  host.querySelectorAll('[data-r2]').forEach(b => b.onclick = async () => {
    const act = b.dataset.r2;
    if (!R2S.sel.size) return toast('请先勾选 R2 账号', true);
    if (act === 'delete' && !await ask(`删除选中的 ${R2S.sel.size} 个 R2 账号？`)) return;
    try {
      const r = await api('/api/r2/batch', { method: 'POST', body: { ids: [...R2S.sel], action: act } });
      toast('已处理 ' + (r.affected || 0)); R2S.sel.clear(); r2Load();
    } catch (e) { toast(e.message, true); }
  });
  r2Load();
}
async function r2Load() {
  const qs = new URLSearchParams({ status: R2S.status, q: R2S.q, page: R2S.page, size: R2S.size });
  let d; try { d = await api('/api/r2/accounts?' + qs); } catch (e) { const tb = $('#r2tb'); if (tb) tb.innerHTML = `<tr><td colspan="7">${esc(e.message)}</td></tr>`; return; }
  R2S.items = d.items || []; R2S.total = d.total || 0; R2S.pages = d.pages || 1; R2S.page = d.page || 1; R2S.sum = d.summary || {};
  r2Draw();
}
function r2Draw() {
  const tb = $('#r2tb'), sum = $('#r2sum'), info = $('#r2info'); if (!tb) return;
  const m = R2S.sum || {};
  if (sum) sum.innerHTML = `<span class="chip c-disabled">总计 ${m.total != null ? m.total : R2S.total}</span><span class="chip c-ok">启用 ${m.enabled || 0}</span><span class="chip c-disabled">停用 ${m.disabled || 0}</span><span class="chip c-cooling">将满 ${m.full || 0}</span><span class="chip c-invalid">异常 ${m.error || 0}</span>`;
  tb.innerHTML = R2S.items.map(a => {
    const q = a.quota || 0, u = a.used || 0, pct = q ? Math.min(100, Math.round(u * 100 / q)) : 0;
    const st = !a.enabled ? ['停用', 'c-disabled'] : (a.usageErr || a.lastErr) ? ['异常', 'c-invalid'] : (q && pct >= 95) ? ['将满', 'c-cooling'] : ['启用', 'c-ok'];
    return `<tr>
      <td><input type="checkbox" data-id="${a.id}" ${R2S.sel.has(a.id) ? 'checked' : ''} /></td>
      <td class="name">${esc(a.name)}<span class="sub mono">${esc(a.id)}</span></td>
      <td class="mono">${esc(a.bucket)}<span class="sub">${esc(a.region || 'auto')} · ${esc(a.prefix || '')}</span></td>
      <td><div class="r2-bar sm"><i style="width:${pct}%;background:${pct>=95?'var(--err)':pct>=80?'var(--warn,#c90)':'var(--ok)'}"></i></div><span class="mono sub">${u} / ${q || '∞'} GB · ${pct}%</span></td>
      <td class="mono">${a.objects || 0}</td>
      <td><span class="chip ${st[1]}">${st[0]}</span></td>
      <td><div class="acts">
        <button class="ab" data-a="edit" data-id="${a.id}">编辑</button>
        <button class="ab" data-a="toggle" data-id="${a.id}">${a.enabled ? '停用' : '启用'}</button>
        <button class="ab danger" data-a="del" data-id="${a.id}">删除</button>
      </div></td></tr>`;
  }).join('') || '<tr><td colspan="7" style="padding:28px;color:var(--muted)">暂无 R2 账号，点击「添加账号」或「导入 JSON」</td></tr>';
  if (info) info.textContent = `第 ${R2S.page} / ${R2S.pages} 页，共 ${R2S.total} 条` + (R2S.sel.size ? ` · 已选 ${R2S.sel.size}` : '');
  const all = $('#r2all'); if (all) all.checked = R2S.items.length > 0 && R2S.items.every(a => R2S.sel.has(a.id));
  tb.querySelectorAll('input[type=checkbox]').forEach(c => c.onchange = () => { c.checked ? R2S.sel.add(c.dataset.id) : R2S.sel.delete(c.dataset.id); r2Draw(); });
  tb.querySelectorAll('.ab').forEach(b => b.onclick = async () => {
    const a = R2S.items.find(x => x.id === b.dataset.id);
    if (b.dataset.a === 'edit') return r2Dlg(a);
    if (b.dataset.a === 'del') {
      if (!await ask('删除 R2 账号「' + (a && a.name) + '」？')) return;
      try { await api('/api/r2/batch', { method: 'POST', body: { ids: [a.id], action: 'delete' } }); r2Load(); } catch (e) { toast(e.message, true); }
      return;
    }
    if (b.dataset.a === 'toggle') {
      try { await api('/api/r2/batch', { method: 'POST', body: { ids: [a.id], action: a.enabled ? 'disable' : 'enable' } }); r2Load(); } catch (e) { toast(e.message, true); }
    }
  });
}
function r2Dlg(a) {
  const edit = !!a;
  modal(`<h3>${edit ? '编辑' : '添加'} R2 账号</h3>
    <div class="form-grid">
      <div><label class="f">名称</label><input id="rn" value="${esc(a && a.name || '')}" /></div>
      <div><label class="f">Bucket</label><input id="rb" value="${esc(a && a.bucket || '')}" /></div>
      <div><label class="f">前缀 prefix</label><input id="rp" value="${esc(a && a.prefix || 'generated')}" /></div>
      <div><label class="f">区域 region</label><input id="rr" value="${esc(a && a.region || 'auto')}" /></div>
      <div class="span2"><label class="f">Endpoint</label><input id="re" value="${esc(a && a.endpoint || '')}" placeholder="https://xxxx.r2.cloudflarestorage.com" /></div>
      <div class="span2"><label class="f">Public URL</label><input id="ru" value="${esc(a && a.publicUrl || '')}" placeholder="https://pub-xxx.r2.dev" /></div>
      <div><label class="f">Access Key</label><input id="rak" value="${esc(a && a.accessKey || '')}" autocomplete="off" /></div>
      <div><label class="f">Secret Key${edit ? '（留空不改）' : ''}</label><input id="rsk" type="password" autocomplete="new-password" placeholder="${edit ? '已保存则留空' : ''}" /></div>
      <div><label class="f">配额 GB</label><input type="number" id="rq" min="0" value="${a && a.quota != null ? a.quota : 10}" /></div>
      <div><label class="f">启用</label><select id="ren"><option value="true">启用</option><option value="false">停用</option></select></div>
    </div>
    <div class="foot"><button class="btn" id="cx">取消</button><button class="btn primary" id="ok">保存</button></div>`, m => {
    $('#ren', m).value = String(!(a && a.enabled === false));
    $('#cx', m).onclick = () => m.remove();
    $('#ok', m).onclick = async () => {
      const body = {
        name: $('#rn', m).value.trim(), bucket: $('#rb', m).value.trim(),
        prefix: $('#rp', m).value.trim() || 'generated', region: $('#rr', m).value.trim() || 'auto',
        endpoint: $('#re', m).value.trim(), publicUrl: $('#ru', m).value.trim(),
        accessKey: $('#rak', m).value.trim(), quotaGB: parseInt($('#rq', m).value || '10', 10),
        enabled: $('#ren', m).value === 'true',
      };
      const sk = $('#rsk', m).value; if (sk) body.secretKey = sk;
      try {
        if (edit) await api('/api/r2/accounts/' + a.id, { method: 'PATCH', body });
        else await api('/api/r2/accounts', { method: 'POST', body });
        toast('已保存'); m.remove(); r2Load();
      } catch (e) { toast(e.message, true); }
    };
  });
}
function r2ImportDlg() {
  modal(`<h3>导入 R2 账号 JSON</h3>
    <p class="hint" style="margin:0 0 8px">粘贴数组或 <code>{"accounts":[...]}</code>。字段：name / bucket / endpoint / accessKey / secretKey / publicUrl / prefix / region / quotaGB / enabled。</p>
    <textarea id="tx" style="min-height:220px" placeholder='[{"name":"r2-1","bucket":"my","endpoint":"https://...","accessKey":"...","secretKey":"..."}]'></textarea>
    <div class="foot"><button class="btn" id="cx">取消</button><button class="btn primary" id="ok">导入</button></div>`, m => {
    $('#cx', m).onclick = () => m.remove();
    $('#ok', m).onclick = async () => {
      try {
        const r = await api('/api/r2/import', { method: 'POST', body: { text: $('#tx', m).value } });
        toast(`导入成功 ${r.imported || 0}` + (r.skipped ? `，跳过 ${r.skipped}` : ''));
        m.remove(); r2Load();
      } catch (e) { toast(e.message, true); }
    };
  });
}


const RS = { status: 'all', q: '', page: 1, size: parseInt(localStorage.getItem('rgsize') || '12', 10), items: [], total: 0, pages: 1 };
const OUTCOME = { success: ['成功', 'c-ok'], incomplete: ['未取到图', 'c-cooling'], rate_limited: ['限流冷却', 'c-cooling'], no_quota: ['额度用尽', 'c-no_quota'], cookie_invalid: ['Cookie 失效', 'c-invalid'],
  ims_failed: ['IMS 失败', 'c-cooling'], token_expired: ['令牌过期', 'c-cooling'], transport_error: ['网络错误', 'c-invalid'], mint_failed: ['铸票失败', 'c-invalid'],
  no_proxy: ['无代理', 'c-invalid'], http_error: ['请求失败', 'c-invalid'] };
const fmtFull = (ms) => { if (!ms) return '-'; const d = new Date(ms), p = (n, l = 2) => String(n).padStart(l, '0'); return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`; };
const sizeCell = (s) => { if (!s) return '-'; const p = String(s).split(' · '); return esc(p[0]) + (p.length > 1 ? '<span class="sub">' + esc(p.slice(1).join(' · ')) + '</span>' : ''); };
const recChip = (r) => { const b = r.status_bucket || (r.state === 'running' ? 'running' : (r.ok || (r.status >= 200 && r.status < 300) ? 'success' : 'failed')); if (b === 'running' || r.state === 'running') return '<span class="chip c-busy">进行中</span>'; if (b === 'success') return '<span class="chip c-ok">成功</span>'; return `<span class="chip c-invalid" title="${esc(friendlyErr(r.error || ''))}">${esc(httpLabel(r.status, false))}</span>`; };

async function pageRecords() {
  $('#main').innerHTML = head('records', '<button class="btn" id="rf">刷新</button><button class="btn danger" id="cfail">清理失败</button><button class="btn danger" id="c3d">清理三天前</button><button class="btn danger" id="clean">更多清理</button>') + `
  <div class="card">
    <div class="tools">
      <div class="l">
        <select id="fs"><option value="all">状态：全部</option><option value="ok">成功</option><option value="fail">失败</option><option value="running">进行中</option></select>
        <input type="text" id="q" placeholder="搜索 提示词 / 账号 / 错误 / ID" />
      </div>
      <div class="r" id="sum"></div>
    </div>
    <div class="tablewrap"><table>
      <thead><tr><th>时间</th><th>提示词</th><th>模型</th><th>尺寸</th><th>账号</th><th>状态</th><th class="hide-m">耗时</th><th class="hide-m">图片</th><th>操作</th></tr></thead>
      <tbody id="tb"></tbody></table></div>
    <div class="pager">
      <span id="info"></span>
      <div class="mid"><button class="btn" id="p1">首页</button><button class="btn" id="pp">上一页</button><button class="btn" id="pn">下一页</button><button class="btn" id="pl">尾页</button></div>
      <div class="mid">每页 <input type="number" id="sz" min="1" max="200" /> 条 <button class="btn" id="svsz">保存</button></div>
    </div>
  </div>`;
  $('#fs').value = RS.status; $('#q').value = RS.q; $('#sz').value = RS.size;
  $('#fs').onchange = () => { RS.status = $('#fs').value; RS.page = 1; load(); };
  let qt; $('#q').oninput = () => { clearTimeout(qt); qt = setTimeout(() => { RS.q = $('#q').value.trim(); RS.page = 1; load(); }, 300); };
  $('#rf').onclick = load;
  $('#p1').onclick = () => { RS.page = 1; load(); };
  $('#pp').onclick = () => { RS.page = Math.max(1, RS.page - 1); load(); };
  $('#pn').onclick = () => { RS.page = Math.min(RS.pages, RS.page + 1); load(); };
  $('#pl').onclick = () => { RS.page = RS.pages; load(); };
  $('#svsz').onclick = () => { RS.size = Math.min(200, Math.max(1, parseInt($('#sz').value || '12', 10))); localStorage.setItem('rgsize', RS.size); RS.page = 1; load(); };
  const purge = async (body, what) => {
    if (!await ask(`确定${what}？（进行中的记录不会删除）`)) return;
    try { const r = await api('/api/records/delete', { method: 'POST', body: mapRecDelete(Object.assign({ exclude_api_type: 'chat' }, body)) }); toast(`已删除 ${r.deleted} 条`); load(); }
    catch (e) { toast(e.message, true); }
  };
  $('#cfail').onclick = () => purge({ mode: 'failed' }, '清理全部失败记录');
  $('#c3d').onclick = () => purge({ mode: 'before_days', days: 3 }, '清理三天前的全部记录');
  $('#clean').onclick = () => modal(`<h3>清理生成记录</h3>
    <label class="f">删除多少天之前的记录（0 = 不限时间，进行中的不会删除）</label><input type="number" id="days" value="7" min="0" />
    <label class="f" style="display:flex;gap:8px;align-items:center;cursor:pointer"><input type="checkbox" id="fo" /> 仅清理失败的记录</label>
    <div class="foot"><button class="btn" id="cx">取消</button><button class="btn danger" id="ok">删除</button></div>`, m => {
    $('#cx', m).onclick = () => m.remove();
    $('#ok', m).onclick = async () => {
      const days = parseInt($('#days', m).value || '0', 10), fo = $('#fo', m).checked;
      if (days === 0 && !fo && !await ask('确定清空全部记录？')) return;
      try {
        let body; if (fo) body = { mode: 'failed', exclude_api_type: 'chat' }; else if (days > 0) body = { mode: 'before_days', days, exclude_api_type: 'chat' }; else body = { mode: 'all', exclude_api_type: 'chat' };
        const r = await api('/api/records/delete', { method: 'POST', body }); toast(`已删除 ${r.deleted} 条`); m.remove(); load();
      } catch (e) { toast(e.message, true); }
    };
  });
  async function load() {
    const stMap = { ok: 'success', fail: 'failed', success: 'success', failed: 'failed', running: 'running', all: 'all' };
    const qs = new URLSearchParams({ status: stMap[RS.status] || RS.status || 'all', q: RS.q, page: RS.page, page_size: RS.size });
    let d; try { d = await api('/api/records?' + qs); } catch (_) { return; }
    const items = (d.items || []).map(normRecord).filter(r => !isChatRecord(r));
    RS.items = items; RS.total = d.total; RS.pages = d.pages; RS.page = d.page;
    const s = d.summary || { total: d.total, ok: items.filter(x => x.status_bucket === 'success').length, fail: items.filter(x => x.status_bucket === 'failed').length, running: items.filter(x => x.status_bucket === 'running').length };
    $('#sum').innerHTML = `<span class="chip c-disabled">总计 ${s.total || d.total || 0}</span><span class="chip c-ok">成功 ${s.ok || s.success || 0}</span><span class="chip c-invalid">失败 ${s.fail || s.failed || 0}</span><span class="chip c-busy">进行中 ${s.running || 0}</span>`;
    $('#tb').innerHTML = items.map(r => `<tr>
      <td class="mono">${fmtFull(r.createdAt).slice(5)}</td>
      <td style="text-align:left;max-width:280px" title="${esc(r.prompt)}">${esc(r.prompt.length > 46 ? r.prompt.slice(0, 46) + '…' : r.prompt)}</td>
      <td class="mono" style="white-space:nowrap">${esc(r.model || '-')}</td>
      <td class="mono" style="white-space:nowrap">${sizeCell(r.size)}</td>
      <td>${esc(r.account || '-')}</td>
      <td>${recChip(r)}</td>
      <td class="hide-m">${r.elapsedMs ? (r.elapsedMs / 1000).toFixed(1) + 's' : '-'}</td>
            <td class="hide-m">${r.images || 0}</td>
      <td><button class="btn" data-id="${r.id}">详情</button></td></tr>`).join('') || '<tr><td colspan="9" style="padding:40px;color:var(--muted)">暂无记录</td></tr>';
    $('#info').textContent = `第 ${RS.page} / ${RS.pages} 页，共 ${RS.total} 条`;
    document.querySelectorAll('#tb button[data-id]').forEach(b => b.onclick = () => detail(b.dataset.id));
  }

  async function detail(id) {
    let r; try { const raw = await api('/api/records/' + id); r = normRecord(raw.item || raw); } catch (e) { return toast(e.message, true); }
    const m = modal(`<div class="wide"><div id="rd"></div>
      <div class="foot"><button class="btn danger" id="del">删除此记录</button><button class="btn primary" id="cl">关闭</button></div></div>`);
    $('#cl', m).onclick = () => m.remove();
    $('#del', m).onclick = async () => { if (!await ask('删除这条记录？')) return; await api('/api/records/delete', { method: 'POST', body: { mode: 'ids', ids: [r.id] } }); m.remove(); load(); };
    const wasRunning = r.state === 'running';
    for (;;) {
      $('#rd', m).innerHTML = recBody(r);
      const lg = $('#rlog', m);
      if (lg) lg.scrollTop = lg.scrollHeight;
      if (r.state !== 'running') break;
      await new Promise(res => setTimeout(res, 1000));
      if (!document.body.contains(m)) return;
      try { const raw = await api('/api/records/' + id); r = normRecord(raw.item || raw); } catch (e) { return; }
    }
    if (wasRunning) load();
  }

  const LOG_COLOR = { ok: '#4ade80', warn: '#fbbf24', err: '#f87171', info: '#cbd5e1' };
  function recLog(r) {
    const lines = r.log || [];
    if (!lines.length) return r.state === 'running' ? '<div style="color:#94a3b8">等待第一步…</div>' : '<div style="color:#94a3b8">此记录没有过程日志（旧记录）</div>';
    const t0 = r.createdAt || lines[0].t;
    return lines.map(l => `<div><span style="color:#64748b">${fmtFull(l.t).slice(11)} +${((l.t - t0) / 1000).toFixed(1)}s</span> <span style="color:${LOG_COLOR[l.lv] || LOG_COLOR.info}">${esc(l.msg)}</span></div>`).join('')
      + (r.state === 'running' ? '<div style="color:#94a3b8">… 进行中（每秒刷新）</div>' : '');
  }

  function linkList(urls) {
    return (urls || []).map((u, i) => `<div class="mono" style="word-break:break-all">${urls.length > 1 ? (i + 1) + '. ' : ''}<a href="${esc(u)}" target="_blank">${esc(u)}</a></div>`).join('') || '<div style="color:var(--muted)">-</div>';
  }
  function recLinks(r) {
    const adobe = r.sourceUrls && r.sourceUrls.length ? r.sourceUrls
      : (r.resultMode === 'direct' || !r.resultMode) && r.imageUrls && r.imageUrls.length && !/r2\.dev|cloudflarestorage|pub-[a-f0-9]+\.r2/.test(r.imageUrls[0] || '') ? r.imageUrls : [];
    const r2 = r.sourceUrls && r.sourceUrls.length ? (r.imageUrls || [])
      : r.imageUrls && r.imageUrls.length && /r2\.dev|cloudflarestorage|pub-[a-f0-9]+\.r2/.test(r.imageUrls[0] || '') ? r.imageUrls : [];
    if (!r.resultHref && !adobe.length && !r2.length) return '';
    const box = (title, body) => `<div style="margin-bottom:8px">${body}</div>`;
    return `<label class="f">结果地址${r.resultMode ? ' · ' + esc(r.resultMode) : ''}</label>
      <div style="background:#f6f8fb;border-radius:8px;padding:10px;font-size:12px;line-height:1.6">
        ${r.resultHref ? box('结果链接', `<div class="mono" style="word-break:break-all"><a href="${esc(r.resultHref)}" target="_blank">${esc(r.resultHref)}</a></div>`) : ''}
        ${adobe.length ? box('媒体直链', linkList(adobe)) : ''}
        ${r2.length ? box('改写后的媒体地址', linkList(r2)) : ''}
      </div>`;
  }

  function recBody(r) {
    const kv = (k, v) => `<div><div style="word-break:break-all">${v || '-'}</div></div>`;
    const isVid = /seedance|veo|omni/.test(r.model || '');
    const imgs = (r.imageUrls || []).map(u => isVid || isVideoUrl(u)
      ? `<video src="${esc(u)}" controls preload="metadata" style="width:100%;border-radius:8px;border:1px solid var(--line)"></video>`
      : `<a href="${esc(u)}" target="_blank"><img src="${esc(u)}" style="width:100%;border-radius:8px;border:1px solid var(--line)" loading="lazy" /></a>`).join('');
    const att = (r.attempts || []).map((a, i) => {
      const [t, c] = OUTCOME[a.outcome] || [a.outcome || '-', 'c-disabled'];
      return `<tr><td>${i + 1}</td><td class="mono">${fmtFull(a.at).slice(11)}</td><td>${esc(a.account)}<span class="sub mono">${esc(a.accountId)}</span></td>
        <td class="mono">${esc(a.ticket || '-')}<span class="sub">${a.ticketUses || 0}/${a.ticketMax || 0}</span></td>
        <td class="mono">${esc(a.profile || '-')}</td><td class="mono">${esc(a.sid || '-')}<span class="sub">${esc(a.region || '')}</span></td>
        <td>${a.status ? esc(httpLabel(a.status)) : '-'}</td><td><span class="chip ${c}">${t}</span></td><td>${a.ms ? (a.ms / 1000).toFixed(1) + 's' : '-'}</td>
        <td style="text-align:left;max-width:240px">${esc(friendlyErr(a.error || '').slice(0, 200))}</td></tr>`;
    }).join('') || '<tr><td colspan="9" style="color:var(--muted)">无尝试记录</td></tr>';
    const live = r.state === 'running' ? (Date.now() - r.createdAt) / 1000 : 0;
    return `<h3 style="display:flex;justify-content:space-between;align-items:center">生成记录详情 ${recChip(r)}</h3>
      <div class="grid g3" style="gap:12px;margin-bottom:12px">
        ${kv('记录 ID', '<span class="mono">' + esc(r.id) + '</span>')}${kv('创建时间', fmtFull(r.createdAt))}${kv('完成时间', r.finishedAt ? fmtFull(r.finishedAt) : '进行中')}
        ${kv('总耗时', r.elapsedMs ? (r.elapsedMs / 1000).toFixed(2) + ' s' : live ? '已进行 ' + live.toFixed(0) + ' s' : '-')}${kv('状态', r.status ? esc(httpLabel(r.status, r.ok)) + ' <span class="mono hint">' + r.status + '</span>' : '-')}
        ${kv('API 类型', esc(r.api_type || r.bucket || '-'))}${kv('模型 / 规格', esc(r.model) + ' · ' + esc(r.size))}${kv('调用方', esc(r.client))}
        ${kv('最终账号', esc(r.account) + ' <span class="mono hint">' + esc(r.accountId) + '</span>')}${kv('票据', esc(r.ticketId) + (r.ticketUses ? ' (第 ' + r.ticketUses + ' 次)' : ''))}
        ${kv('Key / 来源', esc(r.key_prefix || r.source || '-'))}${kv('上游任务', '<span class="mono">' + esc(r.upstream_task_id || '-') + '</span>')}
      </div>
      <label class="f">提示词</label><div style="white-space:pre-wrap;background:#f6f8fb;border-radius:8px;padding:10px;font-size:13px">${esc(r.prompt)}</div>
      ${imgs ? `<label class="f">生成结果（${r.imageUrls.length} 个${r.resultMode === 'r2' || r.resultMode === 'r2_partial' ? ' · R2' : r.resultMode === 'direct' ? ' · 直链' : ''}）</label><div class="grid g3" style="gap:10px">${imgs}</div>` : ''}
      ${recLinks(r)}
      ${r.error ? `<label class="f">错误信息</label><pre class="mono" style="white-space:pre-wrap;word-break:break-all;background:#fdeaea;color:#b91c1c;border-radius:8px;padding:10px">${esc(friendlyErr(r.error))}</pre>` : ''}
      <label class="f">过程日志${r.state === 'running' ? '（实时）' : ''}</label>
      <div id="rlog" class="mono" style="background:#0f172a;border-radius:8px;padding:10px;font-size:12px;line-height:1.7;max-height:260px;overflow:auto;white-space:pre-wrap;word-break:break-all">${recLog(r)}</div>
      <label class="f">尝试链路（若有）</label>
      <div class="tablewrap"><table><thead><tr><th>#</th><th>时间</th><th>账号</th><th>票据</th><th>闲哥秘密</th><th>SID</th><th>状态</th><th>结果</th><th>耗时</th><th>错误</th></tr></thead><tbody>${att}</tbody></table></div>`;
  }
  await load();
}

/* ---------- 补号系统 = Muse 注册（原导号 UI） ---------- */
const SUB_STATUS = { geo_gated: '地区受限', pending: '待注册', running: '注册中', awaiting_payment: '待外部支付', awaiting_external_payment: '待外部支付', needs_profile: '待填资料', needs_otp: '待 OTP', awaiting_otp: '待 OTP', awaiting_profile: '待填资料', awaiting_recovery_code: '待恢复码', awaiting_set_password: '待设密', awaiting_password_login: '待密码登录', activated: '已注册', pooled: '已入池', failed: '失败', ready: '就绪', stopped: '已停' };
const SUB_CHIP = { geo_gated: 'c-invalid', pending: 'c-disabled', running: 'c-busy', awaiting_payment: 'c-cooling', awaiting_external_payment: 'c-cooling', needs_profile: 'c-cooling', needs_otp: 'c-cooling', awaiting_otp: 'c-cooling', awaiting_profile: 'c-cooling', awaiting_recovery_code: 'c-cooling', awaiting_set_password: 'c-cooling', awaiting_password_login: 'c-cooling', activated: 'c-ok', pooled: 'c-pts', failed: 'c-invalid', ready: 'c-ok', stopped: 'c-disabled' };
const SUB_COUNTRIES = ['US', 'SG', 'JP', 'DE', 'GB', 'CA', 'AU', 'FR', 'NL', 'KR', 'TW', 'HK', 'IN', 'PH', 'ID', 'MY', 'TH', 'VN', 'BR'];
let _savedCountry = localStorage.getItem('subcountry') || 'US';
if (_savedCountry === 'JP') { _savedCountry = 'US'; localStorage.setItem('subcountry', 'US'); }
const SS = { status: 'all', q: '', page: 1, pages: 1, total: 0, size: parseInt(localStorage.getItem('subsize') || '20', 10), items: [], sel: new Set(), sum: {}, run: {}, country: _savedCountry, intoPool: localStorage.getItem('subpool') !== '0' };

async function pageRefills() {
  const main = $('#main');
  main.innerHTML = head('refills', '<button class="btn" id="rf">刷新</button><button class="btn" id="lg">日志</button><button class="btn primary" id="imp">导入邮箱</button>') + `
  <div class="chips" id="sum" style="margin-bottom:12px"></div>
  <div class="card">
    <div id="prog" class="subprog hidden"></div>
    <div class="tools">
      <div class="l">
        <select id="fs"><option value="all">状态：全部</option><option value="pending">待注册</option><option value="running">注册中</option><option value="awaiting_otp">待 OTP</option><option value="awaiting_profile">待填资料</option><option value="awaiting_recovery_code">待恢复码</option><option value="awaiting_set_password">待设密</option><option value="awaiting_password_login">待密码登录</option><option value="awaiting_external_payment">待外部支付</option><option value="awaiting_card">待卡支付</option><option value="geo_gated">地区受限</option><option value="paying">支付中</option><option value="activated">已注册</option><option value="failed">失败</option><option value="pooled">已入池</option></select>
        <input type="text" id="q" placeholder="搜索 邮箱 / 信息 / 地区" />
        <select id="co" title="注册国家">${SUB_COUNTRIES.map(c => `<option value="${c}">国家 ${c}</option>`).join('')}</select>
        <label class="inl"><input type="checkbox" id="pool" /> 注册后入池</label>
      </div>
      <div class="r">
        <button class="btn primary" data-b="activate-all">一键自动补号</button>
        <button class="btn danger" data-b="del-selected">删除选中</button>
        <button class="btn danger" data-b="del-failed">删除失败</button>
        <button class="btn danger" data-b="clear-all">清空全部</button>
      </div>
    </div>
    <div class="tablewrap"><table>
      <thead><tr><th style="width:36px"><input type="checkbox" id="all" /></th><th>邮箱</th><th>邮箱物料</th><th class="hide-m">名字</th><th>国家</th><th>状态</th><th>注册结果</th><th class="hide-m">指纹</th><th class="hide-m">导入时间</th><th>操作</th></tr></thead>
      <tbody id="tb"></tbody></table></div>
    <div class="pager">
      <span id="info"></span>
      <div class="mid"><button class="btn" id="p1">首页</button><button class="btn" id="pp">上一页</button><button class="btn" id="pn">下一页</button><button class="btn" id="pl">尾页</button></div>
      <div class="mid">每页 <input type="number" id="sz" min="1" max="200" /> 条 <button class="btn" id="svsz">保存</button></div>
    </div>
    <div class="hint" style="margin-top:12px">补号系统只需邮箱物料：导入后勾选邮箱（或点「一键自动补号」）→ 协议自动取邮件账号与验证码 → 自动填资料 → 自动从「卡池」取卡完成 $1 年龄验证 → 设密 → 入号池。卡号/CVV 只在服务端使用，不回显。</div>
  </div>`;
  $('#fs').value = SS.status; $('#q').value = SS.q; $('#sz').value = SS.size; $('#co').value = SS.country; $('#pool').checked = SS.intoPool;
  $('#fs').onchange = () => { SS.status = $('#fs').value; SS.page = 1; load(); };
  let qt; $('#q').oninput = () => { clearTimeout(qt); qt = setTimeout(() => { SS.q = $('#q').value.trim(); SS.page = 1; load(); }, 300); };
  $('#co').onchange = () => { SS.country = $('#co').value; localStorage.setItem('subcountry', SS.country); };
  $('#pool').onchange = () => { SS.intoPool = $('#pool').checked; localStorage.setItem('subpool', SS.intoPool ? '1' : '0'); };
  $('#rf').onclick = () => { load(); loadTask(); }; $('#imp').onclick = importDlg; $('#lg').onclick = logDlg;
  $('#p1').onclick = () => { SS.page = 1; load(); };
  $('#pp').onclick = () => { SS.page = Math.max(1, SS.page - 1); load(); };
  $('#pn').onclick = () => { SS.page = Math.min(SS.pages, SS.page + 1); load(); };
  $('#pl').onclick = () => { SS.page = SS.pages; load(); };
  $('#svsz').onclick = () => { SS.size = Math.min(200, Math.max(1, parseInt($('#sz').value || '20', 10))); localStorage.setItem('subsize', SS.size); SS.page = 1; load(); };
  $('#all').onchange = (e) => { SS.items.forEach(a => e.target.checked ? SS.sel.add(a.id) : SS.sel.delete(a.id)); draw(); };
  document.querySelectorAll('[data-b]').forEach(b => b.onclick = () => batch(b.dataset.b));

  async function load() {
    const qs = new URLSearchParams({ status: SS.status, q: SS.q, page: SS.page, page_size: SS.size, size: SS.size });
    let d; try { d = await api('/api/refills?' + qs); } catch (_) { return; }
    SS.items = (d.items || []).map(normSub); SS.total = d.total; SS.pages = d.pages || Math.max(1, Math.ceil((d.total||0)/SS.size)); SS.page = d.page; SS.sum = d.summary || d.stats || {};
    draw();
  }
  async function loadTask() {
    let t; try { t = await api('/api/refills/task?after=' + (1 << 30)); } catch (_) { return; }
    SS.run = t; drawProg();
  }
  function drawProg() {
    const t = SS.run, el = $('#prog'); if (!el) return;
    if (!t.running && !t.total) {
      if (t.scheduleLabel) { el.classList.remove('hidden'); el.innerHTML = `<div class="subprog-top"><b>定时注册</b><span>${esc(t.scheduleLabel)}</span></div>`; }
      else el.classList.add('hidden');
      return;
    }
    el.classList.remove('hidden');
    el.innerHTML = `<div class="subprog-top"><b>${t.running ? '注册进行中' : '上次任务'}</b><span>${t.done} / ${t.total} · 成功 ${t.ok} · 失败 ${t.fail}${t.hold ? ' · 未完成 ' + t.hold : ''}${t.message && !t.running ? ' · ' + esc(t.message) : ''}${t.scheduleLabel ? ' · ' + esc(t.scheduleLabel) : ''}</span>
      <span style="flex:1"></span>${t.running ? '<button class="btn danger" id="stp" style="height:26px">停止</button>' : ''}</div>
      <div class="bar"><i style="width:${t.progress || 0}%"></i></div>`;
    const st = $('#stp'); if (st) st.onclick = async () => { try { await api('/api/refills/stop', { method: 'POST' }); toast('已请求停止'); loadTask(); } catch (e) { toast(e.message, true); } };
  }
  function draw() {
    const m = SS.sum || {};
    $('#sum').innerHTML = `<span class="chip c-disabled">总计 ${m.total || 0}</span><span class="chip c-disabled">待注册 ${m.pending || 0}</span><span class="chip c-busy">注册中 ${m.running || 0}</span><span class="chip c-ok">已注册 ${m.activated || 0}</span><span class="chip c-invalid">失败 ${m.failed || 0}</span><span class="chip c-pts">已入池 ${m.pooled || 0}</span>`;
    $('#tb').innerHTML = SS.items.map(a => {
      const mailBits = [];
      mailBits.push(a.mailReady ? '<span class="chip c-ok">物料完整</span>' : '<span class="chip c-invalid" title="需要 邮箱----密码----ClientID----RefreshToken----辅助邮箱----辅助密码">物料缺失</span>');
      if (a.hasAux) mailBits.push('<span class="chip c-ok" title="' + esc(a.auxEmailMasked || '') + '">有辅助</span>');
      if (a.hasPassword) mailBits.push('<span class="chip c-ok">已设密</span>');
      if (a.hasCookie) mailBits.push('<span class="chip c-ok">有Cookie</span>');
      const result = a.resultSummary
        ? `<span title="${esc(a.resultSummary)}">${esc(a.resultSummary)}</span>`
        : (a.pooled ? '<span class="chip c-pts">已入库</span>'
          : (a.status === 'awaiting_payment' || a.ui_status === 'awaiting_external_payment') ? '<span class="chip c-cooling">待支付</span>'
          : '<span class="sub" style="display:inline">-</span>');
      return `<tr>
      <td><input type="checkbox" data-id="${a.id}" ${SS.sel.has(a.id) ? 'checked' : ''}></td>
      <td class="name">${esc(a.email)}<span class="sub mono">${a.id}${a.tries ? ' · 第 ' + a.tries + ' 次' : ''}${a.upstreamId ? ' · 池 ' + esc(a.upstreamId) : ''}</span></td>
      <td>${mailBits.join(' ')}</td>
      <td class="hide-m">${esc(a.displayName || '—')}</td>
      <td class="mono">${esc(a.country || '-')}</td>
      <td><span class="chip ${SUB_CHIP[a.status] || 'c-disabled'}" title="${esc(a.message || '')}">${SUB_STATUS[a.status] || a.status}</span></td>
      <td class="ellip">${result}</td>
      <td class="hide-m mono ellip" title="${esc(a.fingerprintProfile || a.fingerprintLabel || '')}">${esc(a.fingerprintLabel || a.fingerprintProfile || '—')}</td>
      <td class="hide-m">${fmtTime(a.createdAt)}</td>
      <td>
        <button class="ib" title="查看邮件" data-a="mail" data-id="${a.id}">${ICON.mail || ICON.edit}</button>
        <button class="ib" title="启用邮箱（开始/继续补号）" data-a="act" data-id="${a.id}">${ICON.play}</button>
        <button class="ib danger" title="删除" data-a="del" data-id="${a.id}">${ICON.trash}</button>
      </td></tr>`;
    }).join('') || '<tr><td colspan="10" style="padding:40px;color:var(--muted)">暂无邮箱，点击右上角「导入邮箱」</td></tr>';
    $('#info').textContent = `第 ${SS.page} / ${SS.pages} 页，共 ${SS.total} 条` + (SS.sel.size ? ` · 已选 ${SS.sel.size}` : '');
    $('#all').checked = SS.items.length > 0 && SS.items.every(a => SS.sel.has(a.id));
    document.querySelectorAll('#tb input[type=checkbox]').forEach(c => c.onchange = () => { c.checked ? SS.sel.add(c.dataset.id) : SS.sel.delete(c.dataset.id); draw(); });
    document.querySelectorAll('#tb .ib').forEach(b => b.onclick = () => rowAction(b.dataset.a, b.dataset.id));
  }
  async function start(body, okMsg) {
    try {
      const r = await api('/api/refills/batch', { method: 'POST', body: { ...body, country: SS.country, intoPool: SS.intoPool } });
      toast(okMsg(r)); await load(); await loadTask();
    } catch (e) { toast(e.message, true); }
  }
  function profileDlg(a) {
    const p = a.profile || {};
    modal(`<h3>填资料 · ${esc(a.email)}</h3>
      <p class="hint" style="margin:0 0 8px">无卡全协议注册。名字/生日留空则服务端随机生成。禁止卡号 / CVV / PAN。</p>
      <label class="f">名 first_name（可空=随机）</label><input id="fn" value="${esc(p.first_name || '')}" placeholder="随机" />
      <label class="f">姓 last_name（可空=随机）</label><input id="ln" value="${esc(p.last_name || '')}" placeholder="随机" />
      <label class="f">生日（YYYY-MM-DD，可空=随机成人）</label><input id="bd" placeholder="留空则随机" value="${esc(p.date_of_birth || '')}" />
      <label class="f">地区 / 国家</label><input id="rg" value="${esc(a.country || SS.country || 'US')}" />
      <label class="f">备注（可选）</label><input id="nt" placeholder="可选" />
      <div class="foot"><button class="btn" id="cx">取消</button><button class="btn primary" id="ok">提交</button></div>`, m => {
      $('#cx', m).onclick = () => m.remove();
      $('#ok', m).onclick = async () => {
        try {
          const body = {
            date_of_birth: $('#bd', m).value.trim() || null,
            first_name: $('#fn', m).value.trim() || null,
            last_name: $('#ln', m).value.trim() || null,
            region: $('#rg', m).value.trim(),
            country: $('#rg', m).value.trim(),
            note: $('#nt', m).value.trim() || null,
            remember_device: true,
            auto_fill: true,
          };
          await api('/api/refills/' + a.id + '/profile', { method: 'POST', body });
          toast('资料已提交（缺省字段已随机）'); m.remove(); load(); loadTask();
        } catch (e) { toast(e.message, true); }
      };
    });
  }
  function otpDlg(a) {
    modal(`<h3>OTP · ${esc(a.email)}</h3>
      <p class="hint" style="margin:0 0 8px">确认邮箱验证码。也可点「换一种方式」走 Recovery 设密。</p>
      <label class="f">验证码 otp_code</label><input id="otp" placeholder="6 位码" />
      <div class="foot"><button class="btn" id="cx">取消</button><button class="btn" id="auto">自动取码</button><button class="btn" id="alt">换一种方式·设密</button><button class="btn primary" id="ok">确认验证码</button></div>`, m => {
      $('#cx', m).onclick = () => m.remove();
      $('#alt', m).onclick = () => { m.remove(); recoveryDlg(a); };
      $('#auto', m).onclick = async () => {
        try {
          toast('正在从邮箱取码…');
          const r = await api('/api/refills/' + a.id + '/mail/code?kind=otp', { soft: true });
          if (r && r.code) { $('#otp', m).value = r.code; toast('已取到验证码'); }
          else toast((r && (r.error || r.message)) || '未取到验证码（检查邮箱物料）', true);
        } catch (e) { toast(e.message, true); }
      };
      $('#ok', m).onclick = async () => {
        try { await api('/api/refills/' + a.id + '/otp', { method: 'POST', body: { otp_code: $('#otp', m).value.trim(), remember_device: true } }); toast('验证码已提交'); m.remove(); load(); loadTask(); }
        catch (e) { toast(e.message, true); }
      };
    });
  }
  function resumeDlg(a) {
    modal(`<h3>外部支付后续 · ${esc(a.email)}</h3>
      <p class="hint" style="margin:0 0 8px">你在外部完成 Muse 年龄验证支付后，填入 payment_ref，继续 accept-tos / wake / 入池。不收集卡密。</p>
      <label class="f">payment_ref</label><input id="pref" placeholder="外部支付完成后的引用" />
      <div class="foot"><button class="btn" id="cx">取消</button><button class="btn primary" id="ok">继续</button></div>`, m => {
      $('#cx', m).onclick = () => m.remove();
      $('#ok', m).onclick = async () => {
        try { await api('/api/refills/' + a.id + '/resume', { method: 'POST', body: { payment_ref: $('#pref', m).value.trim() } }); toast('已继续注册'); m.remove(); load(); loadTask(); }
        catch (e) { toast(e.message, true); }
      };
    });
  }
  function recoveryDlg(a) {
    const st = a.rawStatus || a.status;
    modal(`<h3>Recovery 设密 · ${esc(a.email)}</h3>
      <p class="hint" style="margin:0 0 8px">全协议：recovery-start → confirm-code → recovery-complete（#PWD_BROWSER）。确认恢复码后服务端会自动随机设密（写入加密会话，不回显明文）。也可手工填密码。无卡。</p>
      <div class="chips" style="margin-bottom:10px"><button class="btn" id="rs">1. 发恢复码</button><button class="btn" id="rc2">2. 自动取恢复码</button></div>
      <label class="f">恢复码 recovery_code</label><input id="rc" placeholder="邮箱收到的恢复码 / mock 可用 00000000" />
      <label class="f">新密码（可空=随机强密码）</label><input id="np" type="password" autocomplete="new-password" placeholder="留空则服务端随机生成" />
      <div class="foot"><button class="btn" id="cx">取消</button><button class="btn" id="okc">确认恢复码（自动设密）</button><button class="btn primary" id="okp">手工设密</button></div>`, m => {
      $('#cx', m).onclick = () => m.remove();
      $('#rs', m).onclick = async () => {
        try { const r = await api('/api/refills/' + a.id + '/recovery/start', { method: 'POST', body: {} }); toast(r.ok ? '恢复码已发送（可到收件查看）' : (r.result && r.result.message) || '已请求'); load(); }
        catch (e) { toast(e.message, true); }
      };
      $('#okc', m).onclick = async () => {
        try { await api('/api/refills/' + a.id + '/recovery/code', { method: 'POST', body: { recovery_code: $('#rc', m).value.trim() } }); toast('恢复码已确认并自动随机设密'); m.remove(); load(); loadTask(); }
        catch (e) { toast(e.message, true); }
      };
      $('#rc2', m).onclick = async () => {
        try {
          toast('正在从邮箱取恢复码…');
          const r = await api('/api/refills/' + a.id + '/mail/code?kind=recovery', { soft: true });
          if (r && r.code) { $('#rc', m).value = r.code; toast('已取到恢复码'); }
          else toast((r && (r.error || r.message)) || '未取到恢复码', true);
        } catch (e) { toast(e.message, true); }
      };
      $('#okp', m).onclick = async () => {
        try { await api('/api/refills/' + a.id + '/recovery/password', { method: 'POST', body: { new_password: $('#np', m).value } }); toast('设密完成'); m.remove(); load(); loadTask(); }
        catch (e) { toast(e.message, true); }
      };
    });
  }
  function pwLoginDlg(a) {
    modal(`<h3>密码登录 · ${esc(a.email)}</h3>
      <p class="hint" style="margin:0 0 8px">send-otp 若返回 password_login_available=true，走 confirm-password（免 OTP）。未设密请先「Recovery 设密」。</p>
      <div class="chips" style="margin-bottom:10px"><button class="btn" id="ps">探测可否密码登录</button></div>
      <label class="f">密码</label><input id="pw" type="password" autocomplete="current-password" placeholder="账号密码" />
      <label class="inl" style="margin-top:8px"><input type="checkbox" id="rd" checked /> 记住设备</label>
      <div class="foot"><button class="btn" id="cx">取消</button><button class="btn primary" id="ok">确认密码登录</button></div>`, m => {
      $('#cx', m).onclick = () => m.remove();
      $('#ps', m).onclick = async () => {
        try {
          const r = await api('/api/refills/' + a.id + '/password/start', { method: 'POST', body: {} });
          if (r.ok) toast('可密码登录，请提交密码');
          else toast((r.result && (r.result.message || r.result.error)) || '尚不可密码登录', true);
          load();
        } catch (e) { toast(e.message, true); }
      };
      $('#ok', m).onclick = async () => {
        try {
          await api('/api/refills/' + a.id + '/password', { method: 'POST', body: { password: $('#pw', m).value, remember_device: $('#rd', m).checked } });
          toast('密码登录已提交'); m.remove(); load(); loadTask();
        } catch (e) { toast(e.message, true); }
      };
    });
  }
  function detailDlg(a) {
    const st = SUB_STATUS[a.status] || a.status;
    modal(`<h3>邮箱详情 · ${esc(a.email)}</h3>
      <div class="sub" style="display:block;line-height:1.8;margin-bottom:10px">
        状态：<b>${esc(st)}</b><br/>
        结果：${esc(a.resultSummary || '-')}<br/>
        卡：${esc(a.cardMasked || '-')}<br/>
        指纹：${esc(a.fingerprintLabel || '-')}<br/>
        说明：${esc(a.message || '-')}
      </div>
      <p class="hint" style="margin:0 0 8px">正常情况下不用动这些按钮：点行首 ▶ 就会自动取码 → 填资料 → 卡池取卡验卡 → 设密入池。</p>
      <div class="chips">
        <button class="btn" data-x="profile">填资料</button>
        <button class="btn" data-x="otp">输验证码</button>
        <button class="btn" data-x="recovery">恢复码设密</button>
        <button class="btn" data-x="pwlogin">密码登录</button>
        <button class="btn" data-x="resume">外部支付后续</button>
        <button class="btn" data-x="cardpay">卡池支付</button>
        <button class="btn" data-x="invite">邀请码</button>
      </div>
      <div class="foot"><button class="btn primary" id="cx">关闭</button></div>`, m => {
      $('#cx', m).onclick = () => m.remove();
      m.querySelectorAll('[data-x]').forEach(b => b.onclick = () => { m.remove(); rowAction(b.dataset.x, a.id); });
    });
  }

  function cardPay(a) {
    modal(`<h3>卡池支付 · ${esc(a.email)}</h3>
      <p class="hint" style="margin:0 0 8px">用「卡池」里的卡自动跑 checkout（init → PTT(JWE) → credential → finalize）完成 $1 年龄验证，然后 accept-tos / wake / 自动设密 / 入池。卡号与 CVV 只在服务端解密并封进 platformTrustToken，不回显。</p>
      <div class="sub" style="display:block;margin-bottom:8px">当前状态：<b>${esc(SUB_STATUS[a.status] || a.status)}</b>${a.cardMasked ? ' · 上次用卡 ' + esc(a.cardMasked) : ''}</div>
      <label class="f">指定卡（留空=按导入顺序取可用卡）</label>
      <select id="cc"><option value="">自动挑选可用卡</option></select>
      <div class="foot"><button class="btn" id="cx">取消</button><button class="btn primary" id="ok">开始支付</button></div>`, async m => {
      $('#cx', m).onclick = () => m.remove();
      try {
        const d = await api('/api/cards?status=available&size=100');
        const sel = $('#cc', m);
        (d.items || []).forEach(c => {
          const o = document.createElement('option');
          o.value = c.id; o.textContent = `${c.masked} · ${c.expiry} · ${c.zip || '-'}`;
          sel.appendChild(o);
        });
        if (!(d.items || []).length) sel.innerHTML = '<option value="">卡池暂无可用卡（先到「卡池」导入）</option>';
      } catch (_) {}
      $('#ok', m).onclick = async () => {
        try {
          const cardId = $('#cc', m).value || null;
          toast('checkout 进行中…');
          const r = await api('/api/cards/pay/' + a.id, { method: 'POST', body: cardId ? { card_id: cardId } : {} });
          const res = r.result || {};
          if (r.ok) toast('支付成功，已继续激活/入池');
          else toast('支付未成功：' + (res.message || res.error || (res.errors && res.errors[0]) || '查看任务日志'), true);
          m.remove(); load(); loadTask();
        } catch (e) { toast(e.message, true); }
      };
    });
  }

  function inviteDlg(a) {
    modal(`<h3>邀请码 · ${esc(a.email)}</h3>
      <p class="hint" style="margin:0 0 8px">需已有 hatch_sess（激活/入池后）。可兑码、查看或创建自有邀请码。无支付卡。</p>
      <label class="f">兑换邀请码</label><input id="ic" placeholder="如 C9O594" />
      <div class="foot"><button class="btn" id="cx">取消</button><button class="btn" id="ig">查看</button><button class="btn" id="icr">创建</button><button class="btn primary" id="ok">兑换</button></div>
      <pre id="out" class="subbox" style="margin-top:10px;max-height:180px;overflow:auto"></pre>`, m => {
      $('#cx', m).onclick = () => m.remove();
      const show = (x) => { $('#out', m).textContent = typeof x === 'string' ? x : JSON.stringify(x, null, 2); };
      $('#ig', m).onclick = async () => { try { show(await api('/api/refills/' + a.id + '/invite')); } catch (e) { toast(e.message, true); } };
      $('#icr', m).onclick = async () => { try { show(await api('/api/refills/' + a.id + '/invite?create=1')); } catch (e) { toast(e.message, true); } };
      $('#ok', m).onclick = async () => {
        try { const r = await api('/api/refills/' + a.id + '/invite/redeem', { method: 'POST', body: { code: $('#ic', m).value.trim() } }); toast('已兑换'); show(r); load(); }
        catch (e) { toast(e.message, true); }
      };
    });
  }
  async function rowAction(act, id) {
    const a = SS.items.find(x => x.id === id);
    if (act === 'mail') return mailDlg(a);
    if (act === 'profile') return profileDlg(a);
    if (act === 'otp') return otpDlg(a);
    if (act === 'resume') return resumeDlg(a);
    if (act === 'cardpay') return cardPay(a);
    if (act === 'recovery') return recoveryDlg(a);
    if (act === 'pwlogin') return pwLoginDlg(a);
    if (act === 'invite') return inviteDlg(a);
    if (act === 'act') return start({ ids: [id], action: 'activate' }, () => '已开始注册 ' + a.email);
    if (act === 'del') {
      if (!await ask('删除补号 ' + a.email + ' ？')) return;
      try { await api('/api/refills/batch', { method: 'POST', body: { ids: [id], action: 'delete' } }); SS.sel.delete(id); load(); } catch (e) { toast(e.message, true); }
    }
  }
  async function batch(action) {
    if (action === 'mailcheck') {
      const ids = SS.sel.size ? [...SS.sel] : SS.items.map(x => x.id);
      if (!ids.length) return toast('没有可检测的邮箱', true);
      toast('检测收件…');
      let ok = 0, fail = 0, errs = [];
      for (const id of ids.slice(0, 50)) {
        try {
          const r = await api('/api/refills/' + id + '/mail/check', { method: 'POST', soft: true });
          if (r && r.ok) ok++; else { fail++; if (r && r.error) errs.push(r.error); }
        } catch (e) { fail++; errs.push(e.message); }
      }
      toast(`收件可用 ${ok} · 不可用 ${fail}` + (errs.length ? '（' + errs[0] + '）' : ''), fail > 0);
      return;
    }
    if (action === 'activate-all') return autoDlg();
    if (action === 'del-selected') {
      if (!SS.sel.size) return toast('请先勾选邮箱', true);
      if (!await ask(`删除选中的 ${SS.sel.size} 个邮箱？`)) return;
      try {
        const r = await api('/api/refills/batch', { method: 'POST', body: { ids: [...SS.sel], action: 'delete' } });
        toast(`已删除 ${r.affected}`); SS.sel.clear(); load();
      } catch (e) { toast(e.message, true); }
      return;
    }
    if (action === 'del-failed') {
      if (!await ask('删除全部失败/地区受限的邮箱？')) return;
      try {
        const r = await api('/api/refills/batch', { method: 'POST', body: { action: 'delete', scope: 'failed' } });
        toast(`已删除 ${r.affected} 个`); load();
      } catch (e) { toast(e.message, true); }
      return;
    }
    if (action === 'clear-all') {
      if (!await ask('清空全部邮箱物料（含已入池记录）？此操作不可恢复')) return;
      if (!await ask('再确认一次：真的清空全部？')) return;
      try {
        const r = await api('/api/refills/batch', { method: 'POST', body: { action: 'delete', scope: 'all' } });
        toast(`已清空 ${r.affected} 个`); SS.sel.clear(); load();
      } catch (e) { toast(e.message, true); }
      return;
    }
    if (action === 'export') {
      if (SS.sel.size) location.href = '/api/refills/export?ids=' + [...SS.sel].join(',');
      else location.href = '/api/refills/export?all=1';
      return;
    }
    if (!SS.sel.size) return toast('请先勾选邮箱', true);
    if (action === 'delete') {
      if (!await ask(`删除选中的 ${SS.sel.size} 个邮箱？`)) return;
      try { const r = await api('/api/refills/batch', { method: 'POST', body: { ids: [...SS.sel], action } }); toast(`已删除 ${r.affected}`); SS.sel.clear(); load(); } catch (e) { toast(e.message, true); }
      return;
    }
    if (action === 'reset') {
      try { const r = await api('/api/refills/batch', { method: 'POST', body: { ids: [...SS.sel], action } }); toast(`已重置 ${r.affected}`); load(); } catch (e) { toast(e.message, true); }
      return;
    }
    start({ ids: [...SS.sel], action }, r => `已开始${action === 'retry' ? '重试' : '注册'} ${r.count} 个邮箱`);
  }
  async function autoDlg() {
    const pending = SS.sum.pending || 0;
    let defConc = 2;
    try { const w = await api('/api/settings'); defConc = (w.config || w).signup_concurrency || defConc; } catch (_) {}
    modal(`<h3>一键自动补号</h3>
      <p class="hint" style="margin:0 0 8px">自动完成：取邮件账号 → 收验证码 → 填资料 → 卡池取卡 → $1 验卡 → 设密 → 入池。</p>
      <label class="f">补号数量（0 = 全部待注册，当前待注册 ${pending} 个）</label>
      <input id="an" type="number" min="0" value="0" />
      <label class="f">并发数（1–10，默认取「系统设置 → 注册补号」；太高容易被 Meta 限流）</label>
      <input id="ac" type="number" min="1" max="10" value="${defConc}" />
      <div class="foot"><button class="btn" id="cx">取消</button><button class="btn primary" id="ok">开始补号</button></div>`, m => {
      $('#cx', m).onclick = () => m.remove();
      $('#ok', m).onclick = async () => {
        const n = Math.max(0, parseInt($('#an', m).value || '0', 10));
        const c = Math.min(10, Math.max(1, parseInt($('#ac', m).value || '1', 10)));
        if (!pending && !n) { toast('没有待注册的邮箱', true); return; }
        m.remove();
        await start({ all: true, status: 'pending', action: 'activate', limit: n, concurrency: c },
          r => `已开始补号 ${r.count} 个（并发 ${c}）`);
      };
    });
  }
  function importDlg() {
    modal(`<h3>导入邮箱（补号）</h3>
      <p class="hint" style="margin:0 0 8px">每行一个，格式：<code>主邮箱----密码----ClientID----RefreshToken----辅助邮箱----辅助密码</code>（对齐小AD补号物料）。用于 Muse 无卡注册；支付在外部完成后用「支付后续」继续。也支持 4 段或 <code>|</code> / Tab 分隔。</p>
      <div style="display:flex;gap:8px;align-items:center;margin-bottom:6px"><button class="btn" id="pick">选择文件导入（txt / csv）</button><span class="hint" id="fn" style="margin:0"></span><input type="file" id="ff" accept=".txt,.csv,text/plain" class="hidden" /></div>
      <textarea id="txt" style="min-height:220px" placeholder="粘贴邮箱物料…" spellcheck="false"></textarea>
      <div class="foot"><button class="btn" id="cx">取消</button><button class="btn primary" id="ok">导入</button></div>`, m => {
      $('#pick', m).onclick = () => $('#ff', m).click();
      $('#ff', m).onchange = async (e) => { const f = e.target.files[0]; if (!f) return; const tx = await f.text(); $('#txt', m).value = ($('#txt', m).value ? $('#txt', m).value.replace(/\s+$/, '') + '\n' : '') + tx; $('#fn', m).textContent = f.name + ' · ' + tx.split(/\r?\n/).filter(x => x.trim()).length + ' 行'; };
      $('#cx', m).onclick = () => m.remove();
      $('#ok', m).onclick = async () => {
        try {
          const r = await api('/api/refills/import', { method: 'POST', body: { text: $('#txt', m).value, country: SS.country } });
          toast(`新增 ${r.added}，更新 ${r.updated}，已入库 ${r.linked || 0}` + (r.refreshing ? `，失效补登 ${r.refreshing}` : '') + `，无效 ${r.invalid}` + (r.started ? `，已自动开始 ${r.started} 个` : (r.added ? '（排队等待自动激活）' : '')) + `，现有 ${r.total}`);
          m.remove(); SS.page = 1; load(); loadTask();
        } catch (e) { toast(e.message, true); }
      };
    });
  }
  function logDlg() {
    let after = 0;
    const m = modal(`<h3 style="display:flex;align-items:center;gap:10px">注册日志<span class="chip c-disabled" id="lst">空闲</span><span style="flex:1"></span><button class="btn danger" id="lstop" style="height:26px">停止</button></h3>
      <div class="bar" style="margin-bottom:10px"><i id="lbar" style="width:0%"></i></div>
      <div class="hint" id="lmsg" style="margin:0 0 8px"></div>
      <div class="subbox" id="lbox"><div class="sub" style="display:block">暂无日志</div></div>
      <div class="foot"><button class="btn" id="lclr">清屏</button><button class="btn primary" id="lcl">关闭</button></div>`, mm => {
      $('#lcl', mm).onclick = () => mm.remove();
      $('#lclr', mm).onclick = () => { $('#lbox', mm).innerHTML = ''; };
      $('#lstop', mm).onclick = async () => { try { await api('/api/refills/stop', { method: 'POST' }); } catch (e) { toast(e.message, true); } };
    });
    const tick = async () => {
      if (!document.body.contains(m)) return clearInterval(iv);
      let t; try { t = await api('/api/refills/task?after=' + after); } catch (_) { return; }
      $('#lst', m).textContent = t.running ? '运行中' : '空闲'; $('#lst', m).className = 'chip ' + (t.running ? 'c-busy' : 'c-disabled');
      $('#lbar', m).style.width = (t.progress || 0) + '%';
      $('#lmsg', m).textContent = t.total ? `${t.done} / ${t.total} · 成功 ${t.ok} · 失败 ${t.fail}${t.hold ? ' · 未完成 ' + t.hold : ''}${t.message ? ' · ' + t.message : ''}` : '';
      $('#lstop', m).disabled = !t.running;
      const box = $('#lbox', m);
      if (t.logs && t.logs.length) {
        if (!after) box.innerHTML = '';
        box.insertAdjacentHTML('beforeend', t.logs.map(l => `<div class="ll ${l.level}"><span>${fmtClock(l.t)}</span>${esc(l.msg)}</div>`).join(''));
        box.scrollTop = box.scrollHeight; after = t.logs[t.logs.length - 1].id;
      }
    };
    const iv = setInterval(tick, 1500); tick();
  }

  function mailDlg(a) {
    const m = modal(`<h3 style="display:flex;align-items:center;gap:10px">收件箱<span class="mono hint">${esc(a.email)}</span><span style="flex:1"></span><button class="btn" id="mrf" style="height:26px">刷新</button></h3>
      <div class="hint" style="margin:0 0 8px">微软邮箱直连收件，不走代理。优先 IMAP，失败再试 Graph。</div>
      <div class="mail-layout"><div class="mail-list" id="ml"><div class="sub" style="display:block;padding:20px">加载中…</div></div><div class="mail-detail" id="md"><div class="sub" style="display:block;padding:20px">点左侧邮件查看内容</div></div></div>
      <div class="foot"><button class="btn primary" id="mcl">关闭</button></div>`, mm => {
      $('#mcl', mm).onclick = () => mm.remove();
      const load = async () => {
        $('#ml', mm).innerHTML = '<div class="sub" style="display:block;padding:20px">加载中…</div>';
        try {
          const r = await api('/api/refills/' + a.id + '/mail');
          const items = r.items || [];
          if (!items.length) { $('#ml', mm).innerHTML = '<div class="sub" style="display:block;padding:20px">收件箱为空</div>'; return; }
          $('#ml', mm).innerHTML = items.map(it => `<button class="mail-row ${it.unread ? 'unread' : ''}" data-mid="${esc(it.id)}">
            <b>${esc(it.subject || '(无主题)')}</b>
            <span>${esc(it.from || '')}</span>
            <span class="sub" style="display:block;margin:0">${(it.received_at || it.receivedAt) ? fmtFull(new Date(it.received_at || it.receivedAt).getTime()) : ''}</span>
          </button>`).join('');
          $('#ml', mm).querySelectorAll('.mail-row').forEach(b => b.onclick = () => openMsg(b.dataset.mid, b));
        } catch (e) { $('#ml', mm).innerHTML = `<div class="sub" style="display:block;padding:20px;color:var(--err)">${esc(e.message)}</div>`; }
      };
      const openMsg = async (mid, btn) => {
        $('#ml', mm).querySelectorAll('.mail-row').forEach(x => x.classList.toggle('on', x === btn));
        $('#md', mm).innerHTML = '<div class="sub" style="display:block;padding:20px">加载中…</div>';
        try {
          const r = await api('/api/refills/' + a.id + '/mail/message?id=' + encodeURIComponent(mid));
          const d = r.item || r.message || {};
          const html = d.html || '';
          const plain = d.text || d.body || '';
          const body = html ? html : '<pre style="white-space:pre-wrap;margin:0;font:inherit">' + esc(plain) + '</pre>';
          const when = d.received_at || d.receivedAt;
          $('#md', mm).innerHTML = `<div class="mail-head"><b>${esc(d.subject || '(无主题)')}</b><div class="sub" style="display:block;margin:4px 0 0">${esc(d.from || '')} · ${when ? fmtFull(new Date(when).getTime()) : ''}</div></div><div class="mail-body">${body}</div>`;
        } catch (e) { $('#md', mm).innerHTML = `<div class="sub" style="display:block;padding:20px;color:var(--err)">${esc(e.message)}</div>`; }
      };
      $('#mrf', mm).onclick = load; load();
    });
  }
  function keepaliveDlg() {
    modal(`<h3>号池保活</h3>
      <p class="hint" style="margin:0 0 8px">对 muse2api 上游账号执行 keepalive / test / relogin / quota。不碰卡字段。</p>
      <label class="f">动作</label>
      <select id="kaact"><option value="keepalive">keepalive</option><option value="test">test</option><option value="relogin">relogin</option><option value="quota">quota</option></select>
      <label class="f">范围</label>
      <select id="kascope"><option value="all">全部上游账号</option><option value="selected">下方勾选</option></select>
      <div id="kalist" class="tablewrap" style="max-height:240px;overflow:auto;margin:8px 0"><div class="sub">加载账号…</div></div>
      <div class="foot"><button class="btn" id="cx">取消</button><button class="btn primary" id="ok">开始</button></div>`, async m => {
      $('#cx', m).onclick = () => m.remove();
      const picked = new Set();
      try {
        const d = await api('/api/refills/upstream-accounts');
        const rows = d.accounts || [];
        $('#kalist', m).innerHTML = rows.length ? `<table><thead><tr><th></th><th>标签</th><th>状态</th></tr></thead><tbody>${rows.map(a => `<tr>
          <td><input type="checkbox" data-id="${esc(a.id)}" /></td>
          <td>${esc(a.label || a.id)}</td>
          <td>${a.ok ? '<span class="chip c-ok">OK</span>' : (a.enabled === false ? '<span class="chip c-disabled">停用</span>' : '<span class="chip c-invalid">异常</span>')}</td>
        </tr>`).join('')}</tbody></table>` : '<div class="sub">上游无账号或不可达</div>';
        $('#kalist', m).querySelectorAll('input[type=checkbox]').forEach(c => c.onchange = () => { c.checked ? picked.add(c.dataset.id) : picked.delete(c.dataset.id); });
      } catch (e) { $('#kalist', m).innerHTML = `<div class="sub" style="color:var(--err)">${esc(e.message)}</div>`; }
      $('#ok', m).onclick = async () => {
        try {
          const scope = $('#kascope', m).value;
          const body = { action: $('#kaact', m).value, scope, account_ids: scope === 'selected' ? [...picked] : [] };
          const r = await api('/api/refills/keepalive', { method: 'POST', body });
          toast('保活任务已启动'); m.remove(); loadTask();
          if (r.job && r.job.id) { /* progress via loadTask */ }
        } catch (e) { toast(e.message, true); }
      };
    });
  }

  await load(); await loadTask();
  if (SS._timer) clearInterval(SS._timer);
  SS._timer = setInterval(() => { if (document.hidden) return; loadTask(); }, 4000);
}



/* ---------- cards 卡池 ---------- */
const CARD_STATUS = {
  available: ['可用', 'c-ok'], in_use: ['使用中', 'c-busy'], used: ['已用', 'c-pts'],
  failed: ['失败', 'c-cooling'], dead: ['已废', 'c-invalid'], disabled: ['停用', 'c-disabled'],
};
const CS = { status: 'all', q: '', page: 1, size: parseInt(localStorage.getItem('csize') || '20', 10), items: [], total: 0, pages: 1, sel: new Set() };

async function pageCards() {
  const main = $('#main');
  main.innerHTML = head('cards', '<button class="btn" id="rf">刷新</button><button class="btn" id="exp">导出全部</button><button class="btn" id="add">新增卡</button><button class="btn primary" id="imp">导入卡</button>') + `
  <div class="chips" id="csum" style="margin-bottom:12px"></div>
  <div class="card">
    <div class="tools">
      <div class="l">
        <select id="fs">
          <option value="all">状态：全部</option>
          <option value="available">可用</option>
          <option value="in_use">使用中</option>
          <option value="used">已用</option>
          <option value="failed">失败</option>
          <option value="dead">已废</option>
          <option value="disabled">停用</option>
          <option value="expired">已过期</option>
        </select>
        <input type="text" id="q" placeholder="搜索 后四位 / 持卡人 / BIN / ZIP / 备注" />
      </div>
      <div class="r">
        <button class="btn" data-b="reset">恢复选中</button>
        <button class="btn" data-b="enable">启用</button>
        <button class="btn" data-b="disable">停用</button>
        <button class="btn danger" data-b="delete">删除</button>
      </div>
    </div>
    <div class="tablewrap"><table class="acc-table">
      <thead><tr><th style="width:36px"><input type="checkbox" id="all" /></th><th>卡号</th><th>有效期</th><th>持卡人</th><th class="hide-m">国家 / ZIP</th><th>状态</th><th class="hide-m">绑定任务</th><th class="hide-m">尝试</th><th class="hide-m">最近错误</th><th style="min-width:190px">操作</th></tr></thead>
      <tbody id="tb"></tbody></table></div>
    <div class="pager">
      <span id="info"></span>
      <div class="mid"><button class="btn" id="p1">首页</button><button class="btn" id="pp">上一页</button><button class="btn" id="pn">下一页</button><button class="btn" id="pl">尾页</button></div>
      <div class="mid">每页 <input type="number" id="sz" min="1" max="200" /> 条 <button class="btn" id="svsz">保存</button></div>
    </div>
  </div>`;
  $('#fs').value = CS.status; $('#q').value = CS.q; $('#sz').value = CS.size;
  $('#fs').onchange = () => { CS.status = $('#fs').value; CS.page = 1; load(); };
  let qt; $('#q').oninput = () => { clearTimeout(qt); qt = setTimeout(() => { CS.q = $('#q').value.trim(); CS.page = 1; load(); }, 300); };
  $('#rf').onclick = load;
  $('#imp').onclick = importDlg;
  $('#add').onclick = addDlg;
  $('#exp').onclick = () => location.href = '/api/cards/export?full=1';
  $('#p1').onclick = () => { CS.page = 1; load(); };
  $('#pp').onclick = () => { CS.page = Math.max(1, CS.page - 1); load(); };
  $('#pn').onclick = () => { CS.page = Math.min(CS.pages, CS.page + 1); load(); };
  $('#pl').onclick = () => { CS.page = CS.pages; load(); };
  $('#svsz').onclick = () => { CS.size = Math.min(200, Math.max(1, parseInt($('#sz').value || '20', 10))); localStorage.setItem('csize', CS.size); CS.page = 1; load(); };
  $('#all').onchange = (e) => { CS.items.forEach(c => e.target.checked ? CS.sel.add(c.id) : CS.sel.delete(c.id)); draw(); };
  document.querySelectorAll('[data-b]').forEach(b => b.onclick = () => batch(b.dataset.b));

  async function load() {
    const qs = new URLSearchParams({ status: CS.status, q: CS.q, page: CS.page, size: CS.size });
    let d; try { d = await api('/api/cards?' + qs); } catch (_) { return; }
    CS.items = d.items || []; CS.total = d.total; CS.pages = d.pages; CS.page = d.page;
    draw(); drawStats(d.stats || {}, d.total);
  }
  function drawStats(st, total) {
    $('#csum').innerHTML = [
      ['总计', total != null ? total : (st.total || 0), 'c-disabled'],
      ['可用', st.available || 0, 'c-ok'],
      ['使用中', st.in_use || 0, 'c-busy'],
      ['已用', st.used || 0, 'c-pts'],
      ['失败', st.failed || 0, 'c-cooling'],
      ['已废', st.dead || 0, 'c-invalid'],
      ['停用', st.disabled || 0, 'c-disabled'],
    ].map(([k, v, c]) => `<span class="chip ${c}">${k} ${v}</span>`).join('');
  }
  function draw() {
    $('#tb').innerHTML = CS.items.map(c => {
      const [label, cls] = CARD_STATUS[c.status] || [c.status, 'c-disabled'];
      return `<tr>
        <td><input type="checkbox" data-id="${c.id}" ${CS.sel.has(c.id) ? 'checked' : ''}></td>
        <td class="acc-cell"><b class="mono">${esc(c.masked)}</b>${c.expired ? ' <span class="chip c-invalid">过期</span>' : ''}</td>
        <td class="mono">${esc(c.expiry)}</td>
        <td>${esc(c.holder || '—')}</td>
        <td class="hide-m mono">${esc(c.country || '-')} / ${esc(c.zip || '-')}</td>
        <td><span class="chip ${cls}" title="${esc(c.last_error || '')}">${label}</span></td>
        <td class="hide-m mono ellip" title="${esc(c.used_by_item || '')}">${esc(c.used_by_item || '—')}</td>
        <td class="hide-m mono">${c.attempts || 0} / <span title="失败次数（超过 3 次自动禁用）">${c.fail_count || 0}</span></td>
        <td class="hide-m mono ellip" title="${esc(c.last_error || '')}">${esc(c.last_error || '—')}</td>
        <td><div class="acts">
          <button class="ab" data-a="edit" data-id="${c.id}">编辑</button>
          <button class="ab" data-a="reset" data-id="${c.id}" title="重置为可用">恢复</button>
          <button class="ab" data-a="toggle" data-id="${c.id}">${c.status === 'disabled' ? '启用' : '停用'}</button>
          <button class="ab danger" data-a="del" data-id="${c.id}">删除</button>
        </div></td></tr>`;
    }).join('') || '<tr><td colspan="10" style="padding:40px;color:var(--muted)">暂无卡，点击右上角「导入卡」</td></tr>';
    $('#info').textContent = `第 ${CS.page} / ${CS.pages} 页，共 ${CS.total} 张` + (CS.sel.size ? ` · 已选 ${CS.sel.size}` : '');
    $('#all').checked = CS.items.length > 0 && CS.items.every(c => CS.sel.has(c.id));
    document.querySelectorAll('#tb input[type=checkbox][data-id]').forEach(c => c.onchange = () => { c.checked ? CS.sel.add(c.dataset.id) : CS.sel.delete(c.dataset.id); draw(); });
    document.querySelectorAll('#tb .ab').forEach(b => b.onclick = () => rowAction(b.dataset.a, b.dataset.id));
  }
  async function rowAction(act, id) {
    const c = CS.items.find(x => x.id === id);
    try {
      if (act === 'edit') return editDlg(c);
      if (act === 'reset') await api('/api/cards/batch', { method: 'POST', body: { ids: [id], action: 'reset' } });
      if (act === 'toggle') await api('/api/cards/batch', { method: 'POST', body: { ids: [id], action: c.status === 'disabled' ? 'enable' : 'disable' } });
      if (act === 'del') { if (!await ask('删除卡 ' + c.masked + ' ？')) return; await api('/api/cards/' + id, { method: 'DELETE' }); CS.sel.delete(id); }
      load();
    } catch (e) { toast(e.message, true); }
  }
  async function batch(action) {
    if (!CS.sel.size) return toast('请先勾选卡', true);
    if (action === 'delete' && !await ask(`删除选中的 ${CS.sel.size} 张卡？`)) return;
    try {
      const r = await api('/api/cards/batch', { method: 'POST', body: { ids: [...CS.sel], action } });
      toast(`已处理 ${r.affected} 张`);
      if (action === 'delete') CS.sel.clear();
      load();
    } catch (e) { toast(e.message, true); }
  }
  function importDlg() {
    modal(`<h3>导入卡</h3>
      <p class="hint" style="margin:0 0 8px">每行一张，<code>卡号|MM/YY|CVV|持卡人|地址|城市|州|ZIP|国家|电话|邮箱|IP|状态</code>；短格式 <code>卡号|MM/YY|CVV|ZIP|US</code> 也可。PAN / CVV 以 Fernet 密文入库，列表只显示掩码。</p>
      <div style="display:flex;gap:8px;align-items:center;margin-bottom:6px"><button class="btn" id="pick">选择文件（txt / csv）</button><span class="hint" id="fn" style="margin:0"></span><input type="file" id="ff" accept=".txt,.csv,text/plain" class="hidden" /></div>
      <textarea id="txt" style="min-height:220px" placeholder="粘贴卡数据…" spellcheck="false"></textarea>
      <label class="f">或服务器本地文件路径（可选，管理员本机）</label><input id="lp" placeholder="C:\\Users\\you\\Downloads\\50.txt" />
      <label class="f">来源备注</label><input id="src" placeholder="如 50-batch-1104" />
      <div class="foot"><button class="btn" id="cx">取消</button><button class="btn primary" id="ok">导入</button></div>`, m => {
      $('#pick', m).onclick = () => $('#ff', m).click();
      $('#ff', m).onchange = async (e) => {
        const f = e.target.files[0]; if (!f) return;
        const tx = await f.text();
        $('#txt', m).value = ($('#txt', m).value ? $('#txt', m).value.replace(/\s+$/, '') + '\n' : '') + tx;
        $('#fn', m).textContent = f.name + ' · ' + tx.split(/\r?\n/).filter(x => x.trim()).length + ' 行';
        $('#src', m).value = $('#src', m).value || f.name;
      };
      $('#cx', m).onclick = () => m.remove();
      $('#ok', m).onclick = async () => {
        try {
          const body = { text: $('#txt', m).value, source: $('#src', m).value.trim() };
          if ($('#lp', m).value.trim()) body.path = $('#lp', m).value.trim();
          const r = await api('/api/cards/import', { method: 'POST', body });
          toast(`新增 ${r.added} · 重复 ${r.duplicate} · 无效 ${r.invalid}` + (r.errors && r.errors.length ? '（' + r.errors[0] + '）' : ''));
          m.remove(); CS.page = 1; load();
        } catch (e) { toast(e.message, true); }
      };
    });
  }
  function addDlg() {
    modal(`<h3>新增卡</h3>
      <label class="f">卡号 PAN</label><input id="pan" placeholder="4111111111111111" autocomplete="off" spellcheck="false" />
      <label class="f">有效期 MM/YY</label><input id="exp" placeholder="09/30" />
      <label class="f">CVV</label><input id="cvv" placeholder="123" autocomplete="off" />
      <label class="f">持卡人</label><input id="hd" placeholder="Test User" />
      <label class="f">ZIP</label><input id="zp" placeholder="73301" />
      <label class="f">国家</label><input id="co" value="US" />
      <label class="f">备注</label><input id="nt" placeholder="可选" />
      <div class="foot"><button class="btn" id="cx">取消</button><button class="btn primary" id="ok">保存</button></div>`, m => {
      $('#cx', m).onclick = () => m.remove();
      $('#ok', m).onclick = async () => {
        const [mm, yy] = ($('#exp', m).value || '').split('/');
        try {
          await api('/api/cards', { method: 'POST', body: {
            pan: $('#pan', m).value.replace(/\s/g, ''), exp: $('#exp', m).value.trim(),
            exp_month: parseInt(mm || '0', 10) || null, exp_year: parseInt(yy || '0', 10) || null,
            cvv: $('#cvv', m).value.trim(), holder: $('#hd', m).value.trim(),
            zip: $('#zp', m).value.trim(), country: $('#co', m).value.trim() || 'US',
            note: $('#nt', m).value.trim(),
          } });
          toast('已保存'); m.remove(); load();
        } catch (e) { toast(e.message, true); }
      };
    });
  }
  async function editDlg(c) {
    let full; try { full = await api('/api/cards/' + c.id + '?reveal=1'); } catch (e) { return toast(e.message, true); }
    const card = full.card || c;
    modal(`<h3>编辑卡 <span class="mono" style="color:var(--muted)">${esc(card.masked)}</span></h3>
      <p class="hint" style="margin:0 0 8px">只改需要改的字段；卡号 / CVV 留空表示不改。</p>
      <label class="f">替换卡号 PAN（留空不改）</label><input id="pan" placeholder="${esc(card.masked)}" autocomplete="off" spellcheck="false" />
      <label class="f">替换 CVV（留空不改）</label><input id="cvv" placeholder="***" autocomplete="off" />
      <label class="f">有效期 MM/YY</label><input id="exp" value="${esc(card.expiry)}" />
      <label class="f">持卡人</label><input id="hd" value="${esc(card.holder || '')}" />
      <label class="f">ZIP</label><input id="zp" value="${esc(card.zip || '')}" />
      <label class="f">国家</label><input id="co" value="${esc(card.country || 'US')}" />
      <label class="f">状态</label><select id="st">${Object.entries(CARD_STATUS).map(([k, v]) => `<option value="${k}" ${card.status === k ? 'selected' : ''}>${v[0]}</option>`).join('')}</select>
      <label class="f">备注</label><input id="nt" value="${esc(card.note || '')}" />
      <div class="foot"><button class="btn" id="cx">取消</button><button class="btn primary" id="ok">保存</button></div>`, m => {
      $('#cx', m).onclick = () => m.remove();
      $('#ok', m).onclick = async () => {
        const body = {
          holder: $('#hd', m).value.trim(), zip: $('#zp', m).value.trim(),
          country: $('#co', m).value.trim(), note: $('#nt', m).value.trim(),
          status: $('#st', m).value,
        };
        const pan = $('#pan', m).value.replace(/\s/g, ''); if (pan) body.pan = pan;
        const cvv = $('#cvv', m).value.trim(); if (cvv) body.cvv = cvv;
        const exp = $('#exp', m).value.trim();
        const mm = exp.match(/^(\d{1,2})\s*\/\s*(\d{2,4})$/);
        if (mm) { body.exp_month = parseInt(mm[1], 10); body.exp_year = parseInt(mm[2], 10); }
        try { await api('/api/cards/' + c.id, { method: 'PATCH', body }); toast('已保存'); m.remove(); load(); }
        catch (e) { toast(e.message, true); }
      };
    });
  }
  await load();
}

async function pageSubs() { location.hash = '#refills'; }


const PAGES = { overview: pageOverview, generate: pageGenerate, chat: pageChat, records: pageRecords, accounts: pageAccounts, cards: pageCards, refills: pageRefills, settings: pageSettings, subs: pageSubs };
api('/api/me').then(route).catch(() => {});
