// Phone + OTP sign-in screen shared by all three apps.
import { $, busy, html, raw } from './ui.js';
import { themeToggle } from './theme.js';

export function showLogin({ api, app, title, subtitle, askName = false, onSuccess }) {
  const screen = document.createElement('div');
  screen.className = 'auth-screen';
  screen.innerHTML = html`
    <form class="card auth-card stack" novalidate>
      <div class="row-between"><div class="brand" style="box-shadow:none;padding-left:0"><span class="brand-mark" aria-hidden="true">&#x1F6FA;</span>
        <span>Campus Rick<small>IIT Roorkee</small></span></div><span data-theme-slot></span></div>
      <div><h1>${title}</h1><p class="muted" style="margin:0">${subtitle}</p></div>
      <div data-step="phone">
        <div class="field"><label for="login-phone">Mobile number</label>
          <input id="login-phone" class="input" type="tel" inputmode="tel" autocomplete="tel" placeholder="98765 43210" required></div>
        <button class="btn btn-primary btn-block" data-action="send" type="submit">Send login code</button>
      </div>
      <div data-step="code" hidden>
        <p class="small muted" data-sent-to></p>
        <div data-dev hidden class="banner banner-warn"><div><strong>Development mode.</strong> No SMS is sent; your code is
          <span class="dev-otp" data-dev-code></span></div></div>
        <div class="field"><label for="login-code">6-digit code</label>
          <input id="login-code" class="input code" inputmode="numeric" autocomplete="one-time-code" maxlength="6" pattern="\\d{6}"></div>
        ${askName ? raw(html`<div class="field" data-name-field hidden><label for="login-name">Your name</label>
          <input id="login-name" class="input" autocomplete="name" maxlength="80" placeholder="As drivers will see it"></div>`) : ''}
        <button class="btn btn-primary btn-block" data-action="verify" type="submit">Verify and continue</button>
        <div class="row-between small" style="margin-top:10px">
          <button type="button" class="btn btn-ghost btn-sm" data-action="change">Change number</button>
          <button type="button" class="btn btn-ghost btn-sm" data-action="resend" disabled>Resend code</button>
        </div>
      </div>
      <p class="error-text" role="alert" data-error></p>
    </form>`;
  document.body.appendChild(screen);
  $('[data-theme-slot]', screen).replaceWith(themeToggle('btn btn-outline btn-icon'));

  const form = $('form', screen);
  const error = $('[data-error]', screen);
  const phoneInput = $('#login-phone', screen);
  const codeInput = $('#login-code', screen);
  let step = 'phone';
  let phone = '';
  let resendTimer = null;

  const setError = (msg) => { error.textContent = msg || ''; };
  const showStep = (name) => {
    step = name;
    $('[data-step="phone"]', screen).hidden = name !== 'phone';
    $('[data-step="code"]', screen).hidden = name !== 'code';
    (name === 'phone' ? phoneInput : codeInput).focus();
  };

  const startResendCountdown = (seconds) => {
    const btn = $('[data-action="resend"]', screen);
    clearInterval(resendTimer);
    let left = seconds;
    btn.disabled = true;
    const tick = () => {
      btn.textContent = left > 0 ? `Resend in ${left}s` : 'Resend code';
      btn.disabled = left > 0;
      left -= 1;
      if (left < -1) clearInterval(resendTimer);
    };
    tick();
    resendTimer = setInterval(tick, 1000);
  };

  async function requestCode(button) {
    setError('');
    phone = phoneInput.value.trim();
    if (!phone) { setError('Enter your mobile number.'); phoneInput.focus(); return; }
    await busy(button, async () => {
      try {
        const res = await api.post('/api/auth/otp/request', { phone, app }, { auth: false });
        $('[data-sent-to]', screen).textContent = `We sent a code to ${phone}. It expires in ${Math.round(res.expires_in / 60)} minutes.`;
        const dev = $('[data-dev]', screen);
        dev.hidden = !res.dev_otp;
        if (res.dev_otp) $('[data-dev-code]', screen).textContent = res.dev_otp;
        const nameField = $('[data-name-field]', screen);
        if (nameField) nameField.hidden = !res.new_account;
        codeInput.value = '';
        showStep('code');
        startResendCountdown(res.resend_in || 30);
      } catch (err) {
        setError(err.message);
      }
    });
  }

  async function verify(button) {
    setError('');
    const code = codeInput.value.trim();
    if (!/^\d{6}$/.test(code)) { setError('Enter the 6-digit code.'); codeInput.focus(); return; }
    const nameInput = $('#login-name', screen);
    const name = nameInput && !$('[data-name-field]', screen).hidden ? nameInput.value.trim() : undefined;
    if (nameInput && !$('[data-name-field]', screen).hidden && (!name || name.length < 2)) {
      setError('Enter your name so your driver can find you.'); nameInput.focus(); return;
    }
    await busy(button, async () => {
      try {
        const res = await api.post('/api/auth/otp/verify', { phone, app, code, name }, { auth: false });
        api.token = res.access_token;
        clearInterval(resendTimer);
        screen.remove();
        onSuccess(res.user);
      } catch (err) {
        setError(err.message);
      }
    });
  }

  form.addEventListener('submit', (e) => {
    e.preventDefault();
    if (step === 'phone') requestCode($('[data-action="send"]', screen));
    else verify($('[data-action="verify"]', screen));
  });
  codeInput.addEventListener('input', () => { codeInput.value = codeInput.value.replace(/\D/g, '').slice(0, 6); });
  $('[data-action="change"]', screen).addEventListener('click', () => { setError(''); showStep('phone'); });
  $('[data-action="resend"]', screen).addEventListener('click', (e) => requestCode(e.currentTarget));
  phoneInput.focus();
}

export async function logout(api, realtime) {
  try { await api.post('/api/auth/logout'); } catch { /* token may already be invalid */ }
  realtime?.stop();
  api.token = null;
  location.reload();
}
