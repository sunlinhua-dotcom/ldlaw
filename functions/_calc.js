// Port of src/calculators.py — deterministic labor law calculators
const MONTH_DAYS = [31,28,31,30,31,30,31,31,30,31,30,31];
export const PAID_DAYS_PER_MONTH = 21.75;

function isLeap(y) { return y % 4 === 0 && (y % 100 !== 0 || y % 400 === 0); }

// Operate on ISO date strings "YYYY-MM-DD" for timezone safety
function addMonths(dateStr, months) {
  let [y, m, d] = dateStr.split('-').map(Number);
  m += months;
  y += Math.floor((m - 1) / 12);
  m = ((m - 1) % 12 + 12) % 12 + 1;
  const dim = (m === 2 && isLeap(y)) ? 29 : MONTH_DAYS[m - 1];
  return `${y}-${String(m).padStart(2,'0')}-${String(Math.min(d,dim)).padStart(2,'0')}`;
}

export function dateDiffDays(a, b) {
  return Math.round((new Date(b+'T00:00:00Z') - new Date(a+'T00:00:00Z')) / 86400000);
}

function nf(n) { return new Intl.NumberFormat('zh-CN').format(n); }

export function serviceN(hireStr, termStr) {
  if (termStr <= hireStr) throw new Error('离职日期必须晚于入职日期');
  let years = 0;
  while (addMonths(hireStr, (years+1)*12) <= termStr) years++;
  const remStart = addMonths(hireStr, years*12);
  if (remStart === termStr) return years;
  if (addMonths(remStart, 6) <= termStr) return years + 1.0;
  return years + 0.5;
}

export function severance(hireStr, termStr, avgWage, socialAvg=null, socialNote='') {
  const n = serviceN(hireStr, termStr);
  let base = avgWage, effN = n;
  const steps = [`工作年限 ${hireStr} 至 ${termStr}，折算 N = ${n} 个月`];
  const warnings = [
    '月工资口径 = 解除/终止前 12 个月平均应发工资（含奖金、津贴）',
    '2008-01-01 前入职的分段计算口径未在演示版实现，遇到请转律师核算'
  ];
  if (socialAvg !== null) {
    const cap = 3 * socialAvg;
    if (base > cap) {
      steps.push(`月工资 ${nf(base)} 元 > 3 × 社平 ${nf(socialAvg)} = ${nf(cap)} 元，基数封顶为 ${nf(cap)} 元${socialNote}`);
      base = cap;
      if (effN > 12) { steps.push(`N 由 ${effN} 封顶至 12`); effN = 12; }
    } else {
      steps.push(`月工资 ${nf(base)} 元 ≤ 3 × 社平，不触发封顶${socialNote}`);
    }
  } else {
    warnings.push('未提供当地社平工资，未校验 3 倍封顶规则');
  }
  const amount = Math.round(base * effN * 100) / 100;
  steps.push(`经济补偿 = ${nf(base)} × ${effN} = ${nf(amount)} 元`);
  return { key:'severance', amount, steps, warnings,
    citations:['《中华人民共和国劳动合同法》第四十六条','《中华人民共和国劳动合同法》第四十七条'] };
}

export function unlawfulDamages(hireStr, termStr, avgWage, socialAvg=null) {
  const s = severance(hireStr, termStr, avgWage, socialAvg);
  const amount = Math.round(s.amount * 2 * 100) / 100;
  return { key:'unlawful_damages', amount,
    steps:[...s.steps, `违法解除赔偿金 = 经济补偿 ${nf(s.amount)} × 2 = ${nf(amount)} 元`],
    citations:[...s.citations,'《中华人民共和国劳动合同法》第八十七条','《中华人民共和国劳动合同法实施条例》第二十五条'],
    warnings:[...s.warnings,'支付赔偿金的，不再同时支付经济补偿（实施条例第二十五条）'] };
}

export function statutoryAnnualDays(years) {
  if (years < 1) return 0;
  if (years < 10) return 5;
  if (years < 20) return 10;
  return 15;
}

export function exitProratedUnusedDays(yearPassedDays, annualDays, takenDays) {
  return Math.max(0, Math.floor(yearPassedDays / 365 * annualDays - takenDays));
}

export function annualLeavePayout(monthlyWage, unusedDays) {
  const daily = monthlyWage / PAID_DAYS_PER_MONTH;
  const total = Math.round(daily * 3 * unusedDays * 100) / 100;
  const extra = Math.round(daily * 2 * unusedDays * 100) / 100;
  return { key:'annual_leave', amount:extra,
    steps:[
      `日工资 = ${nf(monthlyWage)} ÷ 21.75（月计薪天数）= ${daily.toFixed(2)} 元`,
      `未休 ${unusedDays} 天 × 日工资 × 300% = ${nf(total)} 元（法定报酬总额，其中含已随正常工资发放的 100%）`,
      `企业额外应补 = 日工资 × 200% × ${unusedDays} 天 = ${nf(extra)} 元`
    ],
    citations:['《职工带薪年休假条例》第三条','《职工带薪年休假条例》第五条第三款',
      '《企业职工带薪年休假实施办法》第十条','《企业职工带薪年休假实施办法》第十二条',
      '《关于职工全年月平均工作时间和工资折算问题的通知》第二条'],
    warnings:['「累计工龄」含此前其他单位年限，需员工提供证明材料',
      '若当年正常工资尚未结清（含未休天数对应工资），请按 300% 总额口径核算'] };
}
