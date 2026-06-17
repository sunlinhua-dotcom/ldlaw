import { answer } from '../_pipeline.js';

function json(obj, status = 200) {
  return new Response(JSON.stringify(obj), {
    status, headers: { 'Content-Type': 'application/json; charset=utf-8', 'Access-Control-Allow-Origin': '*' }
  });
}

export async function onRequestPost({ request, env }) {
  try {
    const body = await request.json().catch(() => ({}));
    const q = (body.question || '').trim();
    if (!q) return json({ error: 'question 不能为空' }, 400);
    const res = await answer(env.DB, env, q, body.region || null, body.session_id || null);
    return json(res);
  } catch (e) {
    return json({ error: String(e.message || e) }, 500);
  }
}

export async function onRequestOptions() {
  return new Response(null, { status:204, headers: { 'Access-Control-Allow-Origin':'*',
    'Access-Control-Allow-Methods':'POST,OPTIONS', 'Access-Control-Allow-Headers':'Content-Type' } });
}
