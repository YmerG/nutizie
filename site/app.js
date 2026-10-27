// Nutizie : recherche, filtre par source, pagination, dates relatives, thème.
(function () {
  var PAGE = 36;
  var racine = document.documentElement;
  var cartes = Array.prototype.slice.call(document.querySelectorAll('.carte'));
  var q = document.getElementById('q');
  var filtres = document.querySelectorAll('.filtre');
  var etat = document.getElementById('etat');
  var vide = document.getElementById('vide');
  var plus = document.getElementById('plus');
  var source = '';
  var limite = PAGE;

  function normal(s) {
    return s.toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '');
  }
  cartes.forEach(function (c) { c._texte = normal(c.querySelector('h2').textContent + ' ' + ((c.querySelector('.extrait') || {}).textContent || '')); });

  // Une image qui ne se charge pas : on retire le cadre vide.
  document.addEventListener('error', function (e) {
    if (e.target.tagName === 'IMG' && e.target.closest('.carte')) { e.target.remove(); appliquer(); }
  }, true);

  function appliquer() {
    var mots = normal(q.value).split(/\s+/).filter(Boolean);
    var n = 0, une = null;
    cartes.forEach(function (c) {
      var ok = (!source || c.dataset.source === source) &&
        mots.every(function (m) { return c._texte.indexOf(m) !== -1; });
      if (ok) n++;
      var visible = ok && n <= limite;
      c.classList.toggle('cachee', !visible);
      c.classList.remove('une');
      if (visible && !une && c.querySelector('img')) une = c;
    });
    if (une) une.classList.add('une');
    var s = n > 1 ? 's' : '';
    etat.textContent = n + ' article' + s + (mots.length || source ? ' trouvé' + s : '');
    vide.hidden = n > 0;
    plus.hidden = n <= limite;
  }

  q.addEventListener('input', function () { limite = PAGE; appliquer(); });
  Array.prototype.forEach.call(filtres, function (b) {
    b.addEventListener('click', function () {
      source = b.dataset.source;
      Array.prototype.forEach.call(filtres, function (x) { x.setAttribute('aria-pressed', String(x === b)); });
      limite = PAGE;
      appliquer();
    });
  });
  plus.addEventListener('click', function () {
    var premiere = cartes.filter(function (c) { return !c.classList.contains('cachee'); }).length;
    limite += PAGE;
    appliquer();
    // le focus va sur le premier nouvel article, pour continuer la lecture au clavier
    var visibles = cartes.filter(function (c) { return !c.classList.contains('cachee'); });
    if (visibles[premiere]) visibles[premiere].querySelector('h2 a').focus();
  });

  // Dates relatives : « il y a 12 min », « il y a 3 h », « hier, 18 h 05 »
  var maintenant = Date.now();
  function jour(x) { return new Date(x).toLocaleDateString('fr-FR', { timeZone: 'Europe/Paris' }); }
  document.querySelectorAll('.carte time').forEach(function (t) {
    var d = new Date(t.getAttribute('datetime'));
    var min = Math.round((maintenant - d) / 60000);
    var h = d.toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit', timeZone: 'Europe/Paris' }).replace(':', ' h ').replace(/^0(?=\d)/, '');
    var heure = h === '0 h 00' ? '' : ', ' + h; // minuit pile : le flux ne donne que le jour
    var txt = null;
    if (heure && min >= 0 && min < 60) txt = 'il y a ' + Math.max(min, 1) + ' min';
    else if (heure && min >= 60 && min < 12 * 60) txt = 'il y a ' + Math.round(min / 60) + ' h';
    else if (jour(d) === jour(maintenant)) txt = "aujourd'hui" + heure;
    else if (jour(d) === jour(maintenant - 864e5)) txt = 'hier' + heure;
    if (txt) { t.title = t.textContent; t.textContent = txt; }
  });

  // Thème : suit le téléphone, sauf si le visiteur a choisi
  var bouton = document.getElementById('theme');
  function sombre() {
    return racine.dataset.theme ? racine.dataset.theme === 'dark' : matchMedia('(prefers-color-scheme: dark)').matches;
  }
  function etiquette() { bouton.setAttribute('aria-label', sombre() ? 'Thème clair' : 'Thème sombre'); }
  bouton.addEventListener('click', function () {
    racine.dataset.theme = sombre() ? 'light' : 'dark';
    try { localStorage.setItem('theme', racine.dataset.theme); } catch (e) {}
    etiquette();
  });
  etiquette();
  appliquer();
})();
