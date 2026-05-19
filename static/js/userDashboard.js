// STATE
let videos        = [];
let queue         = [];
let feed          = [];
let pollingTimers = {};   // job_id → setInterval id

// ── Null-safe DOM helpers ────────────────────────────────────────────
// Prevents "Cannot set properties of null" crashes when an expected
// element is absent from the DOM (e.g. during a partial page render).
function safeSet(id, value) {
  const el = document.getElementById(id);
  if (el) el.textContent = value;
}
function safeHTML(id, html) {
  const el = document.getElementById(id);
  if (el) el.innerHTML = html;
}
function safeStyle(id, prop, value) {
  const el = document.getElementById(id);
  if (el) el.style[prop] = value;
}

// LOAD DASHBOARD FROM REAL DATABASE
async function loadDashboard() {
  try {
    const res  = await fetch('/api/dashboard');
    if (res.status === 401) { window.location.href = '/login'; return; }
    if (!res.ok) return;
    const data = await res.json();
    document.getElementById('authGate').classList.add('hidden');

    safeSet('statVideos',  data.stats.videos_protected);
    safeSet('statProc',    data.stats.processing_now);
    safeSet('statBlocked', data.stats.scrape_attempts_blocked);

    const usedB  = data.stats.storage_used_bytes;
    const limitB = data.stats.storage_limit_bytes;
    const pct    = limitB > 0 ? Math.min((usedB / limitB) * 100, 100).toFixed(1) : 0;
    safeStyle('storageFill', 'width', pct + '%');
    safeSet('statStorage', fmtBytes(usedB));
    const storageNums = document.querySelector('.storage-nums');
    if (storageNums) storageNums.innerHTML = `<strong>${fmtBytes(usedB)}</strong> of ${fmtBytes(limitB)} used`;

    //  Upload quota sub-text
    const statVideosEl = document.getElementById('statVideos');
    if (statVideosEl) {
      const sub = statVideosEl.closest('.stat-card') && statVideosEl.closest('.stat-card').querySelector('.sc-sub');
      if (sub) sub.textContent = `${data.stats.uploads_used} of ${data.stats.uploads_limit} this month`;
    }

    // ── Plan quota pill in topbar ──────────────────────────────────
    const plan      = (data.user.plan_tier || 'free').toLowerCase();
    const used      = data.stats.uploads_used;
    const limit     = data.stats.uploads_limit;
    const limitInf  = limit === -1;
    const reached   = !limitInf && used >= limit;
    const pill      = document.getElementById('planPill');
    const pillBadge = document.getElementById('planBadge');
    const pillUsage = document.getElementById('planUsage');
    if (pill && pillBadge && pillUsage) {
      pill.className = 'plan-pill ' + plan + (reached ? ' limit-reached' : '');
      pillBadge.textContent = plan.toUpperCase();
      pillUsage.textContent = limitInf ? '∞ uploads' : `${used}/${limit} uploads`;
      // If limit just reached, auto-open upgrade modal
      if (reached) openUpgradeModal(false);
    }

    //  Video library
    videos = data.videos;
    feed   = data.feed;
    queue  = data.videos
      .filter(v => v.status === 'pending' || v.status === 'processing')
      .map(v => ({
        job_id: v.job_id,
        name:   v.name + (v.ext ? '.' + v.ext : ''),
        pct:    0,
        stage:  'Queued · starting soon',
        color:  'cyan'
      }));
    renderTable();
    renderQueue();
    renderFeed();

    safeSet('libTotal',    `${videos.length} total`);
    safeSet('libCount',    videos.length);
    safeSet('queueCount',  queue.length);

    // Start real polling for any active jobs
    queue.forEach(q => startPolling(q.job_id));

  } catch (err) {
    console.error('Dashboard load error:', err);
  }
}

function startPolling(job_id) {
  if (pollingTimers[job_id]) return; // already polling
  pollingTimers[job_id] = setInterval(async () => {
    try {
      const res  = await fetch(`/api/status/${job_id}`);
      if (!res.ok) { stopPolling(job_id); return; }
      const data = await res.json();
      const status   = data.status;
      const progress = data.progress || 0;

      const qItem = queue.find(q => q.job_id === job_id);
      if (qItem) {
        qItem.pct   = Math.round(progress);
        qItem.stage = 'Applying adversarial perturbations...';
        qItem.color = 'amber';
        renderQueue();
        renderTable();
      }

      if (status === 'done' || status === 'error') {
        stopPolling(job_id);
        await loadDashboard();
      }
    } catch { stopPolling(job_id); }
  }, 2000);
}

