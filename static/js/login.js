
/* ── PWD TOGGLE ── */
const pwdIn  = document.getElementById('password');
const pwdBtn = document.getElementById('pwdToggle');

pwdBtn.addEventListener('click', () => {
  const show = pwdIn.type === 'password';
  pwdIn.type = show ? 'text' : 'password';

  pwdBtn.innerHTML = show
    ? '<svg viewBox="0 0 24 24" fill="none"><path d="M17.94 17.94A10.07 10.07 0 0112 20c-7 0-11-8-11-8a18.45 18.45 0 015.06-5.94M9.9 4.24A9.12 9.12 0 0112 4c7 0 11 8 11 8a18.5 18.5 0 01-2.16 3.19m-6.72-1.07a3 3 0 01-4.24-4.24" stroke="currentColor" stroke-width="2"/><line x1="1" y1="1" x2="23" y2="23" stroke="currentColor" stroke-width="2"/></svg>'
    : '<svg viewBox="0 0 24 24" fill="none"><path d="M1 12s4-7 11-7 11 7 11 7-4 7-11 7S1 12 1 12z" stroke="currentColor" stroke-width="2"/><circle cx="12" cy="12" r="3" stroke="currentColor" stroke-width="2"/></svg>';
});

/* ── MODE DETECTION ── */
const urlMode    = new URLSearchParams(window.location.search).get('mode');
let isRegister   = urlMode === 'register' || window.location.pathname === '/register';

const eyebrow   = document.querySelector('.form-eyebrow');
const titleEl   = document.querySelector('.form-title');
const switchEl  = document.querySelector('.form-switch');
const submitBtn = document.getElementById('submitBtn');

function applyMode() {
  if (isRegister) {
    eyebrow.textContent   = 'Create Account';
    titleEl.textContent   = 'Get Started';
    switchEl.innerHTML    = 'Already have an account? <a href="/login">Sign in →</a>';
    submitBtn.textContent = 'Create Account';
  } else {
    eyebrow.textContent   = 'Sign In';
    titleEl.textContent   = 'Sign In';
    switchEl.innerHTML    = 'No account yet? <a href="/register">Create one free →</a>';
    submitBtn.textContent = 'Sign In';
  }
}
applyMode();

/* ── FORM LOGIC ── */
const form    = document.getElementById('loginForm');
const userIn  = document.getElementById('username');
const passIn  = document.getElementById('password');
const userErr = document.getElementById('userErr');
const passErr = document.getElementById('passErr');
const alertEl = document.getElementById('alertErr');
const alertMsg= document.getElementById('alertMsg');

function setErr(el, errEl, msg) {
  el.classList.add('err');
  el.setAttribute('aria-invalid', 'true');
  errEl.textContent = msg;
  errEl.classList.add('show');
}

function clearErr(el, errEl) {
  el.classList.remove('err');
  el.setAttribute('aria-invalid', 'false');
  errEl.classList.remove('show');
}

userIn.addEventListener('input', () => clearErr(userIn, userErr));
passIn.addEventListener('input', () => clearErr(passIn, passErr));

form.addEventListener('submit', async e => {
  e.preventDefault();
  alertEl.classList.remove('show');

  const username = userIn.value.trim();
  const password = passIn.value;
  let ok = true;

  /* ✅ MINIMAL LOGIN VALIDATION */
  if (!username) {
    setErr(userIn, userErr, 'Username is required.');
    ok = false;
  }

  if (!password) {
    setErr(passIn, passErr, 'Password is required.');
    ok = false;
} else if (isRegister && password.length < 6) {
    setErr(passIn, passErr, 'Password must be at least 6 characters.');
    ok = false;
}

  if (!ok) return;

  /* LOADING STATE */
  submitBtn.disabled = true;
  submitBtn.innerHTML = `
    <span style="display:flex;align-items:center;gap:8px;justify-content:center;">
      <span style="
        width:14px;height:14px;border:2px solid white;
        border-top:2px solid transparent;border-radius:50%;
        animation:spin 0.6s linear infinite;"></span>
      ${isRegister ? 'Creating...' : 'Signing in...'}
    </span>
  `;

  const endpoint = isRegister ? '/api/auth/register' : '/api/auth/login';

  try {
    const res = await fetch(endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password }),
    });

    let data = {};
    try {
      data = await res.json();
    } catch {
      throw new Error("Invalid server response");
    }

    console.log("Status:", res.status);

    if (res.ok && (data.success !== false)) {
      submitBtn.innerHTML = '✓ Success';
      submitBtn.style.background = '#10b981';

      setTimeout(() => {
        // Change this line:
        window.location.href = data.redirect || '/dashboard';
      }, 700);

    } else {
      alertMsg.textContent = data.message || 'Invalid credentials.';
      alertEl.classList.add('show');

      submitBtn.innerHTML = isRegister ? 'Create Account' : 'Sign In';
      submitBtn.disabled  = false;
    }

  } catch (err) {
    alertMsg.textContent = err.message || 'Network error. Try again.';
    alertEl.classList.add('show');

    submitBtn.innerHTML = isRegister ? 'Create Account' : 'Sign In';
    submitBtn.disabled  = false;
  }
});

/* SPINNER */
const style = document.createElement('style');
style.innerHTML = `
@keyframes spin {
  to { transform: rotate(360deg); }
}`;
document.head.appendChild(style);