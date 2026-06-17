import { retrieve, resolveCitations, chatJson } from '../_pipeline.js';

function json(obj, status = 200) {
  return new Response(JSON.stringify(obj), {
    status, headers: { 'Content-Type': 'application/json; charset=utf-8', 'Access-Control-Allow-Origin': '*' }
  });
}

// Inline DOC_TYPES (only title/guide/fields/query needed)
const DOC_TYPES = {
  mutual_termination: { title:'协商解除劳动合同协议书', query:'协商一致解除劳动合同 经济补偿 工作交接',
    guide:'依据《劳动合同法》第三十六条协商一致解除。写明：解除日期、经济补偿金额与支付时间、工资结算、社保公积金停缴结转、工作交接、保密义务延续、双方再无其他劳动争议条款。',
    fields:[{key:'company',label:'公司名称'},{key:'employee',label:'员工姓名'},{key:'position',label:'岗位'},
      {key:'hire_date',label:'入职日期'},{key:'term_date',label:'协商离职日期'},{key:'compensation',label:'经济补偿金额（元）'},{key:'note',label:'其他约定 / 情况说明'}] },
  dismissal_notice: { title:'解除劳动合同通知书', query:'用人单位 解除劳动合同 通知 工会 提前三十日 严重违反规章制度',
    guide:'单方解除务必写明事实与法律依据（对应《劳动合同法》第三十九 / 四十条的具体情形）、解除日期、工资与经济补偿结算、工作交接、离职证明开具。提示用人单位应事先将解除理由通知工会。',
    fields:[{key:'company',label:'公司名称'},{key:'employee',label:'员工姓名'},{key:'position',label:'岗位'},
      {key:'basis',label:'解除事由与依据'},{key:'term_date',label:'解除日期'},{key:'note',label:'交接与结算安排'}] },
  warning_letter: { title:'违纪警告处分通知书', query:'严重违反 规章制度 劳动纪律 处分',
    guide:'写明：违纪事实（时间地点行为）、违反的制度条款名称与编号、处分决定、整改要求与期限、再犯后果、员工申辩权利与签收栏。',
    fields:[{key:'company',label:'公司名称'},{key:'employee',label:'员工姓名'},{key:'department',label:'部门'},
      {key:'fact',label:'违纪事实'},{key:'rule',label:'违反的制度条款'},{key:'demand',label:'整改要求'}] },
  return_to_work: { title:'催告返岗通知书', query:'旷工 严重违反规章制度 解除劳动合同',
    guide:'写明：旷工起始日期与天数、催告返岗期限、需提交的说明材料、逾期不返岗将按制度认定为旷工并可能解除劳动合同的后果、送达方式。',
    fields:[{key:'company',label:'公司名称'},{key:'employee',label:'员工姓名'},{key:'absent_from',label:'旷工起始日期'},
      {key:'deadline',label:'限期返岗日期'},{key:'note',label:'补充说明'}] },
  transfer_notice: { title:'调岗通知书', query:'变更劳动合同 协商一致 调整工作岗位',
    guide:'调岗原则上需协商一致（《劳动合同法》第三十五条）。写明：原岗位、新岗位、调整理由（合理性依据）、生效日期、薪酬是否变化、异议反馈渠道与期限。',
    fields:[{key:'company',label:'公司名称'},{key:'employee',label:'员工姓名'},{key:'old_position',label:'原岗位'},
      {key:'new_position',label:'新岗位'},{key:'reason',label:'调岗理由'},{key:'effective',label:'生效日期'},{key:'salary',label:'薪酬变化说明'}] },
  probation_fail: { title:'试用期不符合录用条件通知书', query:'试用期 不符合录用条件 解除劳动合同',
    guide:'务必写明：录用条件是什么（已书面告知）、考核过程与结果、不符合录用条件的具体事实、解除日期与结算交接。解除必须在试用期届满前作出并送达。',
    fields:[{key:'company',label:'公司名称'},{key:'employee',label:'员工姓名'},{key:'position',label:'岗位'},
      {key:'hire_date',label:'入职日期'},{key:'probation_end',label:'试用期截止日期'},{key:'fact',label:'考核情况与不符合录用条件的事实'}] },
};

