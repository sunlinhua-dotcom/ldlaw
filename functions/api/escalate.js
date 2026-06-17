function json(obj, status = 200) {
  return new Response(JSON.stringify(obj), {
    status, headers: { 'Content-Type': 'application/json; charset=utf-8', 'Access-Control-Allow-Origin': '*' }
  });
}

export async function onRequestPost({ request, env }) {
  const body = await request.json().catch(() => ({}));
  const brief = (body.question || '').slice(0, 200);
  const now = new Date().toISOString().replace(/\.\d{3}Z$/, 'Z');
  const res = await env.DB.prepare(
    `INSERT INTO referral(question_brief, consent_at, status, created_at) VALUES (?,?,?,?)`
  ).bind(`[${body.region || '-'}] ${brief}`, now, 'pending', now).run();
  return json({ referral_id: res.meta.last_row_id, status: 'pending',
    message: '已生成咨询摘要并创建转介工单（演示），待匹配律师接单' });
}

export async function onRequestOptions() {
  return new Response(null, { status:204, headers: { 'Access-Control-Allow-Origin':'*',
    'Access-Control-Allow-Methods':'POST,OPTIONS', 'Access-Control-Allow-Headers':'Content-Type' } });
}
