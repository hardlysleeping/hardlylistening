const PAGE = "<!doctype html><html lang=\"en\"><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><meta name=\"robots\" content=\"noindex,nofollow\"><meta http-equiv=\"refresh\" content=\"30\"><title>HardlyListening \u00b7 Temporarily unavailable</title><style>body{margin:0;background:#101014;color:#eeeef3;font:16px/1.6 system-ui,sans-serif;min-height:100svh;display:grid;place-items:center}main{width:min(480px,calc(100% - 48px));padding:32px 0}h1{font-size:28px;line-height:1.2}.status{color:#ff808b;font-weight:650}.dot{display:inline-block;width:10px;height:10px;background:#ff6677;border-radius:50%;margin-right:8px}p{color:#b6b6c4}a{color:#70e1b5}small{color:#9595a5}</style><main><div>HardlyListening</div><h1>Music is temporarily unavailable</h1><div class=\"status\"><span class=\"dot\"></span>PC offline or unreachable</div><p>The hosting PC is offline or cannot be reached right now. Music and Party mode will return when it reconnects.</p><p><a href=\"\">Try again</a></p><small>This page automatically retries every 30 seconds.</small></main></html>";
export default {
  async fetch(request) {
    try {
      const response = await fetch(request);
      if (![502, 503, 504, 520, 521, 522, 523, 524, 525, 526, 527, 529, 530].includes(response.status)) return response;
      // Preserve application failures; replace only gateway/tunnel failures.
      // The app's own 503s should not be mistaken for a powered-off PC.
      if (response.status === 503 && response.headers.get('content-type')?.includes('application/json')) return response;
      return offline(request);
    } catch { return offline(request); }
  }
};
export function offline(request) {
  const headers = {'cache-control':'no-store', 'retry-after':'30',
    'x-robots-tag':'noindex, nofollow, noarchive', 'x-hardlylistening-fallback':'edge',
    'x-content-type-options':'nosniff','referrer-policy':'no-referrer'};
  const document = request.method === 'GET' && request.headers.get('accept')?.includes('text/html');
  headers['content-type'] = document ? 'text/html; charset=utf-8' : 'application/json; charset=utf-8';
  return new Response(request.method === 'HEAD' ? null : document ? PAGE : JSON.stringify({error:'PC offline or unreachable', unavailable:true}), {status:503, headers});
}
