// Port of src/pipeline.py — core Q&A pipeline for Cloudflare Workers / D1
import { normClause } from './_zhnum.js';
import {
  severance, unlawfulDamages, annualLeavePayout,
  statutoryAnnualDays, exitProratedUnusedDays, dateDiffDays
} from './_calc.js';

export const REGIONS = ["上海","江苏","浙江","北京","天津","河北","广东",
  "广州","深圳","南京","无锡","常州","苏州","杭州","宁波"];

const REFUSE_CONCLUSION = "这个问题超出当前知识库可靠回答的范围（依据不足或属于个案争议）。为避免给出不准确的答案，建议转交合作律师处理。";
const PRE2008_CONCLUSION = "员工入职早于 2008-01-01（劳动合同法施行日），经济补偿需分段计算，各地口径差异大（上海等地有特殊规则）。为避免算错，建议转交合作律师核算。";

const MEDICAL_RE = /医疗期|病假|患病|生病|住院|动手术|做了?手术|手术后?|癌|肿瘤|脑积水|尿毒症|重病|绝症|非因工负伤|精神病|化疗|透析/;
const FIRE_RE = /开除|辞退|解雇|炒(?:掉|了)|单方解除|劝退|fire/i;

function nowIso() { return new Date().toISOString().replace(/\.\d{3}Z$/, 'Z'); }

// ============ LLM ============

export async function chatJson(env, messages, opts = {}) {
  const key = env.DEEPSEEK_API_KEY;
  if (!key) return null;
  try {
    const resp = await fetch('https://api.deepseek.com/chat/completions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${key}` },
      body: JSON.stringify({
        model: env.DEEPSEEK_MODEL || 'deepseek-chat',
        messages,
        temperature: opts.temperature ?? 0.1,
        max_tokens: opts.maxTokens || 1200,
        response_format: { type: 'json_object' }
      }),
      signal: AbortSignal.timeout((opts.timeout || 45) * 1000)
    });
    const data = await resp.json();
    if (data.error) return null;
    const content = data.choices?.[0]?.message?.content;
    return content ? JSON.parse(content) : null;
  } catch { return null; }
}

// ============ FTS + retrieval ============

function bigrams(s) {
  const cjk = s.replace(/[^一-鿿]/g, '');
  return [...new Set([...Array(Math.max(0, cjk.length-1)).keys()].map(i => cjk.slice(i, i+2)))];
}

async function regionChain(db, regionName) {
  const chain = [1];
  if (regionName) {
    const row = await db.prepare("SELECT id, parent_id FROM region WHERE name = ?").bind(regionName).first();
    if (row) {
      chain.push(row.id);
      if (row.parent_id && row.parent_id !== 1) chain.push(row.parent_id);
    }
  }
  return chain;
}

export async function retrieve(db, question, regionName, k = 4) {
  const chain = await regionChain(db, regionName);
  const grams = bigrams(question);
  let whereIds = '';
  if (grams.length) {
    const ftsQuery = grams.slice(0, 24).map(g => `"${g}"`).join(' OR ');
    try {
      const fr = await db.prepare("SELECT rowid FROM fts_article WHERE seg MATCH ? ORDER BY rank LIMIT 50").bind(ftsQuery).all();
      if (fr.results.length) whereIds = `AND la.id IN (${fr.results.map(r => r.rowid).join(',')})`;
    } catch { /* FTS unavailable — full scan */ }
  }
  const ph = chain.map(() => '?').join(',');
  const { results } = await db.prepare(
    `SELECT ls.title, la.article_no, la.clause_no, la.text, la.verified, r.name AS region
     FROM legal_article la JOIN legal_source ls ON ls.id=la.source_id
     JOIN region r ON r.id=ls.region_id
     WHERE ls.region_id IN (${ph}) AND la.status='active' ${whereIds}`
  ).bind(...chain).all();
  const gs = new Set(grams);
  return results
    .map(r => ({ source:r.title, article:r.article_no, clause:r.clause_no,
                 text:r.text, verified:!!r.verified, region:r.region,
                 score:[...gs].filter(g => r.text.includes(g)).length }))
    .filter(r => r.score > 0)
    .sort((a,b) => b.score - a.score)
    .slice(0, k);
}

