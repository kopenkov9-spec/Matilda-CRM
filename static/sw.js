const CACHE = 'matilda-shell-v5';
const SHELL = ['/', '/index.html', '/style.css', '/themes.css', '/themes.js', '/app.js', '/completion.js', '/lifecycle.js', '/icon.svg', '/icon-192.png', '/icon-512.png', '/manifest.webmanifest'];
self.addEventListener('install', event => event.waitUntil(caches.open(CACHE).then(c=>c.addAll(SHELL)).then(()=>self.skipWaiting())));
self.addEventListener('activate', event=>event.waitUntil(caches.keys().then(keys=>Promise.all(keys.filter(k=>k!==CACHE).map(k=>caches.delete(k)))).then(()=>self.clients.claim())));
self.addEventListener('fetch', event=>{
  const url = new URL(event.request.url);
  if(url.origin!==location.origin || url.pathname.startsWith('/api/') || event.request.method!=='GET') return;
  event.respondWith(fetch(event.request).then(r=>{if(r.ok && SHELL.includes(url.pathname)){const copy=r.clone(); caches.open(CACHE).then(c=>c.put(event.request,copy));}return r;}).catch(()=>caches.match(event.request).then(r=>r || (event.request.mode==='navigate'?caches.match('/'):Response.error()))));
});
