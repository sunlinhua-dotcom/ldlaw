function json(obj, status = 200) {
  return new Response(JSON.stringify(obj, null, 0), {
    status, headers: { 'Content-Type': 'application/json; charset=utf-8',
      'Access-Control-Allow-Origin': '*' }
  });
}

export async function onRequestGet({ env }) {
  const db = env.DB;
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
  const models = [];
  if (env.DEEPSEEK_API_KEY) models.push({ id: 'deepseek-chat', label: 'DeepSeek' });
  if (env.CLAUDE_API_KEY) models.push({ id: env.CLAUDE_MODEL || 'claude-opus-4-8', label: 'Claude' });
  return json({ sources, articles, articles_unverified: articlesUnverified,
    entries, params, regions, cases, templates: 0, local_sources: 0,
    built_at: metaRow?.value ?? '-',
    llm: models[0]?.id ?? null, models,
    qa_logged: qaLogged, referrals });
}

export async function onRequestOptions() {
  return new Response(null, { status: 204, headers: { 'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Methods': 'GET, POST, OPTIONS', 'Access-Control-Allow-Headers': 'Content-Type' } });
}