async function retrieveCases(db, question, regionName, k = 2) {
  const chain = await regionChain(db, regionName);
  const grams = bigrams(question);
  let whereIds = '';
  if (grams.length) {
    const ftsQuery = grams.slice(0, 24).map(g => `"${g}"`).join(' OR ');
    try {
      const fr = await db.prepare("SELECT rowid FROM fts_case WHERE seg MATCH ? ORDER BY rank LIMIT 30").bind(ftsQuery).all();
      if (fr.results.length) whereIds = `AND c.id IN (${fr.results.map(r => r.rowid).join(',')})`;
    } catch { return []; }
  }
  const ph = chain.map(() => '?').join(',');
  const { results } = await db.prepare(
    `SELECT c.id, c.case_no, c.court, c.gist, c.facts_summary, c.result,
            c.license_note, c.verified, r.name AS region
     FROM case_record c JOIN region r ON r.id=c.region_id
     WHERE c.region_id IN (${ph}) ${whereIds}`
  ).bind(...chain).all();
  const gs = new Set(grams);
  return results
    .map(r => {
      const text = (r.gist||'') + (r.facts_summary||'');
      const score = [...gs].filter(g => text.includes(g)).length;
      const [title,,sourceNote] = (r.license_note||'').split('｜');
      return { ...r, title:title||'（未命名案例）', source_note:sourceNote, score };
    })
    .filter(r => r.score >= 2)
    .sort((a,b) => b.score - a.score)
    .slice(0, k);
}

// ============ Citation resolution ============

export async function resolveCitations(db, refs) {
  const out = [], unresolved = [], seen = new Set();
  for (const ref of refs) {
    const r = String(ref).trim();
    if (seen.has(r)) continue;
    seen.add(r);
    const m = r.match(/《(.+?)》(第.+?条)(?:第(.+?)款)?$/);
    if (!m) { unresolved.push(r); continue; }
    const clause = normClause(m[3]);
    const row = await db.prepare(
      `SELECT ls.title, la.article_no, la.clause_no, la.text, la.verified, r.name AS region
       FROM legal_article la JOIN legal_source ls ON ls.id=la.source_id
       JOIN region r ON r.id=ls.region_id
       WHERE ls.title=? AND la.article_no=? AND (? IS NULL OR la.clause_no=?)`
    ).bind(m[1], m[2], clause, clause).first();
    if (row) out.push({ source:row.title, article:row.article_no, clause:row.clause_no,
                        text:row.text, verified:!!row.verified, region:row.region });
    else unresolved.push(r);
  }
  return [out, unresolved];
}

// ============ Regional params ============

export async function fetchParam(db, regionName, key) {
  let name = regionName;
  for (let i = 0; i < 3; i++) {
    const row = await db.prepare(
      `SELECT rp.value, rp.period, rp.verified, r.name FROM region_param rp
       JOIN region r ON r.id=rp.region_id
       WHERE r.name=? AND rp.param_key=? ORDER BY rp.period DESC LIMIT 1`
    ).bind(name, key).first();
    if (row) return { value:JSON.parse(row.value), period:row.period, verified:!!row.verified,
                      region_used:row.name, fallback:row.name !== regionName };
    const parent = await db.prepare(
      `SELECT p.name FROM region r JOIN region p ON p.id=r.parent_id WHERE r.name=?`
    ).bind(name).first();
    if (!parent || parent.name === '全国') return null;
    name = parent.name;
  }
  return null;
}

// ============ Fact extraction ============

