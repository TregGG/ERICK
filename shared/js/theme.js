// Light / dark / system theme switch shared by every app.
const KEY = 'campusrick.theme';
const ORDER = ['system', 'light', 'dark'];
const LABELS = { system: ['\u{1F5A5}️', 'Theme: automatic'], light: ['☀️', 'Theme: light'], dark: ['\u{1F319}', 'Theme: dark'] };
const media = window.matchMedia('(prefers-color-scheme: dark)');
const buttons = new Set();

function choice() {
  try { return localStorage.getItem(KEY) || 'system'; } catch { return 'system'; }
}

function apply() {
  const c = choice();
  const dark = c === 'dark' || (c === 'system' && media.matches);
  document.documentElement.dataset.theme = dark ? 'dark' : 'light';
  document.documentElement.dataset.themeChoice = c;
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) {
    meta.dataset.light = meta.dataset.light || meta.content;
    meta.content = dark ? '#121a16' : meta.dataset.light;
  }
  buttons.forEach(render);
}

function render(button) {
  const [icon, label] = LABELS[choice()];
  button.textContent = icon;
  button.title = `${label} (click to change)`;
  button.setAttribute('aria-label', `${label}. Click to change.`);
}

export function themeToggle(className = 'pill') {
  const button = document.createElement('button');
  button.type = 'button';
  button.className = `${className} theme-toggle`;
  button.addEventListener('click', () => {
    const next = ORDER[(ORDER.indexOf(choice()) + 1) % ORDER.length];
    try { localStorage.setItem(KEY, next); } catch { /* storage blocked: applies for this page only */ }
    apply();
  });
  buttons.add(button);
  render(button);
  return button;
}

media.addEventListener('change', apply);
window.addEventListener('storage', (e) => { if (e.key === KEY) apply(); });
apply();
