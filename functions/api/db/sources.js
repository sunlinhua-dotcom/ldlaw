function json(obj) {
  return new Response(JSON.stringify(obj), {
    headers: { 'Content-Type': 'application/json; charset=utf-8', 'Access-Control-Allow-Origin': '*' }
  });
}

export async function onRequestGet({ env }) {
  const { results } = await env.DB.prepare(
    `SELECT ls.id, ls.title, ls.doc_no, ls.issuer, ls.level, r.name AS region,
            ls.effective_date, ls.status, ls.source_url,
            (SELECT count(*) FROM legal_article a WHERE a.source_id=ls.id) AS articles
     FROM legal_source ls JOIN region r ON r.id=ls.region_id ORDER BY ls.id`
  ).all();
  return json(results);
}

export async function onRequestOptions() {
  return new Response(null, { status:204, headers: { 'Access-Control-Allow-Origin':'*',
    'Access-Control-Allow-Methods':'GET,OPTIONS', 'Access-Control-Allow-Headers':'Content-Type' } });
}