const EXTRACT_SYS = `你是企业劳动法咨询系统的要素抽取器。从用户问题中抽取结构化要素，只输出 JSON。
字段：
- intent: "severance"（经济补偿/协商解除/裁员补偿的金额测算）| "unlawful_damages"（违法解除赔偿金/2N 测算）| "annual_leave"（年假天数或未休年假折算测算）| "concept"（规则/概念解释类提问，不要求算钱）| "other"
- region: 用工所在地，只能取 ${JSON.stringify(REGIONS)} 之一，否则 null
- monthly_wage: 月薪数字（元），没有则 null
- hire_date: "YYYY-MM-DD"，只说到年月则取当月 1 日，没有则 null
- term_date: "YYYY-MM-DD"，没有则 null
- cumulative_years: 累计工龄（年，数字），没有则 null
- taken_days: 今年已休年假天数，没有则 null
规则：不确定一律 null，禁止编造或推测。只输出 JSON。`;

function toFloat(v) {
  if (v == null) return null;
  if (typeof v === 'number') return v;
  const s = String(v).trim().replace(/[,，元￥]/g, '');
  const wan = s.match(/^(\d+(?:\.\d+)?)\s*万$/);
  if (wan) return parseFloat(wan[1]) * 10000;
  const n = parseFloat(s);
  return isNaN(n) ? null : n;
}

function regexFacts(text) {
  const facts = {};
  for (const r of REGIONS) { if (text.includes(r)) { facts.region = r; break; } }
  const wm = text.match(/月薪[约为是]?\s*([\d,，]+)/) || text.match(/([\d,，]{4,})\s*元/);
  if (wm) facts.monthly_wage = toFloat(wm[1]);
  const dates = [...text.matchAll(/(\d{4})\s*年\s*(\d{1,2})\s*月(?:\s*(\d{1,2})\s*日)?/g)];
  if (dates[0]) {
    const [,y,mo,d] = dates[0];
    facts.hire_date = `${y}-${mo.padStart(2,'0')}-${(d||'01').padStart(2,'0')}`;
  }
  if (dates[1]) {
    const [,y,mo,d] = dates[1];
    facts.term_date = `${y}-${mo.padStart(2,'0')}-${(d||'01').padStart(2,'0')}`;
  }
  const ym = text.match(/(?:工龄|工作了?)\s*(\d+)\s*年/);
  if (ym) facts.cumulative_years = parseFloat(ym[1]);
  if (/违法解除|赔偿金|2\s*N/i.test(text)) facts.intent = 'unlawful_damages';
  else if (/协商解除|经济补偿|补偿|裁员/.test(text)) facts.intent = 'severance';
  else if (/年假|年休假/.test(text)) facts.intent = 'annual_leave';
  else facts.intent = 'other';
  return facts;
}

export async function extractFacts(env, question) {
  const llmResult = await chatJson(env, [
    { role:'system', content:EXTRACT_SYS },
    { role:'user', content:question }
  ], { maxTokens:300 });
  if (llmResult && typeof llmResult === 'object') {
    const facts = Object.fromEntries(Object.entries(llmResult).filter(([,v]) => v != null));
    for (const f of ['monthly_wage','cumulative_years','taken_days']) {
      if (f in facts) { const c = toFloat(facts[f]); if (c == null) delete facts[f]; else facts[f] = c; }
    }
    if (!REGIONS.includes(facts.region)) delete facts.region;
    if (!['severance','unlawful_damages','annual_leave','concept','other'].includes(facts.intent)) facts.intent = 'other';
    return [facts, true];
  }
  return [regexFacts(question), false];
}

export function mergeFacts(stored, newFacts) {
  const merged = { ...stored };
  for (const [k, v] of Object.entries(newFacts)) {
    if (v == null) continue;
    if (k === 'intent' && v === 'other' && stored.intent && stored.intent !== 'other') continue;
    merged[k] = v;
  }
  return merged;
}

// ============ Entry matching ============

export async function matchEntry(db, question) {
  const grams = new Set(bigrams(question));
  const ql = question.toLowerCase();
  const { results } = await db.prepare("SELECT slug, title, body FROM entry WHERE status != 'archived'").all();
  let best = null, bestScore = 0;
  for (const e of results) {
    const kws = (JSON.parse(e.body).keywords || []);
    const kwHits = kws.filter(k => k && ql.includes(k.toLowerCase())).length;
    if (!kwHits) continue;
    const score = kwHits * 3 + [...grams].filter(g => (e.title + kws.join('')).includes(g)).length;
    if (score > bestScore) { best = e.slug; bestScore = score; }
  }
  return best;
}