const DRAFT_SYS = `你是企业 HR 劳动法文书起草助手。根据文书类型、事实要素和提供的【条文】起草规范文书。严格遵守：
1. 只输出 JSON：{"document": "文书全文", "citations": ["《法规名》第X条", ...]}
2. 文书格式：第一行为标题；正文条理分明（需要时用"一、二、三"分条）；结尾落款留公司名称与日期；
3. 事实要素缺失处用【待填写：说明】占位，禁止编造任何事实、日期、金额或证据；
4. 文中如引用法律条文，必须出自提供的【条文】，写法与《法规名》第X条完全一致；citations 列出全部引用；
5. 语言正式克制，不使用威胁性、侮辱性表述；
6. 若该类文书有法律风险前提（如单方解除需事由充分），在文末以"操作提示："另起一段列出 2–3 条要点。`;

export async function onRequestPost({ request, env }) {
  try {
    const body = await request.json().catch(() => ({}));
    const dt = DOC_TYPES[body.type];
    if (!dt) return json({ error: `未知文书类型：${body.type}` }, 400);
    if (!env.DEEPSEEK_API_KEY) return json({ error: '未配置 DeepSeek API，文书起草不可用' }, 503);
    const fields = body.fields || {};
    const note = ['note','basis','fact','reason'].map(k => String(fields[k]||'')).join(' ');
    const ctx = await retrieve(env.DB, dt.query + ' ' + note, body.region || null, 5);
    const ctxBlock = ctx.map((c,i) =>
      `【条文 ${i+1}】《${c.source}》${c.article}${c.clause?'第'+c.clause+'款':''}\n${c.text}`
    ).join('\n\n') || '（无）';
    const factLines = dt.fields.map(f => fields[f.key] ? `- ${f.label}：${fields[f.key]}` : '').filter(Boolean).join('\n') || '（未提供，全部用占位符）';
    const out = await chatJson(env, [
      { role:'system', content:DRAFT_SYS },
      { role:'user', content:`文书类型：${dt.title}\n起草要点：${dt.guide}\n\n事实要素：\n${factLines}\n\n可用条文：\n${ctxBlock}` }
    ], { maxTokens:1800, temperature:0.3, timeout:90 });
    const doc = String(out?.document || '').trim();
    if (!doc) return json({ error: '生成失败：模型未返回文书内容，请重试' }, 502);
    // Citation validation: only keep refs that are in the retrieved ctx set
    const provided = new Set(ctx.map(c => `${c.source}|${c.article}`));
    const validRefs = (out.citations || []).filter(ref => {
      const m = String(ref).match(/《(.+?)》(第.+?条)(第.+?款)?/);
      return m && provided.has(`${m[1]}|${m[2]}`);
    }).map(ref => {
      const m = String(ref).match(/《(.+?)》(第.+?条)(第.+?款)?/);
      return `《${m[1]}》${m[2]}${m[3]||''}`;
    });
    const [resolved] = await resolveCitations(env.DB, validRefs);
    // Check for unchecked inline citations in doc body
    const unchecked = [...new Set([...doc.matchAll(/《(.+?)》(第[一二三四五六七八九十百零\d]+条)/g)]
      .map(m => `${m[1]}|${m[2]}`).filter(k => !provided.has(k)))]
      .map(k => `《${k.split('|')[0]}》${k.split('|')[1]}`);
    const warnings = [
      '本文书为 AI 生成初稿，必须经律师或法务审核后方可对外使用',
      '日期、金额、姓名等事实信息请逐项人工核对；【待填写】占位须补全后再用'
    ];
    if (unchecked.length) warnings.push('文中下列条文引用未能在知识库内核验，请人工确认：' + unchecked.join('、'));
    return json({ title:dt.title, document:doc, citations:resolved, warnings, llm_used:true });
  } catch (e) {
    return json({ error: e.message || String(e) }, 500);
  }
}

export async function onRequestOptions() {
  return new Response(null, { status:204, headers: { 'Access-Control-Allow-Origin':'*',
    'Access-Control-Allow-Methods':'POST,OPTIONS', 'Access-Control-Allow-Headers':'Content-Type' } });
}
