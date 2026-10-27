// Переключение режима поиска в шапке
document.querySelectorAll('.search-block').forEach(function (block) {
  var tabs = block.querySelectorAll('.search-tab');
  var modeInput = block.querySelector('input[name="mode"]');
  var queryInput = block.querySelector('input[name="q"]');
  var placeholders = {
    article: 'Введите артикул, например: 04465-33471',
    vin: 'Введите VIN, 17 символов',
    model: 'Марка и модель, например: Toyota Camry 2018'
  };
  tabs.forEach(function (tab) {
    tab.addEventListener('click', function () {
      tabs.forEach(function (t) { t.classList.remove('active'); });
      tab.classList.add('active');
      modeInput.value = tab.dataset.mode;
      queryInput.placeholder = placeholders[tab.dataset.mode] || '';
      queryInput.focus();
    });
  });
});

// Карусель акций: точки и автопрокрутка
document.querySelectorAll('.carousel').forEach(function (carousel) {
  var slides = carousel.querySelectorAll('.carousel-slide');
  var dots = carousel.querySelectorAll('.dot');
  if (slides.length < 2) return;
  var current = 0;
  function show(index) {
    current = (index + slides.length) % slides.length;
    slides.forEach(function (s, i) { s.hidden = i !== current; });
    dots.forEach(function (d, i) { d.classList.toggle('active', i === current); });
  }
  dots.forEach(function (dot, i) { dot.addEventListener('click', function () { show(i); }); });
  var timer = setInterval(function () { show(current + 1); }, 6000);
  carousel.addEventListener('mouseenter', function () { clearInterval(timer); });
  show(0);
});

// Корзина: счётчик в шапке и добавление без перезагрузки страницы
(function () {
  var badge = document.querySelector('.cart-badge');
  function setCount(count) {
    if (!badge) return;
    badge.textContent = count;
    badge.hidden = !(count > 0);
  }
  fetch('/api/v1/cart/count', { headers: { Accept: 'application/json' }, credentials: 'same-origin' })
    .then(function (r) { return r.json(); })
    .then(function (d) { setCount(d.count); })
    .catch(function () {});

  document.addEventListener('click', function (e) {
    var btn = e.target.closest('.add-to-cart');
    if (!btn || btn.disabled) return;
    btn.disabled = true;
    fetch('/cart/add', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify({ provider: btn.dataset.provider, offer_key: btn.dataset.offerKey, qty: 1 })
    }).then(function (r) {
      return r.json().then(function (d) { return { ok: r.ok, data: d }; });
    }).then(function (res) {
      btn.disabled = false;
      if (!res.ok) { alert(res.data.detail || 'Не удалось добавить в корзину'); return; }
      setCount(res.data.count);
      btn.textContent = 'В корзине ✓';
      btn.classList.add('in-cart');
    }).catch(function () { btn.disabled = false; alert('Не удалось добавить в корзину'); });
  });
})();