function stopPolling(job_id) {
  clearInterval(pollingTimers[job_id]);
  delete pollingTimers[job_id];
}


// HELPERS

function fmtBytes(bytes) {
  if (!bytes || bytes === 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  const i = Math.floor(Math.log(bytes) / Math.log(1024));
  return (bytes / Math.pow(1024, i)).toFixed(1) + ' ' + units[i];
}

function fmtSize(b) {
  if (!b) return '—';
  const k=1024, u=['B','KB','MB','GB'];
  const i=Math.floor(Math.log(b)/Math.log(k));
  return (b/Math.pow(k,i)).toFixed(1)+' '+u[i];
}

// RENDER FUNCTIONS
function renderTable() {
  const tbody = document.getElementById('videoTableBody');
  if (!tbody) return;  // guard: element not in DOM yet
  if (videos.length === 0) {
    tbody.innerHTML = `<tr><td colspan="5" style="text-align:center;padding:2rem;color:var(--muted)">No videos yet — upload one above</td></tr>`;
    safeSet('libTotal',   '0 total');
    safeSet('libCount',   0);
    safeSet('statVideos', 0);
    return;
  }
  tbody.innerHTML = videos.map(v => {
    let statusPill = '', actionCell = '';
    if (v.status === 'done') {
      statusPill = `<span class="pill protected"><span class="pill-dot"></span>Protected</span>`;
      actionCell = `<button class="act-btn" onclick="downloadVideo('${v.job_id}')">Download</button>
                    <button class="act-btn danger" onclick="deleteVideo('${v.job_id}',this)" style="margin-left:4px">Delete</button>`;
    } else if (v.status === 'expired') {
      statusPill = `<span class="pill expired"><span class="pill-dot"></span>Expired</span>`;
      actionCell = `<button class="act-btn danger" onclick="deleteVideo('${v.job_id}',this)">Delete</button>`;
    } else if (v.status === 'processing') {
      statusPill = `<span class="pill processing"><span class="pill-dot"></span>Processing</span>`;
      const qItem = queue.find(q => q.job_id === v.job_id);
      const currentPct = qItem ? qItem.pct : 0;
      actionCell = `<div class="progress-cell">
                      <div class="mini-prog">
                        <div class="mini-prog-fill" style="width:${currentPct}%"></div>
                      </div>
                      <span class="mini-pct">${currentPct}%...</span>
                    </div>`;
    } else if (v.status === 'error') {
      statusPill = `<span class="pill failed"><span class="pill-dot"></span>Failed</span>`;
      actionCell = `<button class="act-btn danger" onclick="deleteVideo('${v.job_id}',this)">Delete</button>`;
    // FIX Bug 7: Handle pending_presign status explicitly so a mid-upload
    // page refresh shows a clear "Uploading..." state instead of "Queued".
    } else if (v.status === 'pending_presign') {
      statusPill = `<span class="pill queued"><span class="pill-dot"></span>Uploading...</span>`;
      actionCell = `<span style="font-size:0.75rem;color:var(--muted)">Upload in progress…</span>`;
    } else {
      // covers 'pending' (queued for GPU worker)
      statusPill = `<span class="pill queued"><span class="pill-dot"></span>Processing</span>`;
      actionCell = `<span style="font-size:0.75rem;color:var(--muted)">Waiting...</span>`;
    }
    return `<tr>
      <td><div class="vid-name">${v.name}<span class="ext">${v.ext}</span></div></td>
      <td><span class="vid-date">${v.date}</span></td>
      <td><span class="vid-size">${v.size}</span></td>
      <td>${statusPill}</td>
      <td>${actionCell}</td>
    </tr>`;
  }).join('');

  safeSet('libTotal',    `${videos.length} total`);
  safeSet('libCount',    videos.length);
  safeSet('statVideos',  videos.filter(v => v.status === 'done').length);
}

function renderQueue() {
  const body = document.getElementById('queueBody');
  if (!body) return;  // guard: element not in DOM yet
  if (queue.length === 0) {
    body.innerHTML = `<div class="queue-empty">No jobs in queue — all clear ✓</div>`;
    safeSet('jobCount',   '0 jobs');
    safeSet('queueCount', '0');
    return;
  }
  body.innerHTML = queue.map(q => `
    <div class="q-item">
      <div class="q-item-top">
        <span class="q-name">${q.name}</span>
        <span class="q-pct ${q.color}">${q.pct}%</span>
      </div>
      <div class="q-bar-track"><div class="q-bar-fill ${q.color}" style="width:${q.pct}%"></div></div>
      <div class="q-stage">${q.stage}</div>
    </div>`).join('');
  safeSet('jobCount',   `${queue.length} job${queue.length!==1?'s':''}`);
  safeSet('queueCount', queue.length);
}

function renderFeed() {
  const body = document.getElementById('feedBody');
  if (!body) return;  // guard: feedBody not in DOM
  if (feed.length === 0) {
    body.innerHTML = `<div class="queue-empty">No activity yet</div>`;
    return;
  }
  body.innerHTML = feed.map(f => `
    <div class="feed-item">
      <div class="feed-dot ${f.dot}"></div>
      <div class="feed-content">
        <div class="feed-text">${f.text}</div>
        <div class="feed-time">${f.time}</div>
      </div>
    </div>`).join('');
}

async function downloadVideo(job_id) {
  try {
    const res  = await fetch(`/api/download/${job_id}`);
    const data = await res.json();
    if (!res.ok) { alert(data.message || 'Download failed.'); return; }
    const a = document.createElement('a');
    a.href = data.download_url;
    a.download = data.filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  } catch (err) {
    alert('Download error. Please try again.');
  }
}

async function deleteVideo(job_id, btn) {
  if (!confirm('Delete this video? This cannot be undone.')) return;
  try {
    btn.disabled = true;
    const res = await fetch(`/api/video/${job_id}`, { method: 'DELETE' });
    const data = await res.json();
    if (!res.ok) { alert(data.message || 'Delete failed.'); btn.disabled = false; return; }
    await loadDashboard();
  } catch {
    alert('Delete failed. Please try again.');
    btn.disabled = false;
  }
}

// ════════════════════════════════════════════════════════════════════
// UPLOAD MODAL — two-step presign + direct-to-R2 flow
//
// Step 1: POST /api/upload/presign  → get { job_id, upload_url, fields }
// Step 2: POST upload_url (R2)      → direct browser-to-R2 transfer
//         Uses XHR so we can show a real progress bar.
// Step 3: POST /api/upload/confirm  → tell Render the file landed; queue job
// ════════════════════════════════════════════════════════════════════
let selectedFile = null;

function openUploadModal()  { document.getElementById('uploadModal').classList.add('open'); resetUploadModal(); }
function closeUploadModal() { document.getElementById('uploadModal').classList.remove('open'); }
function resetUploadModal() {
  selectedFile = null;
  document.getElementById('fileInput').value = '';
  document.getElementById('mFileInfo').classList.remove('show');
  document.getElementById('mProgFill').style.width = '0%';
  document.getElementById('mSubmit').disabled = true;
  document.getElementById('mSubmit').textContent = 'UPLOAD & PROTECT';
  document.getElementById('mSubmit').style.background = '';
  hideUploadError();
}

function handleFileSelect(file) {
  if (!file) return;
  const ext = file.name.split('.').pop().toLowerCase();
  if (!['mp4','mov','avi','mkv'].includes(ext)) {
    alert('Unsupported format. Use MP4, MOV, AVI or MKV.');
    return;
  }
  selectedFile = file;
  document.getElementById('mFileName').textContent = file.name;
  document.getElementById('mFileSize').textContent = fmtSize(file.size);
  document.getElementById('mFileInfo').classList.add('show');
  document.getElementById('mSubmit').disabled = false;
}

document.getElementById('fileInput').addEventListener('change', e => {
  if (e.target.files[0]) handleFileSelect(e.target.files[0]);
});

const mDrop = document.getElementById('mDrop');
mDrop.addEventListener('dragover',  e => { e.preventDefault(); mDrop.classList.add('drag-over'); });
['dragleave','dragend'].forEach(ev => mDrop.addEventListener(ev, () => mDrop.classList.remove('drag-over')));
mDrop.addEventListener('drop', e => {
  e.preventDefault();
  mDrop.classList.remove('drag-over');
  if (e.dataTransfer.files[0]) handleFileSelect(e.dataTransfer.files[0]);
});

// ── Upload error banner (non-blocking, replaces raw alert() in the modal) ──
function showUploadError(msg) {
  let banner = document.getElementById('mErrorBanner');
  if (!banner) {
    banner = document.createElement('div');
    banner.id = 'mErrorBanner';
    banner.style.cssText = [
      'background:rgba(239,68,68,0.12)',
      'border:1px solid rgba(239,68,68,0.45)',
      'border-radius:8px',
      'color:#fca5a5',
      'font-size:0.82rem',
      'padding:0.6rem 0.85rem',
      'margin-top:0.75rem',
      'line-height:1.4',
      'display:none',
    ].join(';');
    // Insert it just before the submit button
    const btn = document.getElementById('mSubmit');
    btn.parentNode.insertBefore(banner, btn);
  }
  banner.textContent = msg;
  banner.style.display = 'block';
}

function hideUploadError() {
  const banner = document.getElementById('mErrorBanner');
  if (banner) banner.style.display = 'none';
}

function _resetSubmitBtn(btn) {
  btn.disabled    = false;
  btn.textContent = 'UPLOAD & PROTECT';
  btn.style.background = '';
}

async function startUpload() {
  if (!selectedFile) return;

  const btn  = document.getElementById('mSubmit');
  const fill = document.getElementById('mProgFill');
  hideUploadError();
  btn.disabled    = true;
  btn.textContent = 'UPLOADING...';
  fill.style.width = '0%';

  // ── Single-step: browser → Flask → R2 ────────────────────────────
  // We POST the video file directly to our Flask server as
  // multipart/form-data.  Flask proxies it to R2 via boto3 on the
  // server side, so no R2 CORS rules are needed.
  const formData = new FormData();
  formData.append('file', selectedFile);

  let uploadFailed = false;
  let responseData = null;

  await new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();

    // Real upload progress — Flask receives the bytes progressively
    xhr.upload.addEventListener('progress', e => {
      if (e.lengthComputable) {
        // Cap at 90% visually — last 10% is Flask→R2 server-side transfer
        const pct = Math.round((e.loaded / e.total) * 90);
        fill.style.width = pct + '%';
      }
    });

    xhr.addEventListener('load', () => {
      try { responseData = JSON.parse(xhr.responseText); } catch {}
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve();
      } else if (xhr.status === 429) {
        reject({ is429: true });
      } else {
        const msg = (responseData && responseData.message) || `Upload failed (HTTP ${xhr.status})`;
        reject(new Error(msg));
      }
    });

    xhr.addEventListener('error', () => reject(new Error('Network error during upload.')));
    xhr.addEventListener('abort', () => reject(new Error('Upload aborted.')));

    xhr.open('POST', '/api/upload/stream');
    xhr.send(formData);
  }).catch(err => {
    uploadFailed = true;
    if (err && err.is429) {
      // Quota exhausted — swap to upgrade modal
      closeUploadModal();
      openUpgradeModal(true);
    } else {
      showUploadError((err && err.message) || 'Upload failed. Please try again.');
      _resetSubmitBtn(btn);
      fill.style.width = '0%';
    }
  });

  if (uploadFailed) return;

  // ── Success ───────────────────────────────────────────────────────
  hideUploadError();
  fill.style.width = '100%';
  btn.textContent  = '✓ QUEUED';
  btn.style.background = 'linear-gradient(135deg,#22c55e,#16a34a)';

  if (responseData && responseData.job_id) startPolling(responseData.job_id);
  await loadDashboard();
  setTimeout(closeUploadModal, 1000);
}


