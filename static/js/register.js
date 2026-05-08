
/* ── PWD TOGGLES ── */
function makePwdToggle(inputId,btnId){
  const inp=document.getElementById(inputId),btn=document.getElementById(btnId);
  const eyeOpen='<svg viewBox="0 0 24 24" fill="none"><path d="M1 12s4-7 11-7 11 7 11 7-4 7-11 7S1 12 1 12z" stroke="currentColor" stroke-width="2"/><circle cx="12" cy="12" r="3" stroke="currentColor" stroke-width="2"/></svg>';
  const eyeClosed='<svg viewBox="0 0 24 24" fill="none"><path d="M17.94 17.94A10.07 10.07 0 0112 20c-7 0-11-8-11-8a18.45 18.45 0 015.06-5.94M9.9 4.24A9.12 9.12 0 0112 4c7 0 11 8 11 8a18.5 18.5 0 01-2.16 3.19m-6.72-1.07a3 3 0 01-4.24-4.24" stroke="currentColor" stroke-width="2" stroke-linecap="round"/><line x1="1" y1="1" x2="23" y2="23" stroke="currentColor" stroke-width="2"/></svg>';
  btn.addEventListener('click',()=>{const s=inp.type==='password';inp.type=s?'text':'password';btn.innerHTML=s?eyeClosed:eyeOpen;});
}
makePwdToggle('password','pwdToggle');
makePwdToggle('confirm','confirmToggle');

/* ── PASSWORD STRENGTH ── */
const pwdIn=document.getElementById('password');
const bars=[document.getElementById('sb1'),document.getElementById('sb2'),document.getElementById('sb3'),document.getElementById('sb4')];
const strengthLabel=document.getElementById('strengthLabel');
const COLORS=['','#ef4444','#f59e0b','#818cf8','#10b981'];
const LABELS=['','Weak','Fair','Good','Strong'];

function calcStrength(p){
  let score=0;
  if(p.length>=8)score++;
  if(p.length>=12)score++;
  if(/[A-Z]/.test(p)&&/[a-z]/.test(p))score++;
  if(/[0-9]/.test(p)&&/[^A-Za-z0-9]/.test(p))score++;
  return Math.min(score,4);
}

pwdIn.addEventListener('input',()=>{
  const v=pwdIn.value;
  if(!v){bars.forEach(b=>{b.style.background='rgba(255,255,255,0.06)'});strengthLabel.textContent='Enter a password';strengthLabel.style.color='var(--text-tertiary)';return;}
  const s=calcStrength(v);
  bars.forEach((b,i)=>{b.style.background=i<s?COLORS[s]:'rgba(255,255,255,0.06)';});
  strengthLabel.textContent=LABELS[s]||'';
  strengthLabel.style.color=COLORS[s];
});

/* ── FORM VALIDATION ── */
const form=document.getElementById('regForm');
const submitBtn=document.getElementById('submitBtn');
const alertErr=document.getElementById('alertErr');
const alertOk=document.getElementById('alertOk');
const alertMsg=document.getElementById('alertMsg');

function setErr(id,errId,msg){
  const el=document.getElementById(id),er=document.getElementById(errId);
  el.classList.add('err');er.textContent=msg;er.classList.add('show');
}
function clrErr(id,errId){
  const el=document.getElementById(id),er=document.getElementById(errId);
  el.classList.remove('err');er.classList.remove('show');
}

['username','password','confirm'].forEach(id=>{
  const errId=id+'Err';
  document.getElementById(id).addEventListener('input',()=>clrErr(id,errId));
});

form.addEventListener('submit', async e => {
  e.preventDefault();
  alertErr.classList.remove('show');
  alertOk.classList.remove('show');
  let ok = true;

  const username = document.getElementById('username').value.trim();
  const pw = document.getElementById('password').value;
  const cf = document.getElementById('confirm').value;
  const tc = document.getElementById('terms').checked;

  if (!username) { setErr('username', 'usernameErr', 'Username is required.'); ok = false; }
  if (pw.length < 8) { setErr('password', 'passErr', 'Password must be at least 8 characters.'); ok = false; }
  if (cf !== pw) { setErr('confirm', 'confirmErr', 'Passwords do not match.'); ok = false; }
  if (!tc) { document.getElementById('termsErr').classList.add('show'); ok = false; }
  else { document.getElementById('termsErr').classList.remove('show'); }

  if (!ok) return;

  submitBtn.textContent = 'Creating account...';
  submitBtn.disabled = true;

  try {
    const res = await fetch('/api/auth/register', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username: username, password: pw })
    });
    
    const data = await res.json();
    
    if (res.ok) {
      alertOk.classList.add('show');
      submitBtn.textContent = '✓ Account Created';
      submitBtn.style.background = '#10b981';
      setTimeout(() => window.location.href = '/dashboard', 1200);
    } else {
      alertMsg.textContent = data.message || 'Registration failed. Username may be taken.';
      alertErr.classList.add('show');
      submitBtn.textContent = 'Create Account';
      submitBtn.disabled = false;
    }
  } catch (err) {
    console.error("Registration error:", err);
    alertMsg.textContent = 'Network error. Please check your connection and try again.';
    alertErr.classList.add('show');
    submitBtn.textContent = 'Create Account';
    submitBtn.disabled = false;
  }
});