// Service Worker - 墨水屏新闻 PWA
// 策略：network-first（在线时取最新，离线时用缓存）

const CACHE_NAME = 'eink-news-v20';
const CACHE_FILES = [
  './',
  './index.html',
  './news-data.json',
  './manifest.json',
  './icon-192.png',
  './icon-512.png',
  './icon-192-maskable.png'
];

// 安装时预缓存核心文件
self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME).then(cache => {
      return cache.addAll(CACHE_FILES).catch(err => {
        // 图标可能还没生成，忽略错误
        console.log('Cache addAll partial fail:', err);
      });
    })
  );
  self.skipWaiting();
});

// 激活时清理旧缓存
self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(keys => {
      return Promise.all(
        keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k))
      );
    })
  );
  self.clients.claim();
});

// 请求拦截：network-first 策略
self.addEventListener('fetch', event => {
  // 只处理 GET 请求
  if (event.request.method !== 'GET') return;

  event.respondWith(
    fetch(event.request)
      .then(response => {
        // 成功获取，更新缓存
        const clone = response.clone();
        caches.open(CACHE_NAME).then(cache => {
          cache.put(event.request, clone).catch(() => {});
        });
        return response;
      })
      .catch(() => {
        // 离线，从缓存读取
        return caches.match(event.request).then(cached => {
          return cached || caches.match('./index.html');
        });
      })
  );
});