// ════════════════════════════════════════════════════════════════════
// UPGRADE MODAL
// ════════════════════════════════════════════════════════════════════
let _plansCache = null;   // cache so we only fetch /api/plans once

async function openUpgradeModal(autoTriggered = false) {
  const modal = document.getElementById('upgradeModal');
  if (!modal) return;

  // Update header wording when triggered automatically vs clicked manually
  const title = modal.querySelector('.upgrade-title');
  const sub   = modal.querySelector('.upgrade-sub');
  if (autoTriggered && title && sub) {
    title.textContent = "YOU'VE HIT YOUR FREE LIMIT";
    sub.innerHTML = 'Your free plan allows <strong>3 videos per month</strong>. Upgrade to keep your content protected without limits.';
  } else if (title && sub) {
    title.textContent = 'UPGRADE YOUR PLAN';
    sub.innerHTML = 'Choose a plan that fits your workflow and protect more videos every month.';
  }

  modal.classList.add('open');
  await renderUpgradePlans();
}

function closeUpgradeModal() {
  const modal = document.getElementById('upgradeModal');
  if (modal) modal.classList.remove('open');
}

// Close on backdrop click
document.addEventListener('click', e => {
  const modal = document.getElementById('upgradeModal');
  if (modal && e.target === modal) closeUpgradeModal();
});

