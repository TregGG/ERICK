// Loaded synchronously in <head> (not a module) so the saved theme applies before first paint.
(function () {
  var choice = 'system';
  try { choice = localStorage.getItem('campusrick.theme') || 'system'; } catch (e) { /* storage blocked */ }
  var dark = choice === 'dark' || (choice === 'system' && window.matchMedia && matchMedia('(prefers-color-scheme: dark)').matches);
  document.documentElement.dataset.theme = dark ? 'dark' : 'light';
  document.documentElement.dataset.themeChoice = choice;
})();