export async function entryBySlug(db, slug) {
  const e = await db.prepare("SELECT id, title, body, status, basis_date FROM entry WHERE slug=?").bind(slug).first();
  if (!e) return null;
  const { results: cites } = await db.prepare(
    `SELECT ls.title, la.article_no, la.clause_no, la.text, la.verified, r.name
     FROM entry_citation ec JOIN legal_article la ON la.id=ec.article_id
     JOIN legal_source ls ON ls.id=la.source_id JOIN region r ON r.id=ls.region_id
     WHERE ec.entry_id=?`
  ).bind(e.id).all();
  return { id:e.id, title:e.title, body:JSON.parse(e.body), status:e.status, basis_date:e.basis_date,
    citations: cites.map(r => ({ source:r.title, article:r.article_no, clause:r.clause_no,
                                  text:r.text, verified:!!r.verified, region:r.name })) };
}

// ============ Session (multi-turn T2.4) ============

export async function sessionFacts(db, sessionId) {
  if (!sessionId) return {};
  try {
    const row = await db.prepare("SELECT facts FROM qa_session WHERE id=?").bind(sessionId).first();
    return (row?.facts) ? JSON.parse(row.facts) : {};
  } catch { return {}; }
}

export async function ensureSession(db, sessionId, regionId) {
  try {
    if (sessionId) {
      const row = await db.prepare("SELECT 1 FROM qa_session WHERE id=?").bind(sessionId).first();
      if (row) return sessionId;
    }
    const res = await db.prepare("INSERT INTO qa_session(region_id, created_at) VALUES (?,?)")
      .bind(regionId ?? null, nowIso()).run();
    return res.meta.last_row_id;
  } catch { return null; }
}

async function logAnswer(db, sessionId, question, facts, res) {
  try {
    let rid = null;
    if (res.region) {
      const r = await db.prepare("SELECT id FROM region WHERE name=?").bind(res.region).first();
      rid = r?.id ?? null;
    }
    await db.prepare("UPDATE qa_session SET facts=?, region_id=? WHERE id=?")
      .bind(JSON.stringify(Object.fromEntries(Object.entries(facts).filter(([,v])=>v!=null))), rid, sessionId).run();
    await db.prepare("INSERT INTO qa_message(session_id,role,content,created_at) VALUES (?,?,?,?)")
      .bind(sessionId, 'user', question, nowIso()).run();
    let hitEntry = null;
    if (res.entry?.slug) {
      const er = await db.prepare("SELECT id FROM entry WHERE slug=?").bind(res.entry.slug).first();
      hitEntry = er?.id ?? null;
    }
    const confidence = res.route === 'entry_hit' || res.route === 'calculator' ? 1.0
      : res.route === 'rag' ? 0.7 : res.route === 'open' ? 0.4 : 0.0;
    await db.prepare(
      `INSERT INTO qa_message(session_id,role,content,facts,route,hit_entry_id,calculator_key,citations,confidence,escalated,created_at)
       VALUES (?,?,?,?,?,?,?,?,?,?,?)`
    ).bind(sessionId, 'assistant', res.conclusion,
      JSON.stringify(Object.fromEntries(Object.entries(facts).map(([k,v])=>[k,String(v)]))),
      res.route, hitEntry, res.calculator_key ?? null,
      JSON.stringify((res.citations||[]).map(c => `《${c.source}》${c.article}`)),
      confidence, res.escalate ? 1 : 0, nowIso()
    ).run();
  } catch { /* log failure is non-fatal */ }
}

// ============ RAG answer ============

