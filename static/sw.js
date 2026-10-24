/* Авиамодельный клуб — service worker.
   Стратегия:
   - HTML-страницы: сеть, при недоступности — офлайн-заглушка
   - статика (css, иконки): кэш, обновление в фоне
   - API: сеть, при недоступности — последний удачный ответ из кэша
*/

const VERSION = 'aero-v5';
const SHELL = VERSION + '-shell';
const API   = VERSION + '-api';

const SHELL_FILES = [
  '/',
  '/news',
  '/schedule',
  '/team',
  '/static/style.css',
  '/static/icon-192.png',
  '/static/icon-512.png',
  '/offline'
];

self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(SHELL)
      .then(c => c.addAll(SHELL_FILES).catch(() => {}))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys
        .filter(k => !k.startsWith(VERSION))
        .map(k => caches.delete(k))
      )
    ).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', event => {
  const req = event.request;
  const url = new URL(req.url);

  // только свой источник
  if (url.origin !== self.location.origin) return;

  // запросы с авторизацией и POST не кэшируем
  if (req.method !== 'GET') return;
  if (url.pathname.startsWith('/cabinet') ||
      url.pathname.startsWith('/admin') ||
      url.pathname.startsWith('/editor') ||
      url.pathname.startsWith('/login') ||
      url.pathname.startsWith('/logout')) return;

  // ── API: сеть, потом кэш ──
  if (url.pathname.startsWith('/api/')) {
    event.respondWith(
      fetch(req)
        .then(resp => {
          const copy = resp.clone();
          caches.open(API).then(c => c.put(req, copy)).catch(() => {});
          return resp;
        })
        .catch(() => caches.match(req).then(hit => hit || new Response(
          JSON.stringify({ ok: false, offline: true }),
          { headers: { 'Content-Type': 'application/json' } }
        )))
    );
    return;
  }

  // ── статика: кэш, потом сеть ──
  if (url.pathname.startsWith('/static/')) {
    event.respondWith(
      caches.match(req).then(hit => {
        const net = fetch(req).then(resp => {
          const copy = resp.clone();
          caches.open(SHELL).then(c => c.put(req, copy)).catch(() => {});
          return resp;
        }).catch(() => hit);
        return hit || net;
      })
    );
    return;
  }

  // ── страницы: сеть, при обрыве — кэш или заглушка ──
  event.respondWith(
    fetch(req)
      .then(resp => {
        const copy = resp.clone();
        caches.open(SHELL).then(c => c.put(req, copy)).catch(() => {});
        return resp;
      })
      .catch(() => caches.match(req)
        .then(hit => hit || caches.match('/offline') || caches.match('/'))
      )
  );
});
