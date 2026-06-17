function json(obj) {
  return new Response(JSON.stringify(obj), {
    headers: { 'Content-Type': 'application/json; charset=utf-8', 'Access-Control-Allow-Origin': '*' }
  });
}

export async function onRequestGet({ request, env }) {
  const url = new URL(request.url);
  const tag = url.searchParams.get('tag');
  const region = url.searchParams.get('region');
  const db = env.DB;

  let sql = `SELECT DISTINCT c.id, c.case_no, c.court, c.gist, c.facts_summary,
                    c.result, c.license_note, c.verified, r.name AS region, c.cause, c.trial_level
             FROM case_record c JOIN region r ON r.id=c.region_id`;
  const args = [];
  const wheres = [];
  if (tag) {
    sql += ' JOIN case_tag ct ON ct.case_id=c.id JOIN dispute_tag dt ON dt.id=ct.tag_id';
    wheres.push('dt.name = ?'); args.push(tag);
  }
  if (region) { wheres.push("r.name IN (?, '全国')"); args.push(region); }
  if (wheres.length) sql += ' WHERE ' + wheres.join(' AND ');
  sql += ' ORDER BY c.id';

  const { results } = await db.prepare(sql).bind(...args).all();
  const out = await Promise.all(results.map(async row => {
    const [title,,note] = (row.license_note || '').split('｜');
    const tags = (await db.prepare(
      `SELECT dt.name FROM case_tag ct JOIN dispute_tag dt ON dt.id=ct.tag_id WHERE ct.case_id=?`
    ).bind(row.id).all()).results.map(r => r.name);
    const citations = (await db.prepare(
      `SELECT ls.title, la.article_no, la.clause_no, la.text, la.verified
       FROM case_citation cc JOIN legal_article la ON la.id=cc.article_id
       JOIN legal_source ls ON ls.id=la.source_id WHERE cc.case_id=?`
    ).bind(row.id).all()).results.map(r => ({
      source:r.title, article:r.article_no, clause:r.clause_no, text:r.text, verified:!!r.verified
    }));
    return { ...row, license_note:undefined, title:title||'（未命名案例）', source_note:note||'', tags, citations };
  }));
  return json(out);
}

export async function onRequestOptions() {
  return new Response(null, { status:204, headers: { 'Access-Control-Allow-Origin':'*',
    'Access-Control-Allow-Methods':'GET,OPTIONS', 'Access-Control-Allow-Headers':'Content-Type' } });
}
