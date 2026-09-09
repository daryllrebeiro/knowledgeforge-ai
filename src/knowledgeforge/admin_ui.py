"""Admin Console UI: Single-page application for Platform Admins.

Provides:
1. Tenants & Billing overview with tier adjustments.
2. DLQ Inspector for ingestion & extraction dead letters.
3. Failed Ingestion Triage.
4. Human-in-the-loop Extraction Review & Correction queue.
"""

_ADMIN_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>KnowledgeForge AI — Platform Admin Console</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
  <style>
    :root {
      --bg-base: #080d1a;
      --bg-surface: #0f172a;
      --bg-card: rgba(30, 41, 59, 0.7);
      --bg-card-hover: rgba(51, 65, 85, 0.8);
      --border-subtle: rgba(255, 255, 255, 0.08);
      --border-accent: rgba(99, 102, 241, 0.3);
      --text-main: #f8fafc;
      --text-muted: #94a3b8;
      --primary: #6366f1;
      --primary-hover: #4f46e5;
      --primary-glow: rgba(99, 102, 241, 0.25);
      --success: #10b981;
      --warning: #f59e0b;
      --danger: #ef4444;
      --info: #38bdf8;
      --radius: 12px;
      --transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
    }

    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
      background: radial-gradient(circle at 10% 20%, #111827 0%, var(--bg-base) 100%);
      color: var(--text-main);
      min-height: 100vh;
      display: flex;
      flex-direction: column;
      overflow-x: hidden;
    }

    /* Header */
    header {
      background: rgba(15, 23, 42, 0.8);
      backdrop-filter: blur(16px);
      border-bottom: 1px solid var(--border-subtle);
      padding: 1rem 2rem;
      display: flex;
      justify-content: space-between;
      align-items: center;
      position: sticky;
      top: 0;
      z-index: 100;
    }
    .brand {
      display: flex;
      align-items: center;
      gap: 0.75rem;
      font-weight: 700;
      font-size: 1.15rem;
      letter-spacing: -0.02em;
    }
    .brand-badge {
      background: linear-gradient(135deg, var(--primary), #a855f7);
      color: white;
      padding: 0.2rem 0.6rem;
      border-radius: 6px;
      font-size: 0.75rem;
      font-weight: 600;
      text-transform: uppercase;
      box-shadow: 0 0 15px var(--primary-glow);
    }
    .auth-bar {
      display: flex;
      align-items: center;
      gap: 1rem;
    }
    .api-token-input {
      background: rgba(15, 23, 42, 0.9);
      border: 1px solid var(--border-subtle);
      color: var(--text-main);
      padding: 0.45rem 0.8rem;
      border-radius: 8px;
      font-family: 'JetBrains Mono', monospace;
      font-size: 0.85rem;
      width: 260px;
      outline: none;
      transition: var(--transition);
    }
    .api-token-input:focus {
      border-color: var(--primary);
      box-shadow: 0 0 0 2px var(--primary-glow);
    }

    /* Layout */
    .app-container {
      display: flex;
      flex: 1;
    }
    nav.sidebar {
      width: 250px;
      background: rgba(15, 23, 42, 0.5);
      border-right: 1px solid var(--border-subtle);
      padding: 1.5rem 1rem;
      display: flex;
      flex-direction: column;
      gap: 0.5rem;
    }
    .nav-btn {
      display: flex;
      align-items: center;
      gap: 0.75rem;
      padding: 0.75rem 1rem;
      border-radius: 8px;
      border: none;
      background: transparent;
      color: var(--text-muted);
      font-size: 0.9rem;
      font-weight: 500;
      cursor: pointer;
      text-align: left;
      transition: var(--transition);
      width: 100%;
    }
    .nav-btn:hover {
      background: rgba(255, 255, 255, 0.04);
      color: var(--text-main);
    }
    .nav-btn.active {
      background: rgba(99, 102, 241, 0.15);
      color: #818cf8;
      border-left: 3px solid var(--primary);
      font-weight: 600;
    }
    .nav-badge {
      margin-left: auto;
      background: rgba(239, 68, 68, 0.2);
      color: #f87171;
      padding: 0.15rem 0.45rem;
      border-radius: 10px;
      font-size: 0.75rem;
      font-weight: 600;
    }

    main.content {
      flex: 1;
      padding: 2rem;
      overflow-y: auto;
    }

    /* Cards & Stats */
    .view-title {
      font-size: 1.5rem;
      font-weight: 700;
      margin-bottom: 0.5rem;
      letter-spacing: -0.02em;
    }
    .view-subtitle {
      color: var(--text-muted);
      font-size: 0.9rem;
      margin-bottom: 1.5rem;
    }
    .stat-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
      gap: 1rem;
      margin-bottom: 2rem;
    }
    .stat-card {
      background: var(--bg-card);
      backdrop-filter: blur(12px);
      border: 1px solid var(--border-subtle);
      border-radius: var(--radius);
      padding: 1.25rem;
      transition: var(--transition);
    }
    .stat-card:hover {
      border-color: var(--border-accent);
      transform: translateY(-2px);
      box-shadow: 0 8px 24px rgba(0, 0, 0, 0.2);
    }
    .stat-label {
      font-size: 0.8rem;
      color: var(--text-muted);
      text-transform: uppercase;
      letter-spacing: 0.05em;
      margin-bottom: 0.4rem;
    }
    .stat-value {
      font-size: 1.6rem;
      font-weight: 700;
    }

    /* Tables */
    .data-table-container {
      background: var(--bg-card);
      backdrop-filter: blur(12px);
      border: 1px solid var(--border-subtle);
      border-radius: var(--radius);
      overflow: hidden;
      box-shadow: 0 4px 20px rgba(0, 0, 0, 0.25);
    }
    table {
      width: 100%;
      border-collapse: collapse;
      text-align: left;
      font-size: 0.88rem;
    }
    th {
      background: rgba(15, 23, 42, 0.7);
      padding: 0.85rem 1rem;
      color: var(--text-muted);
      font-weight: 600;
      border-bottom: 1px solid var(--border-subtle);
      text-transform: uppercase;
      font-size: 0.75rem;
      letter-spacing: 0.05em;
    }
    td {
      padding: 1rem;
      border-bottom: 1px solid rgba(255, 255, 255, 0.03);
    }
    tr:hover td {
      background: rgba(255, 255, 255, 0.02);
    }

    /* Badges & Tags */
    .tag {
      display: inline-block;
      padding: 0.2rem 0.55rem;
      border-radius: 6px;
      font-size: 0.75rem;
      font-weight: 600;
      text-transform: uppercase;
    }
    .tag-free { background: rgba(148, 163, 184, 0.15); color: #cbd5e1; }
    .tag-pro { background: rgba(99, 102, 241, 0.2); color: #a5b4fc; border: 1px solid rgba(99, 102, 241, 0.4); }
    .tag-enterprise { background: rgba(168, 85, 247, 0.2); color: #d8b4fe; border: 1px solid rgba(168, 85, 247, 0.4); }
    .tag-active { background: rgba(16, 185, 129, 0.15); color: #6ee7b7; }
    .tag-past_due { background: rgba(245, 158, 11, 0.2); color: #fcd34d; }
    .tag-canceled { background: rgba(239, 68, 68, 0.15); color: #fca5a5; }

    /* Buttons */
    .btn {
      padding: 0.45rem 0.9rem;
      border-radius: 6px;
      border: 1px solid transparent;
      font-size: 0.82rem;
      font-weight: 600;
      cursor: pointer;
      transition: var(--transition);
      display: inline-flex;
      align-items: center;
      gap: 0.4rem;
    }
    .btn-primary {
      background: var(--primary);
      color: white;
    }
    .btn-primary:hover { background: var(--primary-hover); }
    .btn-secondary {
      background: rgba(255, 255, 255, 0.06);
      color: var(--text-main);
      border-color: var(--border-subtle);
    }
    .btn-secondary:hover { background: rgba(255, 255, 255, 0.1); }
    .btn-danger {
      background: rgba(239, 68, 68, 0.15);
      color: #fca5a5;
      border-color: rgba(239, 68, 68, 0.3);
    }
    .btn-danger:hover { background: rgba(239, 68, 68, 0.3); }

    /* Modals */
    .modal-overlay {
      position: fixed;
      inset: 0;
      background: rgba(0, 0, 0, 0.7);
      backdrop-filter: blur(8px);
      display: none;
      justify-content: center;
      align-items: center;
      z-index: 200;
    }
    .modal-overlay.active { display: flex; }
    .modal-card {
      background: var(--bg-surface);
      border: 1px solid var(--border-accent);
      border-radius: var(--radius);
      width: 90%;
      max-width: 600px;
      max-height: 85vh;
      overflow-y: auto;
      padding: 1.75rem;
      box-shadow: 0 20px 50px rgba(0, 0, 0, 0.5);
    }
    .modal-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 1.25rem;
    }
    .form-group {
      margin-bottom: 1.1rem;
    }
    .form-label {
      display: block;
      font-size: 0.8rem;
      color: var(--text-muted);
      margin-bottom: 0.35rem;
      font-weight: 500;
    }
    .form-input {
      width: 100%;
      background: rgba(15, 23, 42, 0.8);
      border: 1px solid var(--border-subtle);
      border-radius: 6px;
      padding: 0.55rem 0.75rem;
      color: var(--text-main);
      font-family: inherit;
      font-size: 0.88rem;
    }
    .form-input:focus {
      border-color: var(--primary);
      outline: none;
    }

    /* Confidence bar */
    .conf-bar-container {
      width: 100px;
      height: 6px;
      background: rgba(255, 255, 255, 0.1);
      border-radius: 3px;
      overflow: hidden;
      display: inline-block;
      vertical-align: middle;
      margin-right: 6px;
    }
    .conf-bar-fill {
      height: 100%;
      border-radius: 3px;
    }
    .conf-high { background: var(--success); }
    .conf-med { background: var(--warning); }
    .conf-low { background: var(--danger); }

    /* Alerts */
    .toast {
      position: fixed;
      bottom: 2rem;
      right: 2rem;
      background: #1e293b;
      border: 1px solid var(--border-accent);
      color: white;
      padding: 0.8rem 1.2rem;
      border-radius: 8px;
      box-shadow: 0 10px 30px rgba(0,0,0,0.4);
      display: none;
      z-index: 300;
      animation: fadeIn 0.3s ease;
    }
    @keyframes fadeIn { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: translateY(0); } }
  </style>
</head>
<body>

  <header>
    <div class="brand">
      <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <polygon points="12 2 2 7 12 12 22 7 12 2"></polygon>
        <polyline points="2 17 12 22 22 17"></polyline>
        <polyline points="2 12 12 17 22 12"></polyline>
      </svg>
      KnowledgeForge AI
      <span class="brand-badge">Platform Admin</span>
    </div>
    <div class="auth-bar">
      <input type="password" id="bearer-token" class="api-token-input" placeholder="Admin Bearer Token (optional if cookie)">
      <button class="btn btn-secondary" id="btn-refresh">Refresh</button>
    </div>
  </header>

  <div class="app-container">
    <nav class="sidebar">
      <button class="nav-btn active" data-view="tenants" id="btn-tenants">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"></path><circle cx="9" cy="7" r="4"></circle><path d="M23 21v-2a4 4 0 0 0-3-3.87"></path><path d="M16 3.13a4 4 0 0 1 0 7.75"></path></svg>
        Tenants & Billing
      </button>
      <button class="nav-btn" data-view="extractions" id="btn-extractions">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><polyline points="14 2 14 8 20 8"></polyline><line x1="16" y1="13" x2="8" y2="13"></line><line x1="16" y1="17" x2="8" y2="17"></line><polyline points="10 9 9 9 8 9"></polyline></svg>
        Review Queue
        <span class="nav-badge" id="review-badge" style="display:none">0</span>
      </button>
      <button class="nav-btn" data-view="dlq" id="btn-dlq">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"></polyline></svg>
        DLQ Inspector
      </button>
      <button class="nav-btn" data-view="ingestions" id="btn-ingestions">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"></circle><line x1="12" y1="8" x2="12" y2="12"></line><line x1="12" y1="16" x2="12.01" y2="16"></line></svg>
        Failed Ingestions
      </button>
    </nav>

    <main class="content">
      <!-- View: Tenants & Billing -->
      <div id="view-tenants" class="view-panel">
        <h2 class="view-title">Tenant & Subscription Management</h2>
        <p class="view-subtitle">Inspect tenant subscription tiers, budget limits, daily token spend, and Stripe statuses.</p>
        <div class="data-table-container">
          <table id="tenants-table">
            <thead>
              <tr>
                <th>Tenant</th>
                <th>Tier</th>
                <th>Status</th>
                <th>Token Usage / Limit</th>
                <th>Extraction Usage</th>
                <th>Email Verified</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody id="tenants-tbody">
              <tr><td colspan="7" style="text-align:center; color: var(--text-muted); padding: 2rem;">Loading tenants...</td></tr>
            </tbody>
          </table>
        </div>
      </div>

      <!-- View: Extraction Review Queue -->
      <div id="view-extractions" class="view-panel" style="display:none;">
        <h2 class="view-title">Human-in-the-Loop Extraction Review</h2>
        <p class="view-subtitle">Review extractions with confidence scores below threshold (<code>needs_review = true</code>). Correcting fields clears review and logs audit history.</p>
        <div class="data-table-container">
          <table id="extractions-table">
            <thead>
              <tr>
                <th>Document</th>
                <th>Schema</th>
                <th>Overall Conf</th>
                <th>Fields Preview</th>
                <th>Extracted At</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody id="extractions-tbody">
              <tr><td colspan="6" style="text-align:center; color: var(--text-muted); padding: 2rem;">Loading review queue...</td></tr>
            </tbody>
          </table>
        </div>
      </div>

      <!-- View: DLQ Inspector -->
      <div id="view-dlq" class="view-panel" style="display:none;">
        <h2 class="view-title">Dead-Letter Queue (DLQ) Inspector</h2>
        <p class="view-subtitle">Real-time depth and permanent processing failures from ingestion and extraction topics.</p>
        <div class="stat-grid">
          <div class="stat-card">
            <div class="stat-label">Ingestion DLQ Depth</div>
            <div class="stat-value" id="dlq-ingest-depth" style="color: #f87171;">0</div>
          </div>
          <div class="stat-card">
            <div class="stat-label">Extraction DLQ Depth</div>
            <div class="stat-value" id="dlq-extract-depth" style="color: #f87171;">0</div>
          </div>
        </div>
        <h3 style="font-size: 1.1rem; margin-bottom: 0.75rem;">Recent Failed Extractions</h3>
        <div class="data-table-container" style="margin-bottom: 2rem;">
          <table>
            <thead>
              <tr>
                <th>Tenant</th>
                <th>Document</th>
                <th>Schema</th>
                <th>Error</th>
                <th>Failed At</th>
              </tr>
            </thead>
            <tbody id="dlq-extract-tbody">
              <tr><td colspan="5" style="text-align:center; color: var(--text-muted); padding: 1.5rem;">No failed extractions.</td></tr>
            </tbody>
          </table>
        </div>
      </div>

      <!-- View: Failed Ingestions -->
      <div id="view-ingestions" class="view-panel" style="display:none;">
        <h2 class="view-title">Failed Ingestions Triage</h2>
        <p class="view-subtitle">All permanent document parsing and ingestion failures.</p>
        <div class="data-table-container">
          <table>
            <thead>
              <tr>
                <th>Tenant</th>
                <th>Filename</th>
                <th>Error Reason</th>
              </tr>
            </thead>
            <tbody id="ingestions-tbody">
              <tr><td colspan="3" style="text-align:center; color: var(--text-muted); padding: 2rem;">Loading failed ingestions...</td></tr>
            </tbody>
          </table>
        </div>
      </div>
    </main>
  </div>

  <!-- Modal: Tier Adjustment -->
  <div class="modal-overlay" id="tier-modal">
    <div class="modal-card">
      <div class="modal-header">
        <h3 style="font-weight: 600;">Adjust Tenant Subscription Tier</h3>
        <button class="btn btn-secondary" data-close-modal="tier-modal">&times;</button>
      </div>
      <input type="hidden" id="modal-tenant-id">
      <div class="form-group">
        <label class="form-label">Subscription Tier</label>
        <select id="modal-tier-select" class="form-input">
          <option value="free">Free Tier (10K tokens, 5 extractions)</option>
          <option value="pro">Pro Tier (1M tokens, 1,000 extractions)</option>
          <option value="enterprise">Enterprise Tier (10M tokens, 10,000 extractions)</option>
        </select>
      </div>
      <div class="form-group">
        <label class="form-label">Subscription Status</label>
        <select id="modal-status-select" class="form-input">
          <option value="active">Active</option>
          <option value="past_due">Past Due (Grace Period)</option>
          <option value="canceled">Canceled</option>
        </select>
      </div>
      <div style="display:flex; justify-content:flex-end; gap:0.5rem; margin-top:1.5rem;">
        <button class="btn btn-secondary" data-close-modal="tier-modal">Cancel</button>
        <button class="btn btn-primary" id="btn-save-tier">Save Changes</button>
      </div>
    </div>
  </div>

  <!-- Modal: Human Correction Form -->
  <div class="modal-overlay" id="correction-modal">
    <div class="modal-card">
      <div class="modal-header">
        <h3 style="font-weight: 600;">Review & Correct Extraction</h3>
        <button class="btn btn-secondary" data-close-modal="correction-modal">&times;</button>
      </div>
      <p style="font-size:0.85rem; color:var(--text-muted); margin-bottom:1.25rem;">
        Edit field values below. Corrections will update the active extraction, clear the review flag, and save the original model output to the audit history.
      </p>
      <input type="hidden" id="modal-correction-doc-id">
      <div id="correction-fields-container"></div>
      <div style="display:flex; justify-content:flex-end; gap:0.5rem; margin-top:1.5rem;">
        <button class="btn btn-secondary" data-close-modal="correction-modal">Cancel</button>
        <button class="btn btn-primary" id="btn-submit-correction">Submit Human Correction</button>
      </div>
    </div>
  </div>

  <div class="toast" id="toast"></div>

  <script>
    let currentView = 'tenants';
    let extractionsCache = {};

    function getAuthHeaders() {
      const token = document.getElementById('bearer-token').value.trim() || localStorage.getItem('kf_admin_token');
      const headers = { 'Content-Type': 'application/json' };
      if (token) {
        headers['Authorization'] = 'Bearer ' + token;
      }
      return headers;
    }

    function saveToken() {
      const val = document.getElementById('bearer-token').value.trim();
      if (val) {
        localStorage.setItem('kf_admin_token', val);
        showToast("Token saved");
        loadActiveView();
      }
    }

    function showToast(msg) {
      const t = document.getElementById('toast');
      t.textContent = msg;
      t.style.display = 'block';
      setTimeout(() => { t.style.display = 'none'; }, 3000);
    }

    function switchView(view) {
      currentView = view;
      document.querySelectorAll('.view-panel').forEach(p => p.style.display = 'none');
      document.querySelectorAll('.nav-btn').forEach(b => b.classList.remove('active'));
      document.getElementById('view-' + view).style.display = 'block';
      document.getElementById('btn-' + view).classList.add('active');
      loadActiveView();
    }

    function loadActiveView() {
      if (currentView === 'tenants') loadTenants();
      else if (currentView === 'extractions') loadExtractions();
      else if (currentView === 'dlq') loadDLQ();
      else if (currentView === 'ingestions') loadFailedIngestions();
    }

    async function loadTenants() {
      const tbody = document.getElementById('tenants-tbody');
      try {
        const res = await fetch('/admin/billing/tenants', { headers: getAuthHeaders() });
        if (!res.ok) throw new Error("HTTP " + res.status);
        const data = await res.json();
        if (!data || data.length === 0) {
          tbody.innerHTML = '<tr><td colspan="7" style="text-align:center; padding:2rem;">No tenants found.</td></tr>';
          return;
        }
        tbody.innerHTML = data.map(t => `
          <tr>
            <td><strong>${escapeHtml(t.tenant_name)}</strong><br><small style="color:var(--text-muted)">${t.tenant_id}</small></td>
            <td><span class="tag tag-${t.tier}">${t.tier}</span></td>
            <td><span class="tag tag-${t.subscription_status}">${t.subscription_status}</span></td>
            <td>${t.token_usage.toLocaleString()} / ${t.token_limit.toLocaleString()}</td>
            <td>${t.extraction_usage} / ${t.extraction_limit}</td>
            <td>${t.is_email_verified ? '<span style="color:var(--success)">✓ Verified</span>' : '<span style="color:var(--warning)">Pending</span>'}</td>
            <td><button class="btn btn-secondary btn-edit-tier" data-tenant-id="${t.tenant_id}" data-tier="${t.tier}" data-status="${t.subscription_status}">Edit Tier</button></td>
          </tr>
        `).join('');
      } catch (e) {
        tbody.innerHTML = `<tr><td colspan="7" style="color:var(--danger); text-align:center; padding:2rem;">Error loading tenants: ${e.message}. Ensure Admin Console is enabled and token is valid.</td></tr>`;
      }
    }

    async function loadExtractions() {
      const tbody = document.getElementById('extractions-tbody');
      try {
        const res = await fetch('/admin/extractions/review-queue?all_tenants=true', { headers: getAuthHeaders() });
        if (!res.ok) throw new Error("HTTP " + res.status);
        const data = await res.json();
        const items = data.extractions || [];
        extractionsCache = {};
        items.forEach(i => { extractionsCache[i.document_id] = i; });

        const badge = document.getElementById('review-badge');
        if (items.length > 0) {
          badge.textContent = items.length;
          badge.style.display = 'inline';
        } else {
          badge.style.display = 'none';
        }

        if (items.length === 0) {
          tbody.innerHTML = '<tr><td colspan="6" style="text-align:center; padding:2rem; color:var(--success);">Review queue is clean! All extractions meet confidence threshold.</td></tr>';
          return;
        }

        tbody.innerHTML = items.map(i => {
          const conf = Math.round((i.overall_confidence || 0) * 100);
          const confClass = conf >= 80 ? 'conf-high' : conf >= 50 ? 'conf-med' : 'conf-low';
          const fieldsStr = Object.entries(i.fields || {}).slice(0, 3).map(([k, v]) => `${k}: <strong>${escapeHtml(String(v))}</strong>`).join(', ');
          return `
            <tr>
              <td><code>${i.document_id.slice(0, 8)}...</code></td>
              <td><span class="tag tag-pro">${i.schema_type}</span></td>
              <td>
                <div class="conf-bar-container"><div class="conf-bar-fill ${confClass}" style="width:${conf}%"></div></div>
                <span>${conf}%</span>
              </td>
              <td><small>${fieldsStr}</small></td>
              <td><small>${i.created_at ? i.created_at.slice(0, 19).replace('T', ' ') : ''}</small></td>
              <td><button class="btn btn-primary btn-edit-extraction" data-doc-id="${i.document_id}">Review & Correct</button></td>
            </tr>
          `;
        }).join('');
      } catch (e) {
        tbody.innerHTML = `<tr><td colspan="6" style="color:var(--danger); text-align:center; padding:2rem;">Error: ${e.message}</td></tr>`;
      }
    }

    async function loadDLQ() {
      try {
        const res = await fetch('/admin/dlq', { headers: getAuthHeaders() });
        if (!res.ok) throw new Error("HTTP " + res.status);
        const data = await res.json();
        document.getElementById('dlq-ingest-depth').textContent = data.ingestion_dlq_depth || 0;
        document.getElementById('dlq-extract-depth').textContent = data.extraction_dlq_depth || 0;

        const tbody = document.getElementById('dlq-extract-tbody');
        const rows = data.recent_failed_extractions || [];
        if (rows.length === 0) {
          tbody.innerHTML = '<tr><td colspan="5" style="text-align:center; color:var(--text-muted); padding:1.5rem;">No failed extractions.</td></tr>';
        } else {
          tbody.innerHTML = rows.map(r => `
            <tr>
              <td><strong>${escapeHtml(r.tenant_name || '')}</strong></td>
              <td><code>${(r.document_id || '').slice(0, 8)}...</code></td>
              <td><span class="tag tag-free">${escapeHtml(r.schema_type || '')}</span></td>
              <td><code style="color:#f87171">${escapeHtml(r.error || '')}</code></td>
              <td><small>${(r.created_at || '').slice(0, 19).replace('T', ' ')}</small></td>
            </tr>
          `).join('');
        }
      } catch (e) {
        showToast("Error loading DLQ: " + e.message);
      }
    }

    async function loadFailedIngestions() {
      const tbody = document.getElementById('ingestions-tbody');
      try {
        const res = await fetch('/admin/ingestions/failed', { headers: getAuthHeaders() });
        if (!res.ok) throw new Error("HTTP " + res.status);
        const data = await res.json();
        const rows = data.failed_ingestions || [];
        if (rows.length === 0) {
          tbody.innerHTML = '<tr><td colspan="3" style="text-align:center; padding:2rem; color:var(--text-muted);">No failed ingestions recorded.</td></tr>';
          return;
        }
        tbody.innerHTML = rows.map(r => `
          <tr>
            <td><strong>${escapeHtml(r.tenant_name || '')}</strong></td>
            <td><code>${escapeHtml(r.filename || '')}</code></td>
            <td><span style="color:#f87171">${escapeHtml(r.error_message || '')}</span></td>
          </tr>
        `).join('');
      } catch (e) {
        tbody.innerHTML = `<tr><td colspan="3" style="color:var(--danger); text-align:center; padding:2rem;">Error: ${e.message}</td></tr>`;
      }
    }

    function openTierModal(tenantId, currentTier, currentStatus) {
      document.getElementById('modal-tenant-id').value = tenantId;
      document.getElementById('modal-tier-select').value = currentTier;
      document.getElementById('modal-status-select').value = currentStatus;
      document.getElementById('tier-modal').classList.add('active');
    }

    async function submitTierChange() {
      const tenantId = document.getElementById('modal-tenant-id').value;
      const tier = document.getElementById('modal-tier-select').value;
      const status = document.getElementById('modal-status-select').value;

      try {
        const res = await fetch(`/admin/billing/tenants/${tenantId}/tier`, {
          method: 'PUT',
          headers: getAuthHeaders(),
          body: JSON.stringify({ tier, subscription_status: status })
        });
        if (!res.ok) throw new Error("HTTP " + res.status);
        closeModal('tier-modal');
        showToast("Tenant tier updated successfully!");
        loadTenants();
      } catch (e) {
        alert("Failed to update tier: " + e.message);
      }
    }

    function openCorrectionModal(docId) {
      const item = extractionsCache[docId];
      if (!item) return;
      document.getElementById('modal-correction-doc-id').value = docId;
      const container = document.getElementById('correction-fields-container');
      const fields = item.fields || {};
      const confs = item.field_confidence || {};

      container.innerHTML = Object.entries(fields).map(([k, v]) => {
        const c = Math.round((confs[k] || 0) * 100);
        return `
          <div class="form-group">
            <div style="display:flex; justify-content:space-between; margin-bottom:0.25rem;">
              <label class="form-label">${escapeHtml(k)}</label>
              <small style="color:${c >= 80 ? 'var(--success)' : c >= 50 ? 'var(--warning)' : 'var(--danger)'}">Conf: ${c}%</small>
            </div>
            <input type="text" class="form-input correction-field-input" data-key="${escapeHtml(k)}" value="${escapeHtml(String(v || ''))}">
          </div>
        `;
      }).join('');

      document.getElementById('correction-modal').classList.add('active');
    }

    async function submitExtractionCorrection() {
      const docId = document.getElementById('modal-correction-doc-id').value;
      const inputs = document.querySelectorAll('.correction-field-input');
      const corrected = {};
      inputs.forEach(inp => {
        corrected[inp.dataset.key] = inp.value.trim();
      });

      try {
        const res = await fetch(`/admin/extractions/${docId}/correct`, {
          method: 'PUT',
          headers: getAuthHeaders(),
          body: JSON.stringify({ corrected_fields: corrected })
        });
        if (!res.ok) throw new Error("HTTP " + res.status);
        closeModal('correction-modal');
        showToast("Correction saved and review flag cleared!");
        loadExtractions();
      } catch (e) {
        alert("Failed to save correction: " + e.message);
      }
    }

    function closeModal(id) {
      document.getElementById(id).classList.remove('active');
    }

    function escapeHtml(s) {
      return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }

    // Initialize & bind event listeners (strict CSP compliant, zero inline handlers)
    window.addEventListener('DOMContentLoaded', () => {
      const saved = localStorage.getItem('kf_admin_token');
      if (saved) document.getElementById('bearer-token').value = saved;

      document.getElementById('bearer-token')?.addEventListener('change', saveToken);
      document.getElementById('btn-refresh')?.addEventListener('click', loadActiveView);
      document.querySelectorAll('.nav-btn[data-view]').forEach(btn => {
        btn.addEventListener('click', () => switchView(btn.getAttribute('data-view')));
      });
      document.querySelectorAll('[data-close-modal]').forEach(btn => {
        btn.addEventListener('click', () => closeModal(btn.getAttribute('data-close-modal')));
      });
      document.getElementById('btn-save-tier')?.addEventListener('click', submitTierChange);
      document.getElementById('btn-submit-correction')?.addEventListener('click', submitExtractionCorrection);

      // Event delegation for dynamically loaded tables
      document.addEventListener('click', (e) => {
        const tierBtn = e.target.closest('.btn-edit-tier');
        if (tierBtn) {
          openTierModal(
            tierBtn.getAttribute('data-tenant-id'),
            tierBtn.getAttribute('data-tier'),
            tierBtn.getAttribute('data-status')
          );
          return;
        }
        const extBtn = e.target.closest('.btn-edit-extraction');
        if (extBtn) {
          openCorrectionModal(extBtn.getAttribute('data-doc-id'));
          return;
        }
      });

      loadActiveView();
    });
  </script>
</body>
</html>
"""


def render_admin_html(nonce: str = "") -> str:
    """Render the admin console single-page HTML with optional CSP script nonce."""
    nonce_attr = f' nonce="{nonce}"' if nonce else ""
    return _ADMIN_HTML_TEMPLATE.replace("<script>", f"<script{nonce_attr}>")


ADMIN_HTML = render_admin_html("")