// Close on Escape
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') closeUpgradeModal();
});

function _fmtPlanLimit(val, unit) {
  if (val === -1) return 'Unlimited ' + unit;
  if (unit === 'storage') return val === 5368709120 ? '5 GB' : val === 1073741824 ? '1 GB' : Math.round(val / 1048576) + ' MB';
  if (unit === 'duration') {
    if (val >= 3600) return (val/3600) + ' hr max length';
    return Math.round(val/60) + ' min max length';
  }
  return val + ' ' + unit;
}

async function renderUpgradePlans() {
  const container = document.getElementById('upgradePlans');
  if (!container) return;

  if (!_plansCache) {
    try {
      const res = await fetch('/api/plans');
      if (!res.ok) throw new Error();
      const data = await res.json();
      // Exclude free plan from upgrade options
      _plansCache = (data.plans || []).filter(p => p.plan !== 'free');
    } catch {
      container.innerHTML = `<p style="color:var(--muted);font-size:0.85rem;grid-column:1/-1;text-align:center;">Could not load plan options. Please refresh.</p>`;
      return;
    }
  }

  const planMeta = {
    pro: {
      price: '$29',
      cta: 'Upgrade to Pro',
      recommended: true,
      extraFeatures: ['Priority GPU processing', 'Advanced adversarial protection', 'Freq-domain perturbation'],
    },
    business: {
      price: '$149',
      cta: 'Get Business',
      recommended: false,
      extraFeatures: ['Dedicated processing queue'],
    },
  };

  container.innerHTML = _plansCache.map(p => {
    const meta = planMeta[p.plan] || { price: 'Contact us', cta: 'Get Started', recommended: false, extraFeatures: [] };
    const uploadsText = p.max_videos_per_month === -1 ? 'Unlimited uploads/month' : `${p.max_videos_per_month} uploads per month`;
    const storageText = _fmtPlanLimit(p.max_file_size_bytes, 'storage') + ' per file';
    const durationText = _fmtPlanLimit(p.max_video_length_secs, 'duration');
    const features = [
      uploadsText,
      storageText,
      durationText,
      ...meta.extraFeatures,
    ];
    return `
      <div class="upgrade-plan ${p.plan}">
        ${meta.recommended ? '<div class="plan-recommended">⭐ Recommended</div>' : ''}
        <div class="plan-name">${p.plan.toUpperCase()}</div>
        <div class="plan-price">
          <span class="plan-price-amount">${meta.price}</span>
          <span class="plan-price-period">/month</span>
        </div>
        <ul class="plan-features">
          ${features.map(f => `<li>${f}</li>`).join('')}
        </ul>
        <button class="plan-cta" onclick="handlePlanCTA('${p.plan}')">${meta.cta} →</button>
      </div>`;
  }).join('');
}

