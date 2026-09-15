// Service worker do AURUM: permite instalar como app e abrir a tela mesmo com o servidor desligado.
// Estratégia "rede primeiro": com o servidor ligado sempre carrega a versão nova; sem servidor usa a cópia salva
// e o painel mostra a tela "O AURUM está desligado". Chamadas /api/ nunca são guardadas.

const CACHE = "aurum-shell-v3";
const SHELL = [
  "/", "/index.html", "/css/styles.css", "/manifest.webmanifest",
  "/js/app.js", "/js/api.js", "/js/chart.js", "/js/format.js", "/js/live.js", "/js/notify.js", "/js/render.js",
  "/js/small.js", "/js/tour.js", "/js/tutorial.js", "/js/views.js", "/icons/aurum-192.png", "/icons/aurum-512.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== "GET" || url.origin !== self.location.origin || url.pathname.startsWith("/api/")) {
    return;
  }
  event.respondWith(
    fetch(event.request)
      .then((response) => {
        if (response.ok) {
          const copy = response.clone();
          caches.open(CACHE).then((cache) => cache.put(event.request, copy));
        }
        return response;
      })
      .catch(() => caches.match(event.request).then((hit) => hit || caches.match("/index.html"))),
  );
});
