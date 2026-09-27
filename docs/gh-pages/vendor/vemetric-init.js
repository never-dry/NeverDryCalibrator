/*
 * Queue stub for the analytics script.
 *
 * Events can be fired by the page before vemetric-main.js has loaded (it is
 * deferred, and an outbound click can happen first). These two lines give those
 * calls somewhere to land: vmtrc() pushes into vmtrcq, and the real script
 * drains the queue once it is up.
 *
 * This is in a file rather than inline in each page for one reason: an inline
 * script would require script-src 'unsafe-inline' in the Content-Security
 * Policy, and that single word would also permit any script an attacker manages
 * to inject into the markup. Ten lines of file buy back the whole directive.
 */
window.vmtrcq = window.vmtrcq || [];
window.vmtrc = window.vmtrc || function () {
  window.vmtrcq.push(Array.prototype.slice.call(arguments));
};
