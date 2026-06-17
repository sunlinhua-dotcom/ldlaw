import { retrieve, resolveCitations, chatJson } from '../_pipeline.js';

function json(obj, status = 200) {
  return new Response(JSON.stringify(obj), {
    status, headers: { 'Content-Type': 'application/json; charset=utf-8', 'Access-Control-Allow-Origin': '*' }
  });
}

const GENERAL_RUBRIC = [
  '事实表述具体可验证（时间、地点、行为、数据），无情绪化或威胁性措辞',
  '全部日期、金额、姓名与事实要素一致，文中不得出现来源不明的数字',
  '占位符【待填写】必须全部补全后才能发出',
  '落款（公司全称）、日期、员工签收栏完整；一式两份，签收留存',
  '送达可留痕：优先直接送达本人签收；拒收时同住成年亲属签收或 EMS 邮寄（面单注明文件名称）并保留回执；穷尽上述方式才可公告送达',
];

const REVIEW_RULES = {
  mutual_termination: { query:'协商一致 解除 经济补偿 支付', points:['明确写明依据《劳动合同法》第三十六条协商一致解除，而非单方解除','补偿金额、支付时间、支付方式、个税代扣口径明确','工资结算至离职日；未休年假折算、报销、奖金是否了结写明','社保公积金缴至具体月份明确','包含「双方再无其他劳动争议」一次性了结条款'] },
  dismissal_notice: { query:'解除 通知工会 不得解除 提前三十日 经济补偿', points:['解除事由落到第三十九/四十条的具体某一项，事实与该项构成要件对应','依第四十条解除的：提前三十日书面通知或支付一个月代通知金，并附经济补偿','排查第四十二条禁止情形：医疗期内、三期女职工、工伤停工留薪期','解除理由已事先通知工会（第四十三条）；未建工会的向当地总工会履行','所依据的规章制度经民主程序制定并已公示告知（第四条），证据已固定'] },
  warning_letter: { query:'规章制度 劳动纪律 民主程序 公示', points:['违纪事实具体（时间地点行为证据），不用「屡次」「恶劣」等空泛定性','写明违反的制度名称与具体条款编号，该制度经民主程序制定并已公示','处分与违纪程度相当（过罚相当）','给员工申辩/申诉渠道与期限','预留员工签收栏；拒签时注明见证人并拍照留痕'] },
  return_to_work: { query:'旷工 严重违反规章制度 解除', points:['旷工起始日期、累计天数表述准确','返岗期限合理（一般不少于 3 个工作日）','要求限期提交未到岗的书面说明与证明材料','后果只能写「将依规章制度认定旷工并可能依第三十九条解除」，绝不能写「视为自动离职」——自动离职不是法定解除类型'] },
  transfer_notice: { query:'变更劳动合同 协商一致 工作岗位', points:['调岗原则需协商一致（第三十五条）；单方调岗必须写明经营必要性等合理性理由','新岗位薪酬是否变化必须明示；降薪调岗风险极高','给员工异议反馈渠道与期限'] },
  probation_fail: { query:'试用期 不符合录用条件 解除 试用期期限', points:['录用条件已在入职时书面明示并有签收证据','考核事实具体，与录用条件逐项对应','必须在试用期届满前作出并送达，超期即丧失该解除依据','试用期长短合法（第十九条）'] },
};

const REVIEW_SYS = `你是劳动法执业律师，对 HR 即将发出的文书做发出前合规审核。只依据提供的【条文】、【审核清单】与中国劳动法通行实务判断，输出 JSON：
{"verdict": "pass|revise|block", "summary": "30 字内总评",
 "findings": [{"severity": "blocker|risk|polish", "point": "问题标题", "detail": "问题说明（涉及条文时写《法规名》第X条）", "fix": "具体修改建议"}],
 "checklist": [{"item": "清单项原文", "ok": true, "note": "核对结论（20 字内）"}]}
判级标准：blocker = 不改不能发出；risk = 有败诉或争议风险，应当修改；polish = 表述优化建议。
规则：checklist 必须逐项覆盖；引用条文必须来自提供的【条文】；没有问题就判 pass，不要虚构问题。`;

const DOC_QUERIES = {
  mutual_termination:'协商一致解除劳动合同 经济补偿 工作交接',
  dismissal_notice:'用人单位 解除劳动合同 通知 工会 提前三十日',
  warning_letter:'严重违反 规章制度 劳动纪律 处分',
  return_to_work:'旷工 严重违反规章制度 解除劳动合同',
  transfer_notice:'变更劳动合同 协商一致 调整工作岗位',
  probation_fail:'试用期 不符合录用条件 解除劳动合同',
};

const DOC_FIELD_LABELS = {
  mutual_termination:{company:'公司名称',employee:'员工姓名',position:'岗位',hire_date:'入职日期',term_date:'协商离职日期',compensation:'经济补偿金额（元）',note:'其他约定'},
  dismissal_notice:{company:'公司名称',employee:'员工姓名',position:'岗位',basis:'解除事由与依据',term_date:'解除日期',note:'交接与结算安排'},
  warning_letter:{company:'公司名称',employee:'员工姓名',department:'部门',fact:'违纪事实',rule:'违反的制度条款',demand:'整改要求'},
  return_to_work:{company:'公司名称',employee:'员工姓名',absent_from:'旷工起始日期',deadline:'限期返岗日期',note:'补充说明'},
  transfer_notice:{company:'公司名称',employee:'员工姓名',old_position:'原岗位',new_position:'新岗位',reason:'调岗理由',effective:'生效日期',salary:'薪酬变化说明'},
  probation_fail:{company:'公司名称',employee:'员工姓名',position:'岗位',hire_date:'入职日期',probation_end:'试用期截止日期',fact:'考核情况与不符合录用条件的事实'},
};