const RAG_SYS = `你是面向企业 HR 的劳动法助手。严格遵守：
1. 只能依据下面提供的【条文】和【案例】回答，禁止使用其外的任何知识、经验或记忆；
2. 只输出 JSON：{"refuse": false, "conclusion": "一句话结论", "analysis": "简要分析（180 字内）", "citations": ["《法规名》第X条", ...], "case_refs": [1]}
3. citations 只能引用提供的条文，写法必须与提供的《法规名》第X条完全一致；case_refs 为引用的案例编号数组，没引用就 []；
4. 案例只能用于说明裁判倾向，不得当作法律规定本身；提及案例时用"参考案例N"，禁止编造案号；
5. 条文不足以可靠回答时，输出 {"refuse": true}，不要勉强作答；
6. 不得出现条文、案例或问题中没有的数字或金额。`;

async function ragAnswer(db, env, question, regionName) {
  const ctx = await retrieve(db, question, regionName);
  if (!ctx.length || ctx[0].score < 2) return null;
  const cases = await retrieveCases(db, question, regionName);
  let ctxBlock = ctx.map((c,i) =>
    `【条文 ${i+1}】《${c.source}》${c.article}${c.clause?'第'+c.clause+'款':''}\n${c.text}`
  ).join('\n\n');
  if (cases.length) ctxBlock += '\n\n' + cases.map((c,i) =>
    `【案例 ${i+1}】${c.title}（${c.source_note||'官方发布'}）\n裁判要旨：${c.gist}`
  ).join('\n\n');
  const out = await chatJson(env, [
    { role:'system', content:RAG_SYS },
    { role:'user', content:`问题：${question}\n\n可用材料：\n${ctxBlock}` }
  ], { maxTokens:700 });
  if (!out || out.refuse || !out.conclusion) return null;
  const rawCites = Array.isArray(out.citations) ? out.citations : [];
  const provided = new Set(ctx.map(c => `${c.source}|${c.article}`));
  const validRefs = rawCites.filter(ref => {
    const m = String(ref).match(/《(.+?)》(第.+?条)(第.+?款)?/);
    return m && provided.has(`${m[1]}|${m[2]}`);
  }).map(ref => {
    const m = String(ref).match(/《(.+?)》(第.+?条)(第.+?款)?/);
    return `《${m[1]}》${m[2]}${m[3]||''}`;
  });
  if (!validRefs.length) return null;
  // Number traceability check
  const answerText = (String(out.conclusion) + String(out.analysis||'')).replace(/,/g, '');
  const allowedNums = new Set([
    ...[...ctx.map(c=>c.text), ...cases.map(c=>c.gist||''), question]
      .flatMap(t => [...t.matchAll(/\d+(?:\.\d+)?/g)].map(m => m[0]))
  ]);
  for (const num of answerText.matchAll(/\d+(?:\.\d+)?/g)) {
    if (!allowedNums.has(num[0])) return null;
  }
  const caseRefs = Array.isArray(out.case_refs) ? out.case_refs : [];
  const usedCases = caseRefs.filter(i => typeof i==='number' && i>=1 && i<=cases.length).map(i => cases[i-1]);
  const [resolved] = await resolveCitations(db, validRefs);
  if (!resolved.length) return null;
  return { conclusion:out.conclusion, analysis:out.analysis||'', citations:resolved, cases:usedCases };
}

// ============ 放开作答（用户授权：超纲不硬拒，给方案 + 标出处）============
// 安全机制 = 模型提议、库来核验：模型给的法条逐条过 resolveCitations，
// 库里有的进 citations（真·已核验），库里没有的进 ai_refs（标 AI·未核验，不冒充库内）。
// 守底线：涉钱仍只走 calculators（prompt 禁止给金额）；编造的法条隔离标注，不进已核验引用。
const OPEN_SYS = `你是面向企业 HR 的资深劳动法专家。请务必直接给出可操作的解决方案，不要回避、不要只说"咨询律师"。要求：
1. 先给一句话结论，再给简要分析，最后给 2-4 条可操作步骤；
2. 必须标注法律依据——列出你依据的具体法条，写法严格为「《法规全称》第X条」（如《中华人民共和国劳动合同法》第八十二条）；没有十足把握的条号宁可不写，绝不编造；
3. 只输出 JSON：{"conclusion":"一句话结论","analysis":"简要分析（200字内）","steps":["步骤1","步骤2"],"citations":["《中华人民共和国劳动合同法》第八十二条"]}
4. 禁止给出任何具体金额的计算结果或数字答案；涉及金额只描述法定规则（如"二倍工资""N+1""1.5 倍加班费"），具体数额一律提示改用"算钱"工具或咨询律师；
5. 这是一般法律信息，不构成正式法律意见。`;

