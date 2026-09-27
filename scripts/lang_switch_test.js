/*
 * lang_switch_test.js - run the language switch against a stub window.
 *
 * What it covers: the four conditions the automatic redirect has to satisfy
 * (decided once, one direction only, an explicit choice always wins, never
 * towards a language this site does not publish) and what the switcher renders.
 *
 * Why it exists: lang_switch.js is the one file on this site that can strand a
 * reader. A wrong prefix sends them to a 404, and a rule applied in both
 * directions bounces the browser between two languages until it gives up. Both
 * failures are in the redirect logic, so neither is visible in the markup and
 * neither is caught by check_gh_pages.py, which only reads what is on disk.
 *
 * It also guards the pair of files rather than each alone: the codes in
 * languages.js are path prefixes, so renaming one there without moving its
 * directory breaks every link the switcher builds.
 *
 *     node scripts/lang_switch_test.js
 */
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const GH = path.join(__dirname, '..', 'docs', 'gh-pages');
const LANGUAGES = fs.readFileSync(path.join(GH, 'languages.js'), 'utf8');
const SWITCH = fs.readFileSync(path.join(GH, 'lang_switch.js'), 'utf8');

function run({ path, search = '', languages = ['en-US'], stored = null, withHost = true }) {
  const replaced = [];
  const store = new Map();
  if (stored) store.set('site.lang', stored);

  const links = [];
  const host = {
    appendChild: (node) => links.push(node),
  };

  const element = () => ({
    href: '', textContent: '', attrs: {},
    setAttribute(k, v) { this.attrs[k] = v; },
    addEventListener() {},
  });

  const window = {
    location: {
      pathname: path,
      search,
      hash: '',
      replace: (url) => replaced.push(url),
    },
    navigator: { languages },
    localStorage: {
      getItem: (k) => (store.has(k) ? store.get(k) : null),
      setItem: (k, v) => store.set(k, v),
    },
    URLSearchParams,
  };
  const document = {
    readyState: 'complete',
    querySelector: (sel) => (withHost && sel === '[data-lang-switcher]' ? host : null),
    createElement: element,
    addEventListener: (_, fn) => fn(),
  };

  const context = vm.createContext({ window, document, URLSearchParams, console });
  context.globalThis = context;
  vm.runInContext(LANGUAGES, context);
  vm.runInContext(SWITCH, context);

  return { redirect: replaced[0] || null, links, stored: store.get('site.lang') || null };
}

let failures = 0;
function check(name, actual, expected) {
  const ok = JSON.stringify(actual) === JSON.stringify(expected);
  if (!ok) failures++;
  console.log(`${ok ? 'ok  ' : 'FAIL'}  ${name}${ok ? '' : `\n        got      ${JSON.stringify(actual)}\n        expected ${JSON.stringify(expected)}`}`);
}

// The cases below are the ones that strand a reader, and none of them is
// visible in the rendered markup: a wrong prefix is a 404, and a rule applied in
// both directions bounces the browser between two languages for ever. Two
// languages here rather than the sibling site's nine, so the variant cases that
// site needs (exact tag beating base language) have no equivalent and are not
// pretended into existence.

// 1. An Italian browser arriving at the canonical English page is taken to the
//    translation that exists.
check('it browser on / goes to /it/',
  run({ path: '/NeverDryCalibrator/', languages: ['it-IT', 'it'] }).redirect, '/NeverDryCalibrator/it/');

// 2. Base language is enough: it-CH is Italian and there is no Swiss variant.
check('it-CH browser goes to /it/',
  run({ path: '/NeverDryCalibrator/', languages: ['it-CH', 'it'] }).redirect, '/NeverDryCalibrator/it/');

// 3. No translation for the browser's languages: English, silently. This is the
//    common case on a two-language site and it must be quiet, not a fallback
//    notice.
check('de browser stays on English',
  run({ path: '/NeverDryCalibrator/', languages: ['de-DE', 'de'] }).redirect, null);
check('ja browser stays on English',
  run({ path: '/NeverDryCalibrator/', languages: ['ja-JP', 'ja'] }).redirect, null);

// 4. One direction only: a reader on a translation is never sent anywhere,
//    which is what makes a loop impossible.
check('it page never redirects away (even for a German browser)',
  run({ path: '/NeverDryCalibrator/it/', languages: ['de-DE'] }).redirect, null);

// 5. An explicit choice outranks detection, and outlasts the visit.
check('stored English wins over an Italian browser',
  run({ path: '/NeverDryCalibrator/', languages: ['it-IT'], stored: 'en' }).redirect, null);
check('stored Italian is honoured without re-detecting',
  run({ path: '/NeverDryCalibrator/', languages: ['ja-JP'], stored: 'it' }).redirect, '/NeverDryCalibrator/it/');

// 6. ?lang= is a declaration by the reader: it redirects and is remembered.
const override = run({ path: '/NeverDryCalibrator/', search: '?lang=it', languages: ['en-US'] });
check('?lang=it redirects to /it/', override.redirect, '/NeverDryCalibrator/it/');
check('?lang=it is remembered', override.stored, 'it');
check('?lang=de for an unpublished language is ignored',
  run({ path: '/NeverDryCalibrator/', search: '?lang=de', languages: ['en-US'] }).redirect, null);

// 7. Landing straight on a translation counts as a choice.
check('arriving on /it/ stores it',
  run({ path: '/NeverDryCalibrator/it/', languages: ['en-US'] }).stored, 'it');

// 8. Decided once: the first visit writes the choice down.
check('first visit records the detected language',
  run({ path: '/NeverDryCalibrator/', languages: ['it-IT'] }).stored, 'it');

// 9. The switcher: every published language, in its own name, current marked.
const rendered = run({ path: '/NeverDryCalibrator/it/', languages: ['it-IT'] });
check('switcher renders one link per language',
  rendered.links.length, 2);
check('switcher writes each language in its own name',
  rendered.links.map((l) => l.textContent),
  ['English', 'Italiano']);
check('switcher points English back at the canonical root',
  rendered.links[0].href, '/NeverDryCalibrator/');
check('switcher marks the current language',
  rendered.links.filter((l) => l.attrs['aria-current'] === 'true').map((l) => l.textContent),
  ['Italiano']);
check('switcher links carry the base path',
  rendered.links.map((l) => l.href),
  ['/NeverDryCalibrator/', '/NeverDryCalibrator/it/']);

// 10. A deep page keeps its place when the language changes (the tree mirrors).
check('a deep English page maps to the same page under a prefix',
  run({ path: '/NeverDryCalibrator/index.html', languages: ['it-IT'] }).redirect, '/NeverDryCalibrator/it/index.html');

console.log(failures ? `\n${failures} failure(s)` : '\nall checks passed');
process.exit(failures ? 1 : 0);
