function json(obj) {
  return new Response(JSON.stringify(obj), {
    headers: { 'Content-Type': 'application/json; charset=utf-8', 'Access-Control-Allow-Origin': '*' }
  });
}

export async function onRequestGet({ env }) {
  const { results } = await env.DB.prepare(
    `SELECT e.slug, e.title, e.status, e.basis_date, t.name AS topic
     FROM entry e LEFT JOIN topic t ON t.id=e.topic_id
     WHERE e.status != 'archived' ORDER BY e.id`
  ).all();
  return json(results);
}

export async function onRequestOptions() {
  return new Response(null, { status:204, headers: { 'Access-Control-Allow-Origin':'*',
    'Access-Control-Allow-Methods':'GET,OPTIONS', 'Access-Control-Allow-Headers':'Content-Type' } });
}
