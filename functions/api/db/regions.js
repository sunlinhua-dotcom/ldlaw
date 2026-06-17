function json(obj) {
  return new Response(JSON.stringify(obj), {
    headers: { 'Content-Type': 'application/json; charset=utf-8', 'Access-Control-Allow-Origin': '*' }
  });
}

export async function onRequestGet({ env }) {
  const { results } = await env.DB.prepare(
    `SELECT r.id, r.code, r.name, r.level, p.name AS parent
     FROM region r LEFT JOIN region p ON p.id=r.parent_id ORDER BY r.id`
  ).all();
  return json(results);
}

export async function onRequestOptions() {
  return new Response(null, { status:204, headers: { 'Access-Control-Allow-Origin':'*',
    'Access-Control-Allow-Methods':'GET,OPTIONS', 'Access-Control-Allow-Headers':'Content-Type' } });
}
