function json(obj) {
  return new Response(JSON.stringify(obj), {
    headers: { 'Content-Type': 'application/json; charset=utf-8', 'Access-Control-Allow-Origin': '*' }
  });
}

export async function onRequestGet({ env }) {
  const { results } = await env.DB.prepare(
    `SELECT rp.id, r.name AS region, rp.param_key, rp.value, rp.period, rp.verified
     FROM region_param rp JOIN region r ON r.id=rp.region_id ORDER BY rp.id`
  ).all();
  return json(results.map(r => ({ ...r, value: JSON.parse(r.value) })));
}

export async function onRequestOptions() {
  return new Response(null, { status:204, headers: { 'Access-Control-Allow-Origin':'*',
    'Access-Control-Allow-Methods':'GET,OPTIONS', 'Access-Control-Allow-Headers':'Content-Type' } });
}
