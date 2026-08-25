const C='nongfon-spk-v8-1';
const STATIC=['./','./index.html','./manifest.webmanifest','./scene.jpg','./icon-192.png','./icon-512.png','./push-config.js'];
try{importScripts('https://cdn.onesignal.com/sdks/web/v16/OneSignalSDK.sw.js');}catch(e){}
self.addEventListener('install',e=>{e.waitUntil(caches.open(C).then(c=>c.addAll(STATIC)));self.skipWaiting();});
self.addEventListener('activate',e=>{e.waitUntil(caches.keys().then(keys=>Promise.all(keys.filter(k=>k!==C).map(k=>caches.delete(k)))).then(()=>self.clients.claim()));});
self.addEventListener('fetch',e=>{
  const u=new URL(e.request.url);
  if(u.origin!==location.origin)return;

  const isLive = e.request.mode==='navigate' ||
                 u.pathname.endsWith('/index.html') ||
                 u.pathname.endsWith('/push-config.js') ||
                 u.pathname.endsWith('/data/radar_status.json');

  if(isLive){
    e.respondWith(
      fetch(e.request,{cache:'no-store'})
        .then(r=>{
          const copy=r.clone();
          caches.open(C).then(c=>c.put(e.request,copy)).catch(()=>{});
          return r;
        })
        .catch(()=>caches.match(e.request))
    );
    return;
  }

  e.respondWith(
    caches.match(e.request).then(r=>r||fetch(e.request).then(net=>{
      const copy=net.clone();
      caches.open(C).then(c=>c.put(e.request,copy)).catch(()=>{});
      return net;
    }))
  );
});
self.addEventListener('push',e=>{
  let d={title:'เฝ้าน้องฝน สมุทรปราการ ☔💗',body:'มีการอัปเดตสถานการณ์ฝน'};
  try{if(e.data)d=e.data.json()}catch(x){}
  e.waitUntil(self.registration.showNotification(d.title||'เฝ้าน้องฝน สมุทรปราการ',{body:d.body||'มีการอัปเดตสถานการณ์ฝน',icon:'./icon-192.png',badge:'./icon-192.png',tag:d.tag||'nongfon-spk',data:{url:d.url||'./'}}));
});
self.addEventListener('notificationclick',e=>{e.notification.close();e.waitUntil(clients.openWindow(e.notification?.data?.url||'./'));});
