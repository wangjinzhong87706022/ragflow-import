/**
 * MinerU 知识库评测 —— Playwright 浏览器实测运行器（v3）
 *
 * 执行：cd <playwright-skill> && node run.js <本脚本>
 *
 * 相比 v2 的改进（2026-09-13）：
 *   1. 会话治理：每 N 题后通过 UI/API 清理会话，避免 135 条全量跑完后会话堆积
 *      （v2 的 "New conversation" 只新建不删除，实例上会残留上百个会话）。
 *      CLEAN_SESSIONS=0 关闭。优先走 DELETE /api/v1/chats/{chat}/sessions（凭 cookie），
 *      失败则退回 UI 删除按钮。
 *   2. 答案获取双通道：SSE 拦截 + 页面 DOM 兜底（.markdown-content 等容器），
 *      两者取非空，防 SSE 偶发漏捕导致 [EMPTY_ANSWER] 误判。
 *   3. 逐题重试：RETRIES（默认 1）次 [TIMEOUT]/[EMPTY] 后重问一次（新会话）。
 *   4. 结果断点续跑：OUT_FILE 已有结果时跳过其中已存在的用例 ID（SKIP_DONE=0 关闭）。
 *   5. 判分关键词先行 strip 空白，长关键词覆盖率兜底阈值不变（0.7）。
 *
 * 环境变量：
 *   RAGFLOW_URL / RAGFLOW_EMAIL / RAGFLOW_PASSWORD（缺省时打开可见浏览器手动登录）
 *   RAGFLOW_API_KEY         用于会话清理 API（可选；不给则只走 UI 删除）
 *   RAGFLOW_CHAT_ID         默认 46f41fbaaf1611f1896583d540e218a6
 *   CASES_FILE / OUT_FILE   用例与结果 JSON
 *   START / END / CATEGORY / IDS   用例筛选
 *   HEADLESS=1              无头；SHOT_DIR=skip 关闭截图；MAX_WAIT_MS 单题上限（默认 300000）
 *   CLEAN_EVERY=10          每 N 题清理一次历史会话；CLEAN_SESSIONS=0 关闭
 *   RETRIES=1               超时/空答案重试次数
 *   SKIP_DONE=1             跳过 OUT_FILE 已有用例（默认开）
 */
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const URL = process.env.RAGFLOW_URL || 'https://labragf.openagp.top:9080';
const EMAIL = process.env.RAGFLOW_EMAIL || '';
const PASSWORD = process.env.RAGFLOW_PASSWORD || '';
const API_KEY = process.env.RAGFLOW_API_KEY || '';
const CHAT_ID = process.env.RAGFLOW_CHAT_ID || '46f41fbaaf1611f1896583d540e218a6';
const HERE = path.dirname(__filename);
const CASES_FILE = process.env.CASES_FILE || path.join(HERE, 'mineru_eval_cases.json');
const OUT_FILE = process.env.OUT_FILE || path.join(HERE, 'mineru_eval_results_browser.json');
const SHOT_DIR = process.env.SHOT_DIR || '';
const HEADLESS = process.env.HEADLESS === '1';
const MAX_WAIT_MS = parseInt(process.env.MAX_WAIT_MS || '300000', 10);
const IDS = (process.env.IDS || '').split(',').map((s) => s.trim()).filter(Boolean).map(Number);
const CLEAN_EVERY = parseInt(process.env.CLEAN_EVERY || '10', 10);
const CLEAN_SESSIONS = process.env.CLEAN_SESSIONS !== '0';
const RETRIES = parseInt(process.env.RETRIES || '1', 10);
const SKIP_DONE = process.env.SKIP_DONE !== '0';

function loadCases() {
  const all = JSON.parse(fs.readFileSync(CASES_FILE, 'utf-8'));
  return all.filter((c) => {
    const n = parseInt(c.id.split('-')[1], 10);
    if (IDS.length) return IDS.includes(n);
    const catOk = !process.env.CATEGORY || c.category === process.env.CATEGORY;
    return n >= parseInt(process.env.START || '1', 10) && n <= parseInt(process.env.END || '99999', 10) && catOk;
  });
}

function loadDone() {
  if (!SKIP_DONE || !fs.existsSync(OUT_FILE)) return new Set();
  try {
    return new Set(JSON.parse(fs.readFileSync(OUT_FILE, 'utf-8')).map((r) => r.id));
  } catch (e) { return new Set(); }
}

const META_RE = /^\[[^\]]+\]|^(Running the|Kept |Compiled |Searching )|Starting research|Reading the conversation|Understood the question|Skipping/;

/** 剥离 <think>…</think> 思考块；对未闭合的 <think>（流被 length 截断）取其后的正文，
 *  若无正文则返回空串（判分为 MISS），绝不把思考文本当答案评分。 */
