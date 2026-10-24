/* Просмотр фотографий новости на весь экран.
   Листание: стрелки на экране, клавиши ←/→, свайп пальцем. */
(function () {
  'use strict';

  var lb = document.getElementById('lightbox');
  if (!lb) return;

  var big     = lb.querySelector('.lb-img');
  var counter = lb.querySelector('.lb-count');
  var prevBtn = lb.querySelector('.lb-prev');
  var nextBtn = lb.querySelector('.lb-next');
  var closeBt = lb.querySelector('.lb-close');

  var items = Array.prototype.slice.call(
    document.querySelectorAll('.zoomable')
  );
  if (!items.length) return;

  var total = items.length;
  var index = 0;
  var startX = null, startY = null;

  function preload(i) {
    var im = new Image();
    im.src = items[(i + total) % total].src;
  }

  function show(i) {
    index = (i + total) % total;

    big.src = items[index].src;
    big.alt = items[index].alt || '';

    var many = total > 1;
    if (counter) counter.textContent = (index + 1) + ' / ' + total;
    if (prevBtn) prevBtn.hidden = !many;
    if (nextBtn) nextBtn.hidden = !many;
    if (counter) counter.hidden = !many;

    preload(index + 1);
    preload(index - 1);
  }

  function open(i) {
    show(i);
    lb.hidden = false;
    document.body.style.overflow = 'hidden';
  }

  function close() {
    lb.hidden = true;
    document.body.style.overflow = '';
    big.removeAttribute('src');
  }

  function next() { show(index + 1); }
  function prev() { show(index - 1); }

  // ── открытие по клику на любое фото статьи ──
  items.forEach(function (img, i) {
    img.addEventListener('click', function (e) {
      e.preventDefault();
      open(i);
    });
  });

  // ── кнопки ──
  if (nextBtn) nextBtn.addEventListener('click', function (e) {
    e.stopPropagation(); next();
  });
  if (prevBtn) prevBtn.addEventListener('click', function (e) {
    e.stopPropagation(); prev();
  });
  if (closeBt) closeBt.addEventListener('click', function (e) {
    e.stopPropagation(); close();
  });

  // ── клик по тёмному фону закрывает, по самой картинке — нет ──
  lb.addEventListener('click', function (e) {
    if (e.target === lb) close();
  });

  // ── клавиатура ──
  document.addEventListener('keydown', function (e) {
    if (lb.hidden) return;
    if (e.key === 'Escape')      close();
    else if (e.key === 'ArrowRight') next();
    else if (e.key === 'ArrowLeft')  prev();
  });

  // ── свайп ──
  lb.addEventListener('touchstart', function (e) {
    if (e.touches.length !== 1) return;
    startX = e.touches[0].clientX;
    startY = e.touches[0].clientY;
  }, { passive: true });

  lb.addEventListener('touchend', function (e) {
    if (startX === null) return;
    var t  = e.changedTouches[0];
    var dx = t.clientX - startX;
    var dy = t.clientY - startY;
    startX = startY = null;

    // короткий жест или вертикальный — игнорируем
    if (Math.abs(dx) < 45) return;
    if (Math.abs(dx) < Math.abs(dy)) return;

    if (dx < 0) next(); else prev();
  }, { passive: true });
})();