async function openAnswer(db, env, question, regionName) {
  const out = await chatJson(env, [
    { role:'system', content:OPEN_SYS },
    { role:'user', content:`地区：${regionName||'全国'}\n问题：${question}` }
  ], { maxTokens:800 });
  if (!out || !out.conclusion || !String(out.conclusion).trim()) return null;
  const refs = Array.isArray(out.citations) ? out.citations.map(String) : [];
  const [resolved, unresolved] = await resolveCitations(db, refs);
  const steps = (Array.isArray(out.steps) ? out.steps : [])
    .map(s => String(s).trim()).filter(Boolean).slice(0, 6);
  return { conclusion:String(out.conclusion), analysis:String(out.analysis||''),
    steps, citations:resolved, ai_refs:unresolved };
}

// ============ Medical guard ============

async function medicalGuard(db, question, facts, hireStr, termStr, res, intent) {
  if (!facts.medical_context && !MEDICAL_RE.test(question)) return;
  const unitYears = dateDiffDays(hireStr, termStr) / 365.25;
  const total = facts.cumulative_years;
  const tv = total != null ? parseFloat(total) : unitYears;
  let mp;
  if (tv < 10) mp = unitYears < 5 ? 3 : 6;
  else if (unitYears < 5) mp = 6;
  else if (unitYears < 10) mp = 9;
  else if (unitYears < 15) mp = 12;
  else if (unitYears < 20) mp = 18;
  else mp = 24;
  const extraRefs = ['《中华人民共和国劳动合同法》第四十二条','《中华人民共和国劳动合同法》第四十条',
    '《企业职工患病或非因工负伤医疗期规定》第三条','《企业职工患病或非因工负伤医疗期规定》第四条'];
  const [cites] = await resolveCitations(db, extraRefs);
  const seen = new Set(res.citations.map(c => `${c.source}|${c.article}|${c.clause||''}`));
  res.citations.push(...cites.filter(c => !seen.has(`${c.source}|${c.article}|${c.clause||''}`)));
  res.warnings.push(
    `患病/病假语境：${total!=null?'按总工龄 '+total+' 年':'按总工龄≈本单位工龄估算'}、本单位约 ${unitYears.toFixed(1)} 年 → 法定医疗期 ${mp} 个月（劳部发〔1994〕479号第三条）`,
    '医疗期内不得依《劳动合同法》第四十条、第四十一条单方解除（第四十二条）；此时强行解除属违法解除，按第八十七条支付 2N 赔偿金',
    '合规路径：① 协商一致解除（第三十六条，N，金额可谈）；② 医疗期满后不能从事原工作也不能从事另行安排工作的，依第四十条第一项解除（N + 1 个月代通知金）'
  );
  if (res.region === '上海') {
    res.warnings.push('上海口径：医疗期满解除的，另需支付不低于 6 个月工资的医疗补助费（该地方依据库内暂缺，请律师核验）');
  }
  if (intent === 'severance' && FIRE_RE.test(question) && res.amount) {
    res.warnings.push(`注意：当前金额为协商解除口径 N；若在医疗期内被认定单方违法解除，风险敞口为 2N ≈ ${new Intl.NumberFormat('zh-CN').format(res.amount * 2)} 元`);
  }
  res.escalate = true;
}

// ============ Main answer router ============