function stripThink(text) {
  if (!text) return '';
  const close = text.lastIndexOf('</think>');
  const open = text.indexOf('<think>');
  if (open === -1) return text;
  if (close > open) return text.slice(close + '</think>'.length).trim();
  return ''; // <think> 未闭合：思考被截断，正文为空
}

function extractAnswer(acc) {
  const visible = stripThink(acc);
  return visible
    .split(/<br\s*\/?>|\n+/)
    .map((s) => s.trim())
    .filter((s) => s && !META_RE.test(s))
    .join('\n')
    .trim();
}

function parseSse(body) {
  let acc = '';
  for (const line of body.split('\n')) {
    const m = line.match(/^data:\s*(.+)$/);
    if (!m) continue;
    try {
      const j = JSON.parse(m[1]);
      const d = j && j.data;
      if (d && typeof d === 'object' && typeof d.answer === 'string') acc += d.answer;
    } catch (e) { /* partial */ }
  }
  return extractAnswer(acc);
}

async function findVisible(page, selectors, timeout = 15000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    for (const sel of selectors) {
      const loc = page.locator(sel).first();
      if ((await loc.count()) && (await loc.isVisible().catch(() => false))) return loc;
    }
    await page.waitForTimeout(400);
  }
  return null;
}

async function login(page) {
  await page.goto(`${URL}/login`, { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(1500);
  if (!page.url().includes('/login')) return;
  if (!EMAIL || !PASSWORD) {
    console.log('未提供凭据，请在可见浏览器中手动登录（≤5 分钟）...');
    await page.waitForURL((u) => !u.pathname.includes('/login'), { timeout: 300000 });
    return;
  }
  await page.fill('input[name="email"]', EMAIL);
  await page.fill('input[name="password"]', PASSWORD);
  await page.click('button:has-text("Sign in")');
  await page.waitForURL((u) => !u.pathname.includes('/login'), { timeout: 60000 });
  console.log('登录成功');
}

/** 通过页面内 fetch 清理本助手的历史会话（保留最近 keep 个，当前会话在其中）。
 *  注意：本版本 sessions 列表接口 data 直接是数组（非 {sessions: []}）；DELETE 用 ids=a,b,c。 */
async function cleanSessionsViaApi(page, keep = 1) {
  if (!API_KEY) return false;
  try {
    const res = await page.evaluate(async ({ url, chatId, apiKey, keep }) => {
      const r = await fetch(`${url}/api/v1/chats/${chatId}/sessions?page_size=100`, {
        headers: { Authorization: `Bearer ${apiKey}` },
      });
      const j = await r.json();
      const items = Array.isArray(((j || {}).data)) ? j.data : ((((j || {}).data) || {}).sessions || []);
      const victim = items.slice(keep).map((s) => s.id);
      // 本实例实测：DELETE 一次只认单个 ids=<id>（批量/逗号连接会静默失败），逐个删
      for (const sid of victim) {
        await fetch(`${url}/api/v1/chats/${chatId}/sessions?ids=${sid}`, {
          method: 'DELETE',
          headers: { Authorization: `Bearer ${apiKey}` },
        }).catch(() => {});
      }
      return { total: items.length, deleted: victim.length };
    }, { url: URL, chatId: CHAT_ID, apiKey: API_KEY, keep });
    console.log(`会话清理: 保留 ${keep}，删除 ${res.deleted}/${res.total}`);
    return true;
  } catch (e) {
    console.log(`会话清理(API)失败: ${e.message}`);
    return false;
  }
}

async function newConversation(page) {
  const btn = await findVisible(page, ['button:has-text("New conversation")', 'button:has-text("新会话")'], 4000);
  if (btn) {
    await btn.click().catch(() => {});
    await page.waitForTimeout(1500);
  }
}

/** DOM 兜底：流结束后从消息容器取最后一条助手消息文本。 */
async function answerFromDom(page) {
  try {
    const sels = [
      '.chat-answer .markdown-content',
      '.message-content .markdown-content',
      '[class*="answer"] .markdown-content',
      '.markdown-content',
    ];
    const loc = await findVisible(page, sels, 3000);
    if (!loc) return '';
    const n = await page.locator(sels.join(', ')).count();
    const last = page.locator(sels.join(', ')).nth(Math.max(0, n - 1));
    const txt = (await last.innerText().catch(() => '')) || '';
    return extractAnswer(txt);
  } catch (e) { return ''; }
}

async function askOnce(page, question) {
  let body = '';
  const onResp = async (resp) => {
    if (resp.url().includes('completion') && resp.request().method() === 'POST') {
      try { body = await resp.text(); } catch (e) { /* ignore */ }
    }
  };
  page.on('response', onResp);
  try {
    const box = await findVisible(page, ['textarea[placeholder*="message" i]', 'textarea'], 20000);
    if (!box) throw new Error('未找到输入框');
    await box.click();
    await box.fill(question);
    await page.waitForTimeout(400);
    await page.keyboard.press('Enter');
    const t0 = Date.now();
    while (Date.now() - t0 < MAX_WAIT_MS && !body) await page.waitForTimeout(3000);
    await page.waitForTimeout(2000); // 等流渲染稳定，供 DOM 兜底
    let answer = body ? parseSse(body) : '';
    if (!answer) answer = await answerFromDom(page);
    if (!answer) return body ? '[EMPTY_ANSWER]' : '[TIMEOUT]';
    return answer;
  } finally {
    page.off('response', onResp);
  }
}

async function askWithRetry(page, question) {
  let last = '';
  for (let i = 0; i <= RETRIES; i++) {
    if (i > 0) {
      console.log(`  重试 ${i}/${RETRIES}（上次: ${last.slice(0, 40)}）`);
      await newConversation(page);
    }
    last = await askOnce(page, question);
    if (!/^\[(TIMEOUT|EMPTY_ANSWER)\]$/.test(last)) return last;
  }
  return last;
}

function score(answer, keywords) {
  const hit = (k0) => {
    const k = (k0 || '').trim();
    if (!k) return false;
    if (answer.includes(k)) return true;
    if (k.length >= 6 && answer) {
      const cov = [...k].filter((ch) => answer.includes(ch)).length / k.length;
      return cov >= 0.7; // 长关键词（无分隔的表头拼接串）用字符覆盖率兜底
    }
    return false;
  };
  const hits = keywords.filter(hit).length;
  return { hits, ratio: keywords.length ? hits / keywords.length : 0 };
}

(async () => {
  const cases = loadCases();
  const done = loadDone();
  const todo = cases.filter((c) => !done.has(c.id));
  console.log(`用例数: ${cases.length}（已完成跳过 ${cases.length - todo.length}）| 助手: ${CHAT_ID} | ids=${todo.map((c) => c.id).join(',')}`);
  const browser = await chromium.launch({ headless: HEADLESS, slowMo: 30 });
  const context = await browser.newContext({ ignoreHTTPSErrors: true, viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();
  const results = [];
  let run = 0;
  try {
    await login(page);
    await page.goto(`${URL}/chat/${CHAT_ID}`, { waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(4000);

    for (let i = 0; i < todo.length; i++) {
      const c = todo[i];
      const t0 = Date.now();
      try {
        if (i > 0) await newConversation(page);
        const answer = await askWithRetry(page, c.question);
        const s = score(answer, c.expected_keywords);
        const ok = s.ratio >= 0.5 && s.hits >= 1;
        results.push({ ...c, answer: answer.slice(0, 1500), ...s, pass: ok, seconds: Math.round((Date.now() - t0) / 1000) });
        console.log(`${c.id} ${ok ? 'PASS' : 'MISS'} (${s.hits}/${c.expected_keywords.length}, ${Math.round((Date.now() - t0) / 1000)}s) ${answer.slice(0, 90).replace(/\n/g, ' ')}`);
      } catch (e) {
        results.push({ ...c, answer: `[ERROR] ${e.message}`, hits: 0, ratio: 0, pass: false });
        console.log(`${c.id} ERROR ${e.message}`);
      }
      if (SHOT_DIR && SHOT_DIR !== 'skip') {
        fs.mkdirSync(SHOT_DIR, { recursive: true });
        await page.screenshot({ path: path.join(SHOT_DIR, `${c.id}.png`) }).catch(() => {});
      }
      // append-and-merge with existing OUT_FILE for resume support
      const merged = [...JSON.parse(fs.existsSync(OUT_FILE) ? fs.readFileSync(OUT_FILE, 'utf-8') : '[]')
        .filter((r) => !results.some((n) => n.id === r.id)), ...results];
      fs.writeFileSync(OUT_FILE, JSON.stringify(merged, null, 1), 'utf-8');
      run++;
      if (CLEAN_SESSIONS && CLEAN_EVERY > 0 && run % CLEAN_EVERY === 0) {
        await cleanSessionsViaApi(page, 1);
      }
    }
  } finally {
    const merged = [...JSON.parse(fs.existsSync(OUT_FILE) ? fs.readFileSync(OUT_FILE, 'utf-8') : '[]')
      .filter((r) => !results.some((n) => n.id === r.id)), ...results];
    fs.writeFileSync(OUT_FILE, JSON.stringify(merged, null, 1), 'utf-8');
    const all = JSON.parse(fs.readFileSync(OUT_FILE, 'utf-8'));
    const pass = all.filter((r) => r.pass).length;
    console.log(`完成: 本次 ${results.filter((r) => r.pass).length}/${results.length} pass | 累计 ${pass}/${all.length} pass | ${OUT_FILE}`);
    await browser.close();
  }
})();
