// ══════════════════════════════════════
// ANIMATED GRID CANVAS BG
// ══════════════════════════════════════
(function () {
  const c = document.getElementById('bgCanvas');
  const ctx = c.getContext('2d');
  let W, H, particles = [];

  function resize() {
    W = c.width = window.innerWidth;
    H = c.height = window.innerHeight;
  }
  resize();
  window.addEventListener('resize', resize);

  function drawGrid() {
    ctx.clearRect(0, 0, W, H);
    ctx.strokeStyle = 'rgba(0,229,255,0.04)';
    ctx.lineWidth = 1;
    const size = 60;
    for (let x = 0; x < W; x += size) {
      ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, H); ctx.stroke();
    }
    for (let y = 0; y < H; y += size) {
      ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(W, y); ctx.stroke();
    }
  }

  class Particle {
    constructor() { this.reset(); }
    reset() {
      this.x = Math.random() * W;
      this.y = Math.random() * H;
      this.size = Math.random() * 1.5 + 0.5;
      this.speedX = (Math.random() - 0.5) * 0.4;
      this.speedY = (Math.random() - 0.5) * 0.4;
      this.alpha = Math.random() * 0.5 + 0.1;
    }
    update() {
      this.x += this.speedX; this.y += this.speedY;
      if (this.x < 0 || this.x > W || this.y < 0 || this.y > H) this.reset();
    }
    draw() {
      ctx.beginPath();
      ctx.arc(this.x, this.y, this.size, 0, Math.PI * 2);
      ctx.fillStyle = `rgba(0,229,255,${this.alpha})`;
      ctx.fill();
    }
  }

  for (let i = 0; i < 80; i++) particles.push(new Particle());

  function drawConnections() {
    for (let i = 0; i < particles.length; i++) {
      for (let j = i + 1; j < particles.length; j++) {
        const dx = particles[i].x - particles[j].x;
        const dy = particles[i].y - particles[j].y;
        const d = Math.sqrt(dx * dx + dy * dy);
        if (d < 120) {
          ctx.strokeStyle = `rgba(0,229,255,${0.06 * (1 - d / 120)})`;
          ctx.lineWidth = 0.5;
          ctx.beginPath();
          ctx.moveTo(particles[i].x, particles[i].y);
          ctx.lineTo(particles[j].x, particles[j].y);
          ctx.stroke();
        }
      }
    }
  }

  function loop() {
    drawGrid();
    particles.forEach(p => { p.update(); p.draw(); });
    drawConnections();
    requestAnimationFrame(loop);
  }
  loop();
})();

// ══════════════════════════════════════
// CUSTOM CURSOR
// ══════════════════════════════════════
const cursor = document.getElementById('cursor');
const cursorRing = document.getElementById('cursor-ring');
let mx = 0, my = 0, rx = 0, ry = 0;

document.addEventListener('mousemove', e => {
  mx = e.clientX; my = e.clientY;
  cursor.style.left = mx + 'px';
  cursor.style.top = my + 'px';
});

function animateRing() {
  rx += (mx - rx) * 0.12;
  ry += (my - ry) * 0.12;
  cursorRing.style.left = rx + 'px';
  cursorRing.style.top = ry + 'px';
  requestAnimationFrame(animateRing);
}
animateRing();

document.querySelectorAll('a,button,.dash-card,.feat-row,.step-card').forEach(el => {
  el.addEventListener('mouseenter', () => {
    cursorRing.style.width = '52px';
    cursorRing.style.height = '52px';
    cursorRing.style.borderColor = 'rgba(0,229,255,0.8)';
  });
  el.addEventListener('mouseleave', () => {
    cursorRing.style.width = '36px';
    cursorRing.style.height = '36px';
    cursorRing.style.borderColor = 'rgba(0,229,255,0.5)';
  });
});

// ══════════════════════════════════════
// NAVBAR SCROLL
// ══════════════════════════════════════
const navbar = document.getElementById('navbar');
window.addEventListener('scroll', () => {
  navbar.style.background = window.scrollY > 50
    ? 'rgba(4,6,15,0.95)' : 'rgba(4,6,15,0.75)';
});

