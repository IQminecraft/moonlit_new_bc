/* ============================================================
 *  moonlit.wiki Service Worker
 *  - /static/ 配下（アイコン・フォント等の重いアセット）をキャッシュ
 *    → 2回目以降の表示を高速化
 *  - ページ本体・API は常にネットワーク（古いカードデータを見せない）
 *  - キャッシュ名のバージョンを上げると古いキャッシュを自動削除
 * ============================================================ */
const CACHE_VERSION = 'v1';
const CACHE_NAME = `moonlit-static-${CACHE_VERSION}`;

self.addEventListener('install', (event) => {
    // install は即完了（先読みはしない。使われたアセットから徐々に貯める）
    self.skipWaiting();
});

self.addEventListener('activate', (event) => {
    event.waitUntil((async () => {
        const names = await caches.keys();
        await Promise.all(names.filter(n => n !== CACHE_NAME).map(n => caches.delete(n)));
        await self.clients.claim();
    })());
});

self.addEventListener('fetch', (event) => {
    const req = event.request;
    if (req.method !== 'GET') return;
    const url = new URL(req.url);
    // http(s) のみ対象（blob: や extension スキームは素通し）
    if (!/^https?:$/.test(url.protocol)) return;
    if (url.origin !== self.location.origin) return;

    // 静的アセットのみキャッシュ対象（HTML・API は素通し）
    if (!url.pathname.startsWith('/static/')) return;
    // 履歴・JSON データ系は鮮度優先で素通し
    if (url.pathname.startsWith('/static/cache/')) return;

    event.respondWith((async () => {
        const cache = await caches.open(CACHE_NAME);
        const cached = await cache.match(req);
        // キャッシュにあれば即返し、裏で最新を取得して更新（stale-while-revalidate）
        const fetchAndUpdate = (async () => {
            try {
                const res = await fetch(req);
                if (res && res.ok) await cache.put(req, res.clone());
            } catch (e) { /* オフライン時はキャッシュのみ */ }
        })();
        if (cached) {
            event.waitUntil(fetchAndUpdate);
            return cached;
        }
        try {
            const res = await fetch(req);
            if (res && res.ok) await cache.put(req, res.clone());
            return res;
        } catch (e) {
            return new Response('', { status: 504 });
        }
    })());
});