// ════════════════════════════════════════════════════════════════════
// RAZORPAY CHECKOUT (dashboard upgrade modal)
// ════════════════════════════════════════════════════════════════════
async function handlePlanCTA(plan) {
  // Disable the button while we talk to the server
  const btns = document.querySelectorAll(`.plan-cta`);
  btns.forEach(b => b.disabled = true);

  try {
    // ── Step 1: Create Razorpay order server-side ────────────────────
    const orderRes = await fetch('/api/payment/create-order', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ plan }),
    });
    const orderData = await orderRes.json();

    if (!orderRes.ok) {
      alert(orderData.message || 'Could not initiate payment. Please try again.');
      btns.forEach(b => b.disabled = false);
      return;
    }

    // ── Step 2: Open Razorpay checkout popup ──────────────────────
    const options = {
      key:         orderData.key_id,
      amount:      orderData.amount,
      currency:    orderData.currency,
      name:        'VigilantVideo',
      description: orderData.label,
      order_id:    orderData.order_id,
      theme:       { color: '#00e5ff' },
      prefill:     {},
      handler: async function (response) {
        // ── Step 3: Verify signature & upgrade plan in DB ─────────
        try {
          const verifyRes = await fetch('/api/payment/verify', {
            method:  'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              razorpay_order_id:   response.razorpay_order_id,
              razorpay_payment_id: response.razorpay_payment_id,
              razorpay_signature:  response.razorpay_signature,
              plan,
            }),
          });
          const verifyData = await verifyRes.json();

          if (verifyRes.ok) {
            closeUpgradeModal();
            showDashboardPaymentSuccess(plan);
          } else {
            alert('Verification failed: ' + (verifyData.message || 'Unknown error'));
            btns.forEach(b => b.disabled = false);
          }
        } catch (e) {
          alert('Verification error. Please contact support.');
          btns.forEach(b => b.disabled = false);
        }
      },
      modal: {
        ondismiss: function () {
          btns.forEach(b => b.disabled = false);
        }
      }
    };

    const rzp = new Razorpay(options);
    rzp.open();

  } catch (err) {
    console.error('Razorpay error:', err);
    alert('Payment gateway error. Please try again.');
    btns.forEach(b => b.disabled = false);
  }
}

