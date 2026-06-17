import { severance, unlawfulDamages, annualLeavePayout,
  statutoryAnnualDays, exitProratedUnusedDays, dateDiffDays } from '../_calc.js';
import { fetchParam, resolveCitations } from '../_pipeline.js';

function json(obj, status = 200) {
  return new Response(JSON.stringify(obj), {
    status, headers: { 'Content-Type': 'application/json; charset=utf-8', 'Access-Control-Allow-Origin': '*' }
  });
}

export async function onRequestPost({ request, env }) {
  try {
    const body = await request.json().catch(() => ({}));
    const db = env.DB;
    const ctype = body.type;

    if (ctype === 'severance' || ctype === 'unlawful') {
      const hire = body.hire_date;
      const term = body.term_date || new Date().toISOString().slice(0, 10);
      const wage = parseFloat(body.monthly_wage);
      const p = await fetchParam(db, body.region || '', 'social_avg_wage_monthly');
      const social = p ? p.value.amount : null;
      let socialNote = '';
      const extraWarn = [];
      if (p) {
        if (!p.verified) socialNote = `（⚠ 社平为近似值待核验，口径：${p.region_used} ${p.period}）`;
        if (p.fallback) extraWarn.push(`未配置市级社平，封顶按 ${p.region_used} 省级口径，法定口径为设区市级，结果可能偏差`);
      }
      const calc = ctype === 'unlawful'
        ? unlawfulDamages(hire, term, wage, social)
        : severance(hire, term, wage, social, socialNote);
      if (hire < '2008-01-01') extraWarn.push('入职早于 2008-01-01，依法需分段计算（本结果未分段），请转律师核算后再使用');
      const [cites] = await resolveCitations(db, calc.citations);
      return json({ amount:calc.amount, steps:calc.steps, citations:cites, warnings:[...calc.warnings, ...extraWarn] });
    }

    if (ctype === 'annual') {
      const wage = parseFloat(body.monthly_wage);
      const years = parseFloat(body.cumulative_years);
      const taken = parseFloat(body.taken_days || 0);
      const term = body.term_date || new Date().toISOString().slice(0, 10);
      const hire = body.hire_date || null;
      const annual = statutoryAnnualDays(years);
      const yearStart = term.slice(0,4) + '-01-01';
      let passed, baseNote;
      if (hire && hire.slice(0,4) === term.slice(0,4) && hire > yearStart) {
        passed = dateDiffDays(hire, term) + 1; baseNote = `自当年入职日 ${hire} 起算`;
      } else {
        passed = dateDiffDays(yearStart, term) + 1; baseNote = '按全年在职折算';
      }
      const unused = exitProratedUnusedDays(passed, annual, taken);
      const calc = annualLeavePayout(wage, unused);
      calc.steps.unshift(`累计工龄 ${years} 年 → 全年应休 ${annual} 天；${baseNote}，截至 ${term} 已过 ${passed} 天，已休 ${taken} 天 → 应付未休 ${unused} 天`);
      const [cites] = await resolveCitations(db, calc.citations);
      return json({ amount:calc.amount, steps:calc.steps, citations:cites, warnings:calc.warnings, unused_days:unused });
    }

    return json({ error: `未知计算器类型：${ctype}` }, 400);
  } catch (e) {
    return json({ error: e.message || String(e) }, e.message?.includes('入职') ? 400 : 500);
  }
}

export async function onRequestOptions() {
  return new Response(null, { status:204, headers: { 'Access-Control-Allow-Origin':'*',
    'Access-Control-Allow-Methods':'POST,OPTIONS', 'Access-Control-Allow-Headers':'Content-Type' } });
}