export async function onRequestPost({ request, env }) {
  try {
    const body = await request.json().catch(() => ({}));
    const docType = body.type || '';
    const document = String(body.document || '').trim();
    const fields = body.fields || {};
    if (!DOC_QUERIES[docType]) return json({ error: `未知文书类型：${docType}` }, 400);
    if (!document) return json({ error: 'document 不能为空' }, 400);
    if (!env.DEEPSEEK_API_KEY) return json({ error: '未配置 DeepSeek API，AI 审核不可用' }, 503);

    // Hard rule checks
    const ruleFindings = [];
    const placeholders = [...document.matchAll(/【待填写[：:][^】]*】/g)].map(m => m[0]);
    if (placeholders.length) ruleFindings.push({ severity:'blocker', point:'存在未补全的占位符',
      detail:'文中仍有：' + [...new Set(placeholders)].join('、'), fix:'逐项补全真实信息后才能发出', _rule:'placeholder' });
    if (['return_to_work','dismissal_notice','warning_letter'].includes(docType) &&
        /视为自动离职|自动离职处理|视为离职|自行离职处理/.test(document)) {
      ruleFindings.push({ severity:'blocker', point:'「视为自动离职」无法律依据',
        detail:'解除劳动合同只能由用人单位依法定情形作出并送达，「自动离职」不是法定解除类型，按此处理大概率被认定违法解除（2N）',
        fix:'删除该表述，改为「公司将依据规章制度并按《中华人民共和国劳动合同法》第三十九条解除劳动合同」', _rule:'auto_quit' });
    }
    if (/医疗期|病假|患病|生病|住院/.test(JSON.stringify(fields) + document) &&
        ['dismissal_notice','probation_fail'].includes(docType)) {
      ruleFindings.push({ severity:'blocker', point:'涉及患病/医疗期员工的单方解除',
        detail:'事实要素或文书中出现患病/病假语境：医疗期内禁止依第四十/四十一条解除（第四十二条），需先核实医疗期是否届满',
        fix:'核实医疗期；未满则停发本通知，改走协商或等期满后依法定程序处理', _rule:'medical' });
    }

    const rules = REVIEW_RULES[docType] || { query:DOC_QUERIES[docType], points:[] };
    const rubric = [...GENERAL_RUBRIC, ...rules.points];
    const ctx = await retrieve(env.DB, rules.query + ' ' + DOC_QUERIES[docType], body.region || null, 6);
    const ctxBlock = ctx.map((c,i) => `【条文 ${i+1}】《${c.source}》${c.article}${c.clause?'第'+c.clause+'款':''}\n${c.text}`).join('\n\n') || '（无）';
    const labels = DOC_FIELD_LABELS[docType] || {};
    const factLines = Object.entries(fields).filter(([,v]) => String(v||'').trim()).map(([k,v]) => `- ${labels[k]||k}：${v}`).join('\n') || '（未提供）';
    const rubricBlock = rubric.map((r,i) => `${i+1}. ${r}`).join('\n');
    const out = await chatJson(env, [
      { role:'system', content:REVIEW_SYS },
      { role:'user', content:`文书类型：${docType}\n\n事实要素：\n${factLines}\n\n审核清单：\n${rubricBlock}\n\n可用条文：\n${ctxBlock}\n\n待审文书全文：\n${document}` }
    ], { maxTokens:2200, temperature:0.2, timeout:90 });

    const llmFindings = (out?.findings || []).filter(f => f?.point).map(f => {
      if (!['blocker','risk','polish'].includes(f.severity)) f.severity = 'risk';
      return f;
    });
    const checklist = (out?.checklist || []).filter(c => c?.item);
    let verdict = ['pass','revise','block'].includes(out?.verdict) ? out.verdict : 'revise';
    const findings = [...ruleFindings, ...llmFindings];
    if (findings.some(f => ['auto_quit','medical'].includes(f._rule))) verdict = 'block';
    else if (findings.some(f => f.severity === 'blocker') && verdict === 'pass') verdict = 'revise';
    findings.forEach(f => delete f._rule);

    const refs = [...new Set(findings.flatMap(f => [...(String(f.detail||'')+String(f.fix||'')).matchAll(/《(.+?)》(第[一二三四五六七八九十百零\d]+条)/g)].map(m => `《${m[1]}》${m[2]}`)))];
    const [resolved] = await resolveCitations(env.DB, refs);
    return json({ verdict, summary:String(out?.summary||'').trim(), findings, checklist,
      citations:resolved, llm_used:true, disclaimer:'AI 审核为算法辅助意见，不构成法律意见；重大事项发出前仍建议执业律师人工复核' });
  } catch (e) {
    return json({ error: e.message || String(e) }, 500);
  }
}

export async function onRequestOptions() {
  return new Response(null, { status:204, headers: { 'Access-Control-Allow-Origin':'*',
    'Access-Control-Allow-Methods':'POST,OPTIONS', 'Access-Control-Allow-Headers':'Content-Type' } });
}