// ══════════════════════════════════════
// SMOOTH SCROLL NAV
// ══════════════════════════════════════
document.querySelectorAll('a[href^="#"]').forEach(a => {
  a.addEventListener('click', e => {
    const t = document.querySelector(a.getAttribute('href'));
    if (t) { e.preventDefault(); t.scrollIntoView({ behavior: 'smooth', block: 'start' }); }
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

chooseBtn.addEventListener('click', e => { e.stopPropagation(); fileInput.click(); });
fileInput.addEventListener('change', () => { if (fileInput.files[0]) handleFile(fileInput.files[0]); });
dropZone.addEventListener('click', () => { if (!fileMeta.classList.contains('show')) fileInput.click(); });
dropZone.addEventListener('dragover', e => { e.preventDefault(); dropZone.classList.add('drag-over'); });
['dragleave', 'dragend'].forEach(ev => dropZone.addEventListener(ev, () => dropZone.classList.remove('drag-over')));
dropZone.addEventListener('drop', e => {
  e.preventDefault();
  dropZone.classList.remove('drag-over');
  if (e.dataTransfer.files[0]) handleFile(e.dataTransfer.files[0]);
});

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

function showState(name) {
  uploadState.style.display = 'none';
  processingState.style.display = 'none';
  doneState.style.display = 'none';
  if (name === 'upload')      uploadState.style.display = 'block';
  else if (name === 'processing') processingState.style.display = 'flex';
  else if (name === 'done')   doneState.style.display = 'flex';
}

function startProcessing() {
  stageIdx = 0;
  STAGES.forEach(id => {
    const el = document.getElementById(id);
    el.classList.remove('active', 'done');
  });
  showState('processing');
  advanceStage();
  pollTimer = setInterval(pollStatus, 2000);
}

function advanceStage() {
  if (stageIdx >= STAGES.length) return;
  const el = document.getElementById(STAGES[stageIdx]);
  el.classList.add('active');
  procStatus.textContent = STAGE_MSGS[stageIdx] || 'Processing...';
  stageTimer = setTimeout(() => {
    stageIdx++;
    if (stageIdx < STAGES.length) {
      document.getElementById(STAGES[stageIdx - 1]).classList.remove('active');
      document.getElementById(STAGES[stageIdx - 1]).classList.add('done');
      advanceStage();
    } else {
      STAGES.forEach(id => document.getElementById(id).classList.add('done'));
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
  if (processingState.style.display === 'flex') { stopPolling(); finishAndShow(); }
}, 12000);

function simulateDone() { stopPolling(); finishAndShow(); }
function stopPolling() { clearInterval(pollTimer); clearTimeout(stageTimer); }

function finishAndShow() {
  STAGES.forEach(id => {
    const el = document.getElementById(id);
    el.classList.remove('active');
    el.classList.add('done');
  });
  procStatus.textContent = 'Protection complete!';
  dlBtn.href = jobId ? `/api/download/${jobId}` : '#';
  setTimeout(() => showState('done'), 600);
}

resetBtn.addEventListener('click', resetApp);
function resetApp() {
  stopPolling();
  selectedFile = null; jobId = null; stageIdx = 0;
  fileInput.value = '';
  fileMeta.classList.remove('show');
  progWrap.style.display = 'none';
  progFill.style.width = '0%';
  progPct.textContent = '0%';
  uploadBtn.disabled = true;
  STAGES.forEach(id => document.getElementById(id).classList.remove('active', 'done'));
  showState('upload');
}

// ══════════════════════════════════════
// SCROLL REVEAL
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
  el.style.transform = 'translateY(20px)';
  el.style.transition = 'opacity 0.6s cubic-bezier(0.23,1,0.32,1), transform 0.6s cubic-bezier(0.23,1,0.32,1)';
  observer.observe(el);
});

// Stagger children
document.querySelectorAll('.dash-grid,.steps-grid,.protection-list,.about-features').forEach(parent => {
  parent.querySelectorAll(':scope > *').forEach((child, i) => {
    child.style.transitionDelay = (i * 0.08) + 's';
  });
});