import { entryBySlug } from '../../_pipeline.js';

function json(obj, status = 200) {
  return new Response(JSON.stringify(obj), {
    status, headers: { 'Content-Type': 'application/json; charset=utf-8', 'Access-Control-Allow-Origin': '*' }
  });
}

export async function onRequestGet({ env, params }) {
  const entry = await entryBySlug(env.DB, params.slug);
  return entry ? json(entry) : json({ error: 'not found' }, 404);
}

export async function onRequestOptions() {
  return new Response(null, { status:204, headers: { 'Access-Control-Allow-Origin':'*',
    'Access-Control-Allow-Methods':'GET,OPTIONS', 'Access-Control-Allow-Headers':'Content-Type' } });
}
