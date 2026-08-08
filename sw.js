/* ─────────────────────────────────────────────────────────────
   APMT Poti — ამინდის კონსენსუს-პორტალი · Service Worker

   საპროექტო პრინციპი: ეს ოპერაციული ინსტრუმენტია. ქეშიდან ძველი
   მონაცემის ჩვენება მაშინ, როცა ქსელი ხელმისაწვდომია, დაუშვებელია.
   ამიტომ:
     · data.json  → NETWORK-FIRST (ქეში მხოლოდ ჩავარდნისას)
     · shell      → NETWORK-FIRST (ახალი commit მაშინვე აისახოს)
     · CDN აქტივები → STALE-WHILE-REVALIDATE (სწრაფი ჩატვირთვა)
     · რუკის ფილები → არ ქეშირდება (მოცულობა + დროზე მგრძნობიარეა)

   ვერსიის აწევა: შეცვალე VERSION — ძველი ქეშები ავტომატურად წაიშლება.
   ───────────────────────────────────────────────────────────── */

const VERSION     = 'v1';
const SHELL_CACHE = `poti-shell-${VERSION}`;
const DATA_CACHE  = `poti-data-${VERSION}`;
const CDN_CACHE   = `poti-cdn-${VERSION}`;

const SHELL_ASSETS = [
  './',
  './index.html',
  './manifest.json',
  './icon-192.png',
  './icon-512.png',
  './icon-maskable-512.png',
  './apple-touch-icon.png',
];

/* რუკის/რადარის ფილები — არასოდეს ქეშირდება */
const NEVER_CACHE = [
  'tilecache.rainviewer.com',
  'api.rainviewer.com',
  'basemaps.cartocdn.com',
  'tile.openstreetmap.org',
];

/* ─── ინსტალაცია ─── */
self.addEventListener('install', (e) => {
  e.waitUntil(
    caches.open(SHELL_CACHE)
      .then((c) => c.addAll(SHELL_ASSETS))
      .catch(() => {})          // ერთი აქტივის ჩავარდნა არ უნდა აფერხებდეს
      .then(() => self.skipWaiting())
  );
});

/* ─── გააქტიურება: ძველი ვერსიების დასუფთავება ─── */
self.addEventListener('activate', (e) => {
  const keep = [SHELL_CACHE, DATA_CACHE, CDN_CACHE];
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(
        keys.filter((k) => k.startsWith('poti-') && !keep.includes(k))
            .map((k) => caches.delete(k))
      ))
      .then(() => self.clients.claim())
  );
});

/* ─── დამხმარეები ─── */

/* ქეშის გასაღები search-პარამეტრების გარეშე (?t=... ამტვრევს დამთხვევას) */
function cacheKey(url) {
  const u = new URL(url);
  u.search = '';
  return u.href;
}

/* ქეშიდან მოსულ პასუხს ვნიშნავთ, რომ UI-მ „ოფლაინი" აჩვენოს */
function tagCached(resp) {
  const h = new Headers(resp.headers);
  h.set('X-SW-Cache', '1');
  return new Response(resp.body, {
    status: resp.status,
    statusText: resp.statusText,
    headers: h,
  });
}

/* NETWORK-FIRST: ქსელი მთავარია, ქეში მხოლოდ სათადარიგოა */
async function networkFirst(req, cacheName) {
  const key = cacheKey(req.url);
  try {
    const fresh = await fetch(req);
    if (fresh && fresh.ok) {
      const cache = await caches.open(cacheName);
      cache.put(key, fresh.clone());
    }
    return fresh;
  } catch (err) {
    const cached = await caches.match(key);
    if (cached) return tagCached(cached);
    throw err;
  }
}

/* STALE-WHILE-REVALIDATE: მაშინვე ქეშიდან, ფონურად განახლება */
async function staleWhileRevalidate(req, cacheName) {
  const cache  = await caches.open(cacheName);
  const cached = await cache.match(req);
  const network = fetch(req)
    .then((resp) => {
      if (resp && (resp.ok || resp.type === 'opaque')) cache.put(req, resp.clone());
      return resp;
    })
    .catch(() => null);
  return cached || network || fetch(req);
}

/* ─── მოთხოვნების მარშრუტიზაცია ─── */
self.addEventListener('fetch', (e) => {
  const req = e.request;
  if (req.method !== 'GET') return;

  let url;
  try { url = new URL(req.url); } catch (err) { return; }

  /* რუკის ფილები — პირდაპირ ქსელში, ჩარევის გარეშე */
  if (NEVER_CACHE.some((h) => url.hostname.includes(h))) return;

  /* ოპერაციული მონაცემები (Pages-ის data.json და raw fallback) */
  if (url.pathname.endsWith('data.json')) {
    e.respondWith(networkFirst(req, DATA_CACHE));
    return;
  }

  /* ნავიგაცია — ყოველთვის ახალი index.html, ქეში მხოლოდ ოფლაინში */
  if (req.mode === 'navigate') {
    e.respondWith(
      networkFirst(req, SHELL_CACHE)
        .catch(() => caches.match('./index.html').then((r) => r && tagCached(r)))
    );
    return;
  }

  /* საკუთარი აქტივები */
  if (url.origin === self.location.origin) {
    e.respondWith(networkFirst(req, SHELL_CACHE).catch(() => caches.match(req)));
    return;
  }

  /* გარე CDN — Tailwind, Google Fonts, Leaflet */
  e.respondWith(staleWhileRevalidate(req, CDN_CACHE));
});

/* გვერდიდან დაუყოვნებელი განახლების ბრძანება */
self.addEventListener('message', (e) => {
  if (e.data === 'SKIP_WAITING') self.skipWaiting();
});
