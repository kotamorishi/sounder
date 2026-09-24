/* sounder のサービスワーカー（PWA 用）。HTTPS（Tailscale など）で開いたときだけ登録される。
   画面のファイルは毎回 Mac から取り（ネットワーク優先）、つながらないときだけ前回の画面を出す。
   予定や設定などのデータ（/api/）は取っておかない（古い予定で鳴ったように見えるのを防ぐ）。 */
const CACHE = 'sounder-shell-v1';

self.addEventListener('install', (e) => { self.skipWaiting(); });

self.addEventListener('activate', (e) => {
  e.waitUntil((async () => {
    for (const k of await caches.keys()) if (k !== CACHE) await caches.delete(k);
    await self.clients.claim();
  })());
});

self.addEventListener('fetch', (e) => {
  const req = e.request;
  const url = new URL(req.url);
  if (req.method !== 'GET' || url.origin !== location.origin || url.pathname.startsWith('/api/')) return;
  e.respondWith((async () => {
    try {
      const res = await fetch(req);
      if (res.ok && res.type === 'basic') {
        const copy = res.clone();
        // 合言葉つきの URL（?t=…）はキャッシュの鍵にしない
        const key = new URL(url);
        key.searchParams.delete('t');
        caches.open(CACHE).then((c) => c.put(key.href, copy)).catch(() => {});
      }
      return res;
    } catch (err) {
      const key = new URL(url);
      key.searchParams.delete('t');
      const hit = await caches.match(key.href) || (req.mode === 'navigate' && await caches.match(new URL('/', location.origin).href));
      if (hit) return hit;
      throw err;
    }
  })());
});
