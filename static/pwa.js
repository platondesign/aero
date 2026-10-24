/* Регистрация service worker + установка PWA */
(function () {
  if (!('serviceWorker' in navigator)) return;

  window.addEventListener('load', function () {
    navigator.serviceWorker.register('/sw.js', { scope: '/' })
      .catch(function () { /* офлайн-режим просто не включится */ });
  });

  // кнопка «Установить» появляется, когда браузер разрешит
  var promptEvent = null;

  window.addEventListener('beforeinstallprompt', function (e) {
    e.preventDefault();
    promptEvent = e;
    var btn = document.getElementById('install-app');
    if (btn) btn.hidden = false;
  });

  document.addEventListener('click', function (e) {
    var btn = e.target.closest && e.target.closest('#install-app');
    if (!btn || !promptEvent) return;
    promptEvent.prompt();
    promptEvent.userChoice.then(function () {
      promptEvent = null;
      btn.hidden = true;
    });
  });

  window.addEventListener('appinstalled', function () {
    var btn = document.getElementById('install-app');
    if (btn) btn.hidden = true;
  });
})();
