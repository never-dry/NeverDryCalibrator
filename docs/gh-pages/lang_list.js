/*
 * lang_list.js - render the list of published languages as plain links.
 *
 * For pages that must show every language but must not take anybody anywhere on
 * their own: the 404, which GitHub Pages serves at whatever address was
 * requested, so the automatic switch of lang_switch.js would compute a
 * translation path out of a URL that does not exist and send the reader from a
 * missing page to a different missing page.
 *
 * It reads the same map as the switch (languages.js) and holds no list of its
 * own. The one this replaced was written by hand and had gone stale: it pointed
 * at eight addresses that had moved, which is exactly the drift the
 * single-declaration rule exists to prevent.
 *
 * Markup: <p data-lang-list></p>, with languages.js loaded before this file.
 */
(function () {
  'use strict';

  var config = window.SITE_LANGUAGES;
  var host = document.querySelector('[data-lang-list]');
  if (!config || !config.available || !host) return;

  var base = (config.base || '').replace(/\/+$/, '');
  var fallback = config.default || 'en';

  config.available.forEach(function (language, index) {
    if (index) host.appendChild(document.createTextNode(' · '));
    var link = document.createElement('a');
    link.href = base + (language.code === fallback ? '/' : '/' + language.code + '/');
    link.textContent = language.name;
    link.setAttribute('lang', language.code);
    link.setAttribute('hreflang', language.code);
    host.appendChild(link);
  });
})();