// Shown after a successful payment inside the dashboard
function showDashboardPaymentSuccess(plan) {
  const overlay = document.createElement('div');
  overlay.style.cssText = [
    'position:fixed', 'inset:0', 'z-index:9999',
    'background:rgba(4,6,15,0.97)',
    'display:flex', 'flex-direction:column',
    'align-items:center', 'justify-content:center',
    'font-family:Figtree,sans-serif', 'text-align:center', 'gap:1.25rem',
  ].join(';');
  const planCap = plan.charAt(0).toUpperCase() + plan.slice(1);
  overlay.innerHTML = `
    <div style="font-size:3.5rem">🎉</div>
    <div style="font-size:1.5rem;font-weight:800;color:var(--cyan,#00e5ff);letter-spacing:0.04em">
      YOU'RE NOW ON ${plan.toUpperCase()}
    </div>
    <p style="color:#7a8a9a;font-size:0.9rem;max-width:340px;line-height:1.6">
      Your ${planCap} plan is active. Reloading your dashboard with the upgraded limits…
    </p>
    <div style="width:220px;height:3px;background:rgba(255,255,255,0.06);border-radius:99px;overflow:hidden">
      <div id="rzpSuccessBar" style="width:0%;height:100%;background:linear-gradient(90deg,#00e5ff,#6366f1);border-radius:99px;transition:width 2.5s linear"></div>
    </div>`;
  document.body.appendChild(overlay);
  requestAnimationFrame(() => {
    const bar = document.getElementById('rzpSuccessBar');
    if (bar) bar.style.width = '100%';
  });
  // Reload the dashboard data so plan badge + limits update instantly
  setTimeout(async () => {
    _plansCache = null;  // force upgrade modal to re-fetch fresh plan data next time
    await loadDashboard();
    document.body.removeChild(overlay);
  }, 2700);
}

function openLogoutModal()  { document.getElementById('logoutModal').classList.add('open'); }
function closeLogoutModal() { document.getElementById('logoutModal').classList.remove('open'); }
async function doLogout() {
  try { await fetch('/api/logout', { method: 'POST' }); } catch {}
  window.location.href = '/login';
}

(function(){
  const c=document.getElementById('bgCanvas'),ctx=c.getContext('2d');
  let W,H,pts=[];
  function resize(){W=c.width=window.innerWidth;H=c.height=window.innerHeight;}
  resize();window.addEventListener('resize',resize);
  class P{constructor(){this.r()}r(){this.x=Math.random()*W;this.y=Math.random()*H;this.s=Math.random()*1.2+0.3;this.vx=(Math.random()-.5)*.25;this.vy=(Math.random()-.5)*.25;this.a=Math.random()*.35+0.06;}u(){this.x+=this.vx;this.y+=this.vy;if(this.x<0||this.x>W||this.y<0||this.y>H)this.r();}d(){ctx.beginPath();ctx.arc(this.x,this.y,this.s,0,Math.PI*2);ctx.fillStyle=`rgba(0,229,255,${this.a})`;ctx.fill();}}
  for(let i=0;i<50;i++)pts.push(new P());
  function loop(){ctx.clearRect(0,0,W,H);ctx.strokeStyle='rgba(0,229,255,0.025)';ctx.lineWidth=1;const sz=70;for(let x=0;x<W;x+=sz){ctx.beginPath();ctx.moveTo(x,0);ctx.lineTo(x,H);ctx.stroke();}for(let y=0;y<H;y+=sz){ctx.beginPath();ctx.moveTo(0,y);ctx.lineTo(W,y);ctx.stroke();}pts.forEach(p=>{p.u();p.d();});requestAnimationFrame(loop);}
  loop();
})();


window.addEventListener('DOMContentLoaded', loadDashboard);