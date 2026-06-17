// 语音识别代理（豆包大模型 flash 一次性识别）。凭据全在 CF Secret，浏览器只传音频。
// 浏览器录 PCM→编 WAV(16k 单声道)→base64 POST 到这里；本端点带服务端鉴权头转发豆包，回 result.text。
// 合规：仅服务端持 App-Key/Access-Key（CLAUDE.md #7）；问答管线不引此端点。
const FLASH = 'https://openspeech.bytedance.com/api/v3/auc/bigmodel/recognize/flash';

function json(obj, status = 200) {
  return new Response(JSON.stringify(obj), {
    status, headers: { 'Content-Type': 'application/json; charset=utf-8', 'Access-Control-Allow-Origin': '*' }
  });
}

export async function onRequestPost({ request, env }) {
  try {
    if (!env.DOUBAO_ASR_APP_ID || !env.DOUBAO_ASR_ACCESS_TOKEN)
      return json({ error: '未配置语音识别凭据（DOUBAO_ASR_*）' }, 503);
    const body = await request.json().catch(() => ({}));
    const audioB64 = (body.audio || '').replace(/^data:[^,]*,/, '');  // 容错 dataURL 前缀
    if (!audioB64) return json({ error: 'audio 不能为空' }, 400);

    const resp = await fetch(FLASH, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Api-App-Key': env.DOUBAO_ASR_APP_ID,
        'X-Api-Access-Key': env.DOUBAO_ASR_ACCESS_TOKEN,
        'X-Api-Resource-Id': 'volc.bigasr.auc_turbo',
        'X-Api-Request-Id': crypto.randomUUID(),
        'X-Api-Sequence': '-1'
      },
      body: JSON.stringify({
        user: { uid: 'ldlawq-web' },
        audio: { data: audioB64 },
        request: { model_name: 'bigmodel' }
      }),
      signal: AbortSignal.timeout(30000)
    });
    const statusCode = resp.headers.get('X-Api-Status-Code');
    const data = await resp.json().catch(() => ({}));
    const text = (data && data.result && data.result.text) ? data.result.text : '';
    // 鉴权/服务级失败（4xxxxxxx 等）才报错；无人声/空结果回空文本，前端提示"没听清"
    const authFail = statusCode && /^4/.test(statusCode);
    if (authFail) return json({ error: `语音服务鉴权或请求失败（${statusCode}）`, detail: (data && data.message) || '' }, 502);
    return json({ text, code: statusCode || '' });
  } catch (e) {
    return json({ error: String(e.message || e) }, 500);
  }
}

export async function onRequestOptions() {
  return new Response(null, { status: 204, headers: { 'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Methods': 'POST,OPTIONS', 'Access-Control-Allow-Headers': 'Content-Type' } });
}
