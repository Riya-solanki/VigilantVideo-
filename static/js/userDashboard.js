// STATE
let videos        = [];
let queue         = [];
let feed          = [];
let pollingTimers = {};   // job_id → setInterval id

// LOAD DASHBOARD FROM REAL DATABASE
async function loadDashboard() {
  try {
    const res  = await fetch('/api/dashboard');
    if (res.status === 401) { window.location.href = '/login'; return; }
    if (!res.ok) return;
    const data = await res.json();
    document.getElementById('authGate').classList.add('hidden');

    document.getElementById('statVideos').textContent  = data.stats.videos_protected;
    document.getElementById('statProc').textContent    = data.stats.processing_now;
    document.getElementById('statBlocked').textContent = data.stats.scrape_attempts_blocked;

    const usedB  = data.stats.storage_used_bytes;
    const limitB = data.stats.storage_limit_bytes;
    const pct    = limitB > 0 ? Math.min((usedB / limitB) * 100, 100).toFixed(1) : 0;
    document.getElementById('storageFill').style.width = pct + '%';
    document.getElementById('statStorage').textContent = fmtBytes(usedB);
    document.querySelector('.storage-nums').innerHTML  =
      `<strong>${fmtBytes(usedB)}</strong> of ${fmtBytes(limitB)} used`;

    //  Upload quota sub-text
    const sub = document.getElementById('statVideos').closest('.stat-card').querySelector('.sc-sub');
    if (sub) sub.textContent = `${data.stats.uploads_used} of ${data.stats.uploads_limit} this month`;

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

    document.getElementById('libTotal').textContent   = `${videos.length} total`;
    document.getElementById('libCount').textContent   = videos.length;
    document.getElementById('queueCount').textContent = queue.length;

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
  if (videos.length === 0) {
    tbody.innerHTML = `<tr><td colspan="5" style="text-align:center;padding:2rem;color:var(--muted)">No videos yet — upload one above</td></tr>`;
    document.getElementById('libTotal').textContent = '0 total';
    document.getElementById('libCount').textContent = 0;
    document.getElementById('statVideos').textContent = 0;
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
    } else {
      statusPill = `<span class="pill queued"><span class="pill-dot"></span>Queued</span>`;
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

  document.getElementById('libTotal').textContent   = `${videos.length} total`;
  document.getElementById('libCount').textContent   = videos.length;
  document.getElementById('statVideos').textContent = videos.filter(v => v.status === 'done').length;
}

function renderQueue() {
  const body = document.getElementById('queueBody');
  if (queue.length === 0) {
    body.innerHTML = `<div class="queue-empty">No jobs in queue — all clear ✓</div>`;
    document.getElementById('jobCount').textContent   = '0 jobs';
    document.getElementById('queueCount').textContent = '0';
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
  document.getElementById('jobCount').textContent   = `${queue.length} job${queue.length!==1?'s':''}`;
  document.getElementById('queueCount').textContent = queue.length;
}

function renderFeed() {
  const body = document.getElementById('feedBody');
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
  btn.textContent = 'PREPARING...';
  fill.style.width = '0%';

  // ── Step 1: Ask Render for a presigned upload token ──────────────
  let presignData;
  try {
    const presignRes = await fetch('/api/upload/presign', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        filename: selectedFile.name,
        filesize: selectedFile.size,
      }),
    });
    presignData = await presignRes.json();
    if (!presignRes.ok) {
      // Surface the backend message directly — covers "Plan not found",
      // "Monthly upload limit reached", file-size errors, etc.
      const errMsg = presignData.message || 'Could not start upload. Please try again.';
      if (presignRes.status === 429) {
        // Quota-exceeded — give a friendlier hint
        showUploadError('⚠ ' + errMsg + ' Upgrade your plan to upload more videos.');
      } else {
        showUploadError(errMsg);
      }
      _resetSubmitBtn(btn);
      fill.style.width = '0%';
      return;
    }
  } catch {
    showUploadError('Network error during upload setup. Please try again.');
    _resetSubmitBtn(btn);
    fill.style.width = '0%';
    return;
  }

  const { job_id, upload_url, fields } = presignData;

  // ── Step 2: Upload the file directly to Cloudflare R2 ───────────
  // Build a multipart/form-data body using the presigned POST fields.
  // The browser sends the video bytes directly to R2 — Render is not involved.
  btn.textContent = 'UPLOADING...';

  const formData = new FormData();
  // All presigned fields MUST come before the file key.
  Object.entries(fields).forEach(([k, v]) => formData.append(k, v));
  formData.append('file', selectedFile);   // 'file' is the standard S3 key for presigned POST

  await new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();

    xhr.upload.addEventListener('progress', e => {
      if (e.lengthComputable) {
        // Cap at 90% visually — the last 10% is the /confirm round-trip.
        const pct = Math.round((e.loaded / e.total) * 90);
        fill.style.width = pct + '%';
      }
    });

    xhr.addEventListener('load', () => {
      // R2 presigned POST returns 204 No Content on success.
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve();
      } else {
        reject(new Error(`R2 upload failed: HTTP ${xhr.status}\n${xhr.responseText}`));
      }
    });

    xhr.addEventListener('error', () => reject(new Error('Network error during upload to storage.')));
    xhr.addEventListener('abort', () => reject(new Error('Upload aborted.')));

    xhr.open('POST', upload_url);
    xhr.send(formData);
  }).catch(err => {
    showUploadError(err.message || 'Upload to storage failed. Please try again.');
    _resetSubmitBtn(btn);
    fill.style.width = '0%';
    throw err;   // exit the outer async function
  });

  // ── Step 3: Tell Render the file landed; queue the GPU job ───────
  btn.textContent  = 'QUEUING...';
  fill.style.width = '95%';

  try {
    const confirmRes = await fetch('/api/upload/confirm', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ job_id }),
    });
    const confirmData = await confirmRes.json();

    if (!confirmRes.ok) {
      showUploadError(confirmData.message || 'Upload complete but queuing failed. Please contact support.');
      _resetSubmitBtn(btn);
      fill.style.width = '0%';
      return;
    }

    // All done — show success state
    hideUploadError();
    fill.style.width = '100%';
    btn.textContent  = '✓ QUEUED';
    btn.style.background = 'linear-gradient(135deg,#22c55e,#16a34a)';

    if (confirmData.job_id) startPolling(confirmData.job_id);
    await loadDashboard();
    setTimeout(closeUploadModal, 1000);

  } catch {
    showUploadError('Network error while confirming upload. Please try again.');
    _resetSubmitBtn(btn);
    fill.style.width = '0%';
  }
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