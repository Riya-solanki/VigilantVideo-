// ══════════════════════════════════════
// NAVBAR SCROLL EFFECT
// ══════════════════════════════════════
const navbar = document.getElementById('navbar');
if (navbar) {

// ── Mobile hamburger toggle ────────────────────────────────────
const navToggle = document.getElementById('navToggle');
if (navToggle) {
  navToggle.addEventListener('click', () => {
    navbar.classList.toggle('open');
  });

  // Close menu when a nav link is clicked
  navbar.querySelectorAll('.nav-links a').forEach(link => {
    link.addEventListener('click', () => navbar.classList.remove('open'));
  });
}
  window.addEventListener('scroll', () => {
    navbar.classList.toggle('scrolled', window.scrollY > 30);
  });
}

// ══════════════════════════════════════
// SMOOTH SCROLL NAV
// ══════════════════════════════════════
document.querySelectorAll('a[href^="#"]').forEach(a => {
  a.addEventListener('click', e => {
    const t = document.querySelector(a.getAttribute('href'));
    if (t) {
      e.preventDefault();
      t.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
  });
});

// ══════════════════════════════════════
// UPLOAD LOGIC
// ══════════════════════════════════════
const fileInput      = document.getElementById('fileInput');
const dropZone       = document.getElementById('dropZone');
const chooseBtn      = document.getElementById('chooseBtn');
const fileMeta       = document.getElementById('fileMeta');
const fName          = document.getElementById('fName');
const fSize          = document.getElementById('fSize');
const progWrap       = document.getElementById('progWrap');
const progFill       = document.getElementById('progFill');
const progPct        = document.getElementById('progPct');
const uploadBtn      = document.getElementById('uploadBtn');
const uploadState    = document.getElementById('uploadState');
const processingState = document.getElementById('processingState');
const doneState      = document.getElementById('doneState');
const procStatus     = document.getElementById('procStatus');
const dlBtn          = document.getElementById('dlBtn');
const resetBtn       = document.getElementById('resetBtn');

let selectedFile = null, jobId = null, pollTimer = null, stageTimer = null, stageIdx = 0;

const STAGES = ['s1', 's2', 's3', 's4'];
const STAGE_MSGS = [
  'Injecting adversarial noise...',
  'Scrubbing metadata...',
  'Embedding fingerprint...',
  'Final encoding pass...'
];

function fmt(b) {
  if (b === 0) return '0 B';
  const k = 1024, s = ['B', 'KB', 'MB', 'GB'];
  const i = Math.floor(Math.log(b) / Math.log(k));
  return +(b / Math.pow(k, i)).toFixed(1) + ' ' + s[i];
}

function handleFile(f) {
  if (!f) return;
  const ext = f.name.split('.').pop().toLowerCase();
  if (!['mp4', 'avi', 'mov', 'mkv'].includes(ext)) {
    alert('Unsupported format. Please use MP4, AVI, MOV, or MKV.');
    return;
  }
  selectedFile = f;
  fName.textContent = f.name;
  fSize.textContent = fmt(f.size) + ' · ' + ext.toUpperCase();
  fileMeta.classList.add('show');
  progWrap.style.display = 'none';
  progFill.style.width = '0%';
  progPct.textContent = '0%';
  uploadBtn.disabled = false;
}

if (chooseBtn) {
  chooseBtn.addEventListener('click', e => { e.stopPropagation(); fileInput.click(); });
}
if (fileInput) {
  fileInput.addEventListener('change', () => { if (fileInput.files[0]) handleFile(fileInput.files[0]); });
}
if (dropZone) {
  dropZone.addEventListener('click', () => { if (!fileMeta.classList.contains('show')) fileInput.click(); });
  dropZone.addEventListener('dragover', e => { e.preventDefault(); dropZone.classList.add('drag-over'); });
  ['dragleave', 'dragend'].forEach(ev => dropZone.addEventListener(ev, () => dropZone.classList.remove('drag-over')));
  dropZone.addEventListener('drop', e => {
    e.preventDefault();
    dropZone.classList.remove('drag-over');
    if (e.dataTransfer.files[0]) handleFile(e.dataTransfer.files[0]);
  });
}

if (uploadBtn) {
  uploadBtn.addEventListener('click', () => {
    if (!selectedFile) return;
    const fd = new FormData();
    fd.append('video', selectedFile);
    const xhr = new XMLHttpRequest();
    progWrap.style.display = 'flex';
    uploadBtn.disabled = true;

    xhr.upload.addEventListener('progress', e => {
      if (e.lengthComputable) {
        const p = Math.round(e.loaded / e.total * 100);
        progFill.style.width = p + '%';
        progPct.textContent = p + '%';
      }
    });
    xhr.addEventListener('load', () => {
      try {
        const d = JSON.parse(xhr.responseText);
        jobId = d.job_id;
      } catch { jobId = 'demo-' + Date.now(); }
      startProcessing();
    });
    xhr.addEventListener('error', () => startProcessing()); // demo fallback
    xhr.open('POST', '/api/upload');
    xhr.send(fd);
  });
}

function showState(name) {
  if (uploadState) uploadState.style.display = 'none';
  if (processingState) processingState.style.display = 'none';
  if (doneState) doneState.style.display = 'none';
  if (name === 'upload' && uploadState)      uploadState.style.display = 'block';
  else if (name === 'processing' && processingState) processingState.style.display = 'flex';
  else if (name === 'done' && doneState)   doneState.style.display = 'flex';
}

function startProcessing() {
  stageIdx = 0;
  STAGES.forEach(id => {
    const el = document.getElementById(id);
    if (el) el.classList.remove('active', 'done');
  });
  showState('processing');
  advanceStage();
  pollTimer = setInterval(pollStatus, 2000);
}

function advanceStage() {
  if (stageIdx >= STAGES.length) return;
  const el = document.getElementById(STAGES[stageIdx]);
  if (el) el.classList.add('active');
  if (procStatus) procStatus.textContent = STAGE_MSGS[stageIdx] || 'Processing...';
  stageTimer = setTimeout(() => {
    stageIdx++;
    if (stageIdx < STAGES.length) {
      const prev = document.getElementById(STAGES[stageIdx - 1]);
      if (prev) { prev.classList.remove('active'); prev.classList.add('done'); }
      advanceStage();
    } else {
      STAGES.forEach(id => { const el = document.getElementById(id); if (el) el.classList.add('done'); });
    }
  }, 2800);
}

async function pollStatus() {
  if (!jobId) { simulateDone(); return; }
  try {
    const r = await fetch('/api/status/' + jobId);
    if (!r.ok) throw new Error();
    const d = await r.json();
    if (d.status === 'done')  { stopPolling(); finishAndShow(); }
    else if (d.status === 'error') { stopPolling(); resetApp(); }
  } catch {
    // demo: auto-complete handled by setTimeout below
  }
}

// Demo fallback: auto-complete after 12s if still processing
setTimeout(() => {
  if (processingState && processingState.style.display === 'flex') { stopPolling(); finishAndShow(); }
}, 12000);

function simulateDone() { stopPolling(); finishAndShow(); }
function stopPolling() { clearInterval(pollTimer); clearTimeout(stageTimer); }

function finishAndShow() {
  STAGES.forEach(id => {
    const el = document.getElementById(id);
    if (el) { el.classList.remove('active'); el.classList.add('done'); }
  });
  if (procStatus) procStatus.textContent = 'Protection complete!';
  if (dlBtn) dlBtn.href = jobId ? `/api/download/${jobId}` : '#';
  setTimeout(() => showState('done'), 600);
}

if (resetBtn) {
  resetBtn.addEventListener('click', resetApp);
}

function resetApp() {
  stopPolling();
  selectedFile = null; jobId = null; stageIdx = 0;
  if (fileInput) fileInput.value = '';
  if (fileMeta) fileMeta.classList.remove('show');
  if (progWrap) progWrap.style.display = 'none';
  if (progFill) progFill.style.width = '0%';
  if (progPct) progPct.textContent = '0%';
  if (uploadBtn) uploadBtn.disabled = true;
  STAGES.forEach(id => { const el = document.getElementById(id); if (el) el.classList.remove('active', 'done'); });
  showState('upload');
}

// ══════════════════════════════════════
// SCROLL REVEAL (subtle fade-in)
// ══════════════════════════════════════
const observer = new IntersectionObserver(entries => {
  entries.forEach(e => {
    if (e.isIntersecting) {
      e.target.style.opacity = '1';
      e.target.style.transform = 'translateY(0)';
    }
  });
}, { threshold: 0.1 });

document.querySelectorAll('.dash-card,.feat-row,.step-card,.prot-item').forEach(el => {
  el.style.opacity = '0';
  el.style.transform = 'translateY(16px)';
  el.style.transition = 'opacity 0.5s ease, transform 0.5s ease';
  observer.observe(el);
});

// Stagger children
document.querySelectorAll('.dash-grid,.steps-grid,.protection-list,.about-features').forEach(parent => {
  parent.querySelectorAll(':scope > *').forEach((child, i) => {
    child.style.transitionDelay = (i * 0.06) + 's';
  });
});