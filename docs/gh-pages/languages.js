/*
 * languages.js - the single declaration of which languages this site serves.
 *
 * Read by lang_switch.js and by the switcher it renders; nothing else repeats
 * the list. Written once here rather than in every page, in the sitemap and in
 * the 404, because those would be several copies of one list and every copy is
 * a chance to send a reader to a page that is not there.
 *
 * "base" is the path the site is published at. This is a project page, so it is
 * "/NeverDryCalibrator" and not "": every URL the switcher builds carries that
 * prefix.
 *
 * Each code is also its path prefix, and the code is the language tag, so the
 * three spellings stay identical and an exact-tag match is possible at all.
 *
 * Two languages and not the nine of the sibling site, deliberately. Only
 * complete, current translations belong here: a translation frozen two versions
 * back is worse than a missing one, because the reader cannot tell. This page
 * changes with a project whose thresholds are still being measured in a garden,
 * so keeping nine of them honest is a promise that would be broken quietly.
 */
window.SITE_LANGUAGES = {
  base: '/NeverDryCalibrator',
  default: 'en',
  available: [
    { code: 'en', name: 'English' },
    { code: 'it', name: 'Italiano' }
  ]
};