export async function answer(db, env, question, defaultRegion, sessionId) {
  const [newFacts, llmUsed] = await extractFacts(env, question);
  if (MEDICAL_RE.test(question)) newFacts.medical_context = true;
  if (FIRE_RE.test(question)) newFacts.fire_context = true;
  const stored = await sessionFacts(db, sessionId);
  let facts = mergeFacts(stored, newFacts);
  if (!facts.region && REGIONS.includes(defaultRegion)) { facts.region = defaultRegion; facts.region_defaulted = true; }
  const region = facts.region;
  const regionRow = region ? await db.prepare("SELECT id FROM region WHERE name=?").bind(region).first() : null;
  const sid = await ensureSession(db, sessionId, regionRow?.id);

  const res = { route:'refuse', llm_used:llmUsed, conclusion:REFUSE_CONCLUSION,
    steps:[], amount:null, analysis:null, citations:[], cases:[], region,
    warnings:[], clarify:[], entry:null, escalate:false, session_id:sid,
    ai_refs:[], verified:true };

  let intent = facts.intent || 'other';
  const fire = !!(facts.fire_context || FIRE_RE.test(question));
  const moneyAsk = /多少钱|给多少|怎么[赔补]|补偿|赔偿|测算|算一下/.test(question);
  if (['other','concept'].includes(intent) && fire && moneyAsk) intent = 'severance';
  if (fire && facts.medical_context && ['severance','other','concept'].includes(intent)) intent = 'unlawful_damages';
  facts.intent = intent;

  async function finish() {
    if (facts.region_defaulted && ['calculator','rag','open'].includes(res.route)) {
      res.warnings.push(`地区取自页面默认设置（${region}），请确认实际用工所在地`);
    }
    const local = [...new Set(res.citations.filter(c => c.region && c.region !== '全国').map(c => c.region))];
    if (local.length) res.warnings.push(`本回答含地方性依据，适用地区：${local.join('、')}`);
    await logAnswer(db, sid, question, facts, res);
    return res;
  }

  // —— calculator routes ——
  if (['severance','unlawful_damages'].includes(intent)) {
    const need = [];
    if (!region) need.push({ field:'region', type:'region', label:'用工所在城市', hint:'各地社平封顶口径不同' });
    if (!facts.monthly_wage) need.push({ field:'monthly_wage', type:'number', label:'离职前 12 个月平均应发月工资', hint:'元' });
    if (!facts.hire_date) need.push({ field:'hire_date', type:'date', label:'入职日期' });
    if (need.length) { Object.assign(res, { route:'clarify', clarify:need, conclusion:'为了算得准，请先补充以下要素：' }); return finish(); }
    if (facts.hire_date < '2008-01-01') { Object.assign(res, { route:'refuse', escalate:true, conclusion:PRE2008_CONCLUSION }); return finish(); }
    const termStr = facts.term_date || new Date().toISOString().slice(0,10);
    const p = await fetchParam(db, region, 'social_avg_wage_monthly');
    const social = p ? p.value.amount : null;
    let socialNote = '';
    if (p) {
      if (!p.verified) socialNote = `（⚠ 社平为近似值待核验，口径：${p.region_used} ${p.period}）`;
      if (p.fallback) res.warnings.push(`未配置 ${region} 市级社平工资，封顶校验按 ${p.region_used} 省级口径，法定口径为设区市级，结果可能偏差`);
    }
    const calc = intent === 'unlawful_damages'
      ? unlawfulDamages(facts.hire_date, termStr, parseFloat(facts.monthly_wage), social)
      : severance(facts.hire_date, termStr, parseFloat(facts.monthly_wage), social, socialNote);
    const [cites] = await resolveCitations(db, calc.citations);
    Object.assign(res, { route:'calculator', amount:calc.amount, steps:calc.steps,
      warnings:[...calc.warnings, ...res.warnings], citations:cites,
      conclusion:`按现有要素测算，应支付 ${new Intl.NumberFormat('zh-CN').format(calc.amount)} 元（${intent==='unlawful_damages'?'违法解除赔偿金 2N':'经济补偿 N'}）。`,
      calculator_key:calc.key });
    await medicalGuard(db, question, facts, facts.hire_date, termStr, res, intent);
    return finish();
  }

  if (intent === 'annual_leave') {
    const need = [];
    if (!facts.monthly_wage) need.push({ field:'monthly_wage', type:'number', label:'月工资', hint:'元' });
    if (facts.cumulative_years == null) need.push({ field:'cumulative_years', type:'number', label:'累计工龄', hint:'年，含此前单位年限' });
    if (need.length) { Object.assign(res, { route:'clarify', clarify:need, conclusion:'为了算得准，请先补充以下要素：' }); return finish(); }
    const years = parseFloat(facts.cumulative_years);
    const taken = parseFloat(facts.taken_days || 0);
    const termStr = facts.term_date || new Date().toISOString().slice(0,10);
    const hireStr = facts.hire_date;
    const annual = statutoryAnnualDays(years);
    const yearStart = termStr.slice(0,4) + '-01-01';
    let passed, baseNote;
    if (hireStr && hireStr.slice(0,4) === termStr.slice(0,4) && hireStr > yearStart) {
      passed = dateDiffDays(hireStr, termStr) + 1; baseNote = `自当年入职日 ${hireStr} 起算`;
    } else {
      passed = dateDiffDays(yearStart, termStr) + 1; baseNote = '按全年在职折算';
      if (!hireStr) res.warnings.push('未提供入职日期，按全年在职折算；如系当年入职请补充入职日期重算');
    }
    const unused = exitProratedUnusedDays(passed, annual, taken);
    const calc = annualLeavePayout(parseFloat(facts.monthly_wage), unused);
    calc.steps.unshift(`累计工龄 ${years} 年 → 全年应休 ${annual} 天；${baseNote}已过 ${passed} 天，已休 ${taken} 天 → 应付未休 ${unused} 天`);
    if (!facts.taken_days) calc.warnings.push('今年已休天数按 0 计，如已休过年假请补充后重算');
    const [cites] = await resolveCitations(db, calc.citations);
    Object.assign(res, { route:'calculator', amount:calc.amount, steps:calc.steps,
      warnings:[...calc.warnings, ...res.warnings], citations:cites,
      conclusion:`离职折算未休年假 ${unused} 天，企业额外应补 ${new Intl.NumberFormat('zh-CN').format(calc.amount)} 元（200% 口径；含正常工资的 300% 总额见计算过程）。`,
      calculator_key:calc.key });
    return finish();
  }

  // —— concept / open questions ——
  const slug = await matchEntry(db, question);
  if (slug) {
    const e = await entryBySlug(db, slug);
    if (e) {
      const warnings = [...(e.body.pitfalls || [])];
      if (e.status !== 'published') warnings.unshift(`本词条状态为「${e.status}」，尚未完成律师审核（演示数据）`);
      Object.assign(res, { route:'entry_hit', conclusion:e.body.conclusion,
        citations:e.citations, warnings, entry:{ title:e.title, slug, how_to:e.body.how_to || [] } });
      return finish();
    }
  }

  const rag = env.DEEPSEEK_API_KEY ? await ragAnswer(db, env, question, region) : null;
  if (rag) {
    Object.assign(res, { route:'rag', conclusion:rag.conclusion, analysis:rag.analysis,
      citations:rag.citations, cases:rag.cases,
      warnings:['本回答由检索 + 生成产生，已通过引用存在性、数字溯源与案号校验；重要决策前建议人工复核或转律师确认'] });
    return finish();
  }

  // —— 放开作答兜底（用户授权）：超纲不硬拒，给方案 + 出处分两档标注 ——
  const opened = env.DEEPSEEK_API_KEY ? await openAnswer(db, env, question, region) : null;
  if (opened) {
    let note = '本回答为 AI 基于通用法律知识生成的参考方案。';
    if (opened.citations.length) note += '标「库内依据」的法条已在本库核对存在；';
    if (opened.ai_refs.length) note += '标「AI 标注」的法条本库未收录、未经逐字核验，请自行核对原文；';
    note += '本回答不构成正式法律意见，重要决策请咨询执业律师。';
    Object.assign(res, { route:'open', verified:false, conclusion:opened.conclusion,
      analysis:opened.analysis, steps:opened.steps, citations:opened.citations,
      ai_refs:opened.ai_refs, escalate:false, warnings:[note] });
    return finish();
  }

  // LLM 不可用等极端情况才硬拒（规则引擎降级态）
  Object.assign(res, { route:'refuse', escalate:true });
  return finish();
}
