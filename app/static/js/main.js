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

// Корзина: подтверждённые сервером количества, общие для всех строк одного предложения.
(function () {
  var badge = document.querySelector('.cart-badge');
  var controls = Array.from(document.querySelectorAll('.cart-control'));
  var items = new Map();
  var pending = new Map();
  var queue = Promise.resolve();
  var loaded = false;
  var loading = false;
  var refreshPending = false;
  var changeEvent = 'das-cart-changed';

  function key(control) {
    return JSON.stringify([control.dataset.provider, control.dataset.offerKey]);
  }
  function quantity(control) {
    var item = items.get(key(control));
    return item ? item.qty : 0;
  }
  function step(control) {
    return Math.max(1, Number(control.dataset.step) || 1);
  }
  function nextQuantity(control, direction) {
    var qty = quantity(control), pack = step(control);
    return direction > 0 ? (Math.floor(qty / pack) + 1) * pack : Math.max(0, (Math.ceil(qty / pack) - 1) * pack);
  }
  function limit(control) {
    return Math.max(0, Math.min(999, Number(control.dataset.max) || 0));
  }
  function render() {
    controls.forEach(function (control) {
      var qty = quantity(control);
      var add = control.querySelector('.add-to-cart');
      var group = control.querySelector('.cart-quantity');
      var minus = control.querySelector('.cart-minus');
      var plus = control.querySelector('.cart-plus');
      var active = document.activeElement;
      var busy = (pending.get(key(control)) || 0) > 0;
      add.hidden = qty > 0;
      group.hidden = qty === 0;
      control.querySelector('.cart-quantity-value').textContent = qty;
      control.setAttribute('aria-busy', String(busy || loading));
      add.disabled = loading || busy || !control.dataset.offerKey || step(control) > limit(control);
      minus.disabled = loading || qty === 0;
      plus.disabled = loading || nextQuantity(control, 1) > limit(control);
      if (qty > 0 && active === add) minus.focus();
      if (qty === 0 && (active === minus || active === plus)) add.focus();
    });
  }
  function applyState(data) {
    if (!Array.isArray(data.items) || !Number.isInteger(data.count)) throw new Error('Не удалось получить корзину');
    items = new Map(data.items.map(function (item) {
      return [JSON.stringify([item.provider, item.offer_key]), item];
    }));
    loaded = true;
    if (badge) {
      badge.textContent = data.count;
      badge.hidden = data.count === 0;
    }
    render();
  }
  function showError(control, message) {
    controls.forEach(function (other) {
      if (control && key(other) !== key(control)) return;
      var label = other.querySelector('.cart-control-error');
      label.textContent = message;
      label.hidden = !message;
    });
  }
  async function request(url, payload) {
    var controller = new AbortController();
    var timer = setTimeout(function () { controller.abort(); }, 15000);
    try {
      var options = { credentials: 'same-origin', cache: 'no-store', signal: controller.signal,
        headers: { Accept: 'application/json' } };
      if (payload) {
        options.method = 'POST';
        options.headers['Content-Type'] = 'application/json';
        options.body = JSON.stringify(payload);
      }
      var response = await fetch(url, options);
      var data = await response.json();
      if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Не удалось изменить корзину');
      return data;
    } finally {
      clearTimeout(timer);
    }
  }
  async function refresh() {
    applyState(await request('/api/v1/cart'));
  }
  function syncCart() {
    if (refreshPending) return;
    refreshPending = true;
    loading = !loaded;
    render();
    queue = queue.then(async function () {
      try { await refresh(); }
      catch (error) { showError(null, 'Не удалось получить корзину. Повторите действие.'); }
      finally { refreshPending = false; loading = false; render(); }
    });
  }
  function notifyOtherTabs() {
    try { localStorage.setItem(changeEvent, Date.now() + '-' + Math.random()); }
    catch (error) { /* Хранилище браузера может быть отключено. */ }
  }

  document.addEventListener('click', function (event) {
    var button = event.target.closest('.add-to-cart, .cart-plus, .cart-minus');
    var control = button && button.closest('.cart-control');
    if (!control || button.disabled) return;
    var direction = button.classList.contains('cart-minus') ? -1 : 1;
    var offerKey = key(control);
    pending.set(offerKey, (pending.get(offerKey) || 0) + 1);
    render();
    // Вычисляем новое количество при выполнении, а не при клике: быстрые нажатия не теряются.
    queue = queue.then(async function () {
      showError(control, '');
      try {
        if (!loaded) await refresh();
        var qty = nextQuantity(control, direction);
        if (qty === quantity(control) || (direction > 0 && qty > limit(control))) return;
        var item = items.get(offerKey);
        var data = item
          ? await request('/cart/update', { item_id: item.id, qty: qty })
          : await request('/cart/add', { provider: control.dataset.provider, offer_key: control.dataset.offerKey, qty: qty });
        applyState(data);
        notifyOtherTabs();
      } catch (error) {
        // Ответ мог потеряться после записи: перечитываем состояние, но не повторяем изменение.
        try { await refresh(); } catch (ignored) { /* Сохраняем последнее подтверждённое количество. */ }
        showError(control, error.name === 'Error' ? error.message : 'Не удалось связаться с сервером. Повторите действие.');
      } finally {
        pending.set(offerKey, pending.get(offerKey) - 1);
        render();
      }
    });
  });
  window.addEventListener('pageshow', function (event) { if (event.persisted) syncCart(); });
  window.addEventListener('storage', function (event) { if (event.key === changeEvent) syncCart(); });
  document.addEventListener('visibilitychange', function () { if (!document.hidden) syncCart(); });
  syncCart();
})();

// Подтверждение удаления и печать сметы.
document.addEventListener('submit', function (event) {
  var message = event.target.dataset.confirm;
  if (message && !window.confirm(message)) event.preventDefault();
});
document.querySelectorAll('[data-print]').forEach(function (button) {
  button.addEventListener('click', function () { window.print(); });
});
