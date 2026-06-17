function json(obj, status = 200) {
  return new Response(JSON.stringify(obj, null, 0), {
    status, headers: { 'Content-Type': 'application/json; charset=utf-8',
      'Access-Control-Allow-Origin': '*' }
  });
}

export async function onRequestGet({ env }) {
  const db = env.DB;
  const g = async sql => (await db.prepare(sql).first())?.[Object.keys(await db.prepare(sql).first())[0]] ?? 0;
  const count = async sql => { const r = await db.prepare(sql).first(); return r ? Object.values(r)[0] : 0; };
  const [sources, articles, articlesUnverified, entries, params, regions, cases] = await Promise.all([
    count("SELECT count(*) AS n FROM legal_source"),
    count("SELECT count(*) AS n FROM legal_article"),
    count("SELECT count(*) AS n FROM legal_article WHERE verified=0"),
    count("SELECT count(*) AS n FROM entry"),
    count("SELECT count(*) AS n FROM region_param"),
    count("SELECT count(*) AS n FROM region WHERE level != 'country'"),
    count("SELECT count(*) AS n FROM case_record"),
  ]);
  const metaRow = await db.prepare("SELECT value FROM meta WHERE key='built_at'").first();
  const qaLogged = await count("SELECT count(*) AS n FROM qa_message");
  const referrals = await count("SELECT count(*) AS n FROM referral");
  return json({ sources, articles, articles_unverified: articlesUnverified,
    entries, params, regions, cases, templates: 0, local_sources: 0,
    built_at: metaRow?.value ?? '-',
    llm: env.DEEPSEEK_API_KEY ? (env.DEEPSEEK_MODEL || 'deepseek-chat') : null,
    qa_logged: qaLogged, referrals });
}

export async function onRequestOptions() {
  return new Response(null, { status: 204, headers: { 'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Methods': 'GET, POST, OPTIONS', 'Access-Control-Allow-Headers': 'Content-Type' } });
}
