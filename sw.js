const C='nongfon-spk-v7',S=['./','./index.html','./manifest.webmanifest','./scene.jpg','./icon-192.png','./icon-512.png'];
self.addEventListener('install',e=>{e.waitUntil(caches.open(C).then(c=>c.addAll(S)));self.skipWaiting();});
self.addEventListener('activate',e=>e.waitUntil(self.clients.claim()));
self.addEventListener('fetch',e=>{const u=new URL(e.request.url);if(u.origin===location.origin)e.respondWith(caches.match(e.request).then(r=>r||fetch(e.request)));});
self.addEventListener('push',e=>{let d={title:'เฝ้าน้องฝน สมุทรปราการ ☔💗',body:'มีการอัปเดตสถานการณ์ฝน'};try{if(e.data)d=e.data.json()}catch(x){};e.waitUntil(self.registration.showNotification(d.title,{body:d.body,icon:'./icon-192.png',tag:'nongfon-spk'}));});
self.addEventListener('notificationclick',e=>{e.notification.close();e.waitUntil(clients.openWindow('./'));});