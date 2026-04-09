/** Grid Stabilization v2 — Dashboard JS */
const API = '/dashboard/api';
const EMS = '/api';
let state = { status: null, miners: [] };
let pollTimer = null;

document.addEventListener('DOMContentLoaded', () => {
  setupControls();
  poll();
  pollTimer = setInterval(poll, 3000);
});

async function poll() {
  try {
    const [s, m] = await Promise.all([
      fetch(`${API}/status`).then(r => r.json()),
      fetch(`${API}/miners`).then(r => r.json()),
    ]);
    state.status = s;
    state.miners = m;
    render();
    setConnected(true);
  } catch (e) {
    setConnected(false);
  }
}

function render() {
  const s = state.status;
  if (!s) return;

  // Header stats
  setText('h-state', s.state.toUpperCase());
  setText('h-power', `${s.active_power_kw.toFixed(1)} kW`);
  setText('h-target', s.target_kw > 0 ? `${s.target_kw.toFixed(0)} kW` : '—');
  setText('h-miners', `${s.mining_miners}/${s.total_miners}`);

  // State dot
  const dot = document.getElementById('state-dot');
  dot.className = 'status-dot ' + stateDotClass(s.state);

  // Overview metrics
  setText('m-active', `${s.active_power_kw.toFixed(1)}`);
  setText('m-target', s.target_kw > 0 ? `${s.target_kw.toFixed(0)}` : '0');
  setText('m-rated', `${s.rated_kw.toFixed(0)}`);
  setText('m-measured', s.measured_power_kw != null ? `${s.measured_power_kw.toFixed(1)}` : '—');
  setText('m-mining', `${s.mining_miners}`);
  setText('m-waking', `${s.waking_miners}`);
  setText('m-idle', `${s.idle_miners}`);
  setText('m-voltage', s.voltage != null ? `${s.voltage.toFixed(0)}V` : '—');

  // Power bar
  const pct = s.rated_kw > 0 ? (s.active_power_kw / s.rated_kw * 100) : 0;
  const tgtPct = s.rated_kw > 0 ? (s.target_kw / s.rated_kw * 100) : 0;
  setStyle('power-fill', 'width', `${Math.min(pct, 100)}%`);
  setStyle('power-target', 'left', `${Math.min(tgtPct, 100)}%`);
  setStyle('power-target', 'display', s.target_kw > 0 ? 'block' : 'none');
  setText('pb-actual', `${s.active_power_kw.toFixed(1)} kW`);
  setText('pb-rated', `${s.rated_kw.toFixed(0)} kW`);

  // Sections
  renderSections(s.sections);

  // Miner grid
  renderMiners();
}

function renderSections(sections) {
  const container = document.getElementById('sections');
  container.innerHTML = '';
  for (const sec of sections) {
    const active = sec.target_kw > 0;
    const pct = sec.target_kw > 0 ? Math.min(100, sec.estimated_kw / sec.target_kw * 100) : 0;
    const barColor = pct >= 80 ? 'var(--green)' : pct >= 40 ? 'var(--yellow)' : 'var(--red)';
    const offline = sec.total - sec.mining - sec.waking - sec.idle;

    const card = document.createElement('div');
    card.className = `section-card${active ? ' active' : ''}`;
    card.innerHTML = `
      <div class="sec-header">
        <span class="sec-name">Section ${sec.name}</span>
        <span class="sec-target">${sec.target_kw > 0 ? sec.target_kw.toFixed(0) + ' kW' : 'IDLE'}</span>
      </div>
      <div class="sec-bar-bg">
        <div class="sec-bar-fill" style="width:${pct}%; background:${barColor}"></div>
      </div>
      <div class="sec-counts">
        <span class="c-mining">⛏ ${sec.mining}</span>
        <span class="c-waking">⏳ ${sec.waking}</span>
        <span class="c-idle">💤 ${sec.idle}</span>
        <span class="c-off">${offline > 0 ? '✕ ' + offline : ''}</span>
      </div>
    `;
    container.appendChild(card);
  }
}

function renderMiners() {
  const container = document.getElementById('miner-grid');
  container.innerHTML = '';
  for (const m of state.miners) {
    const dot = document.createElement('div');
    const octet = m.ip.split('.').pop();
    dot.className = `miner-dot ${m.state}`;
    dot.textContent = octet;
    dot.addEventListener('mouseenter', (e) => showTooltip(e, m));
    dot.addEventListener('mouseleave', hideTooltip);
    container.appendChild(dot);
  }
}

// Controls
function setupControls() {
  document.getElementById('btn-activate')?.addEventListener('click', doActivate);
  document.getElementById('btn-deactivate')?.addEventListener('click', doDeactivate);
}

async function doActivate() {
  const input = document.getElementById('power-input');
  const kw = parseFloat(input.value);
  if (isNaN(kw) || kw <= 0) return;
  try {
    const r = await fetch(`${EMS}/activate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ activationPowerInKw: kw }),
    });
    const d = await r.json();
    if (!d.accepted) alert(d.message);
    poll();
  } catch (e) {
    alert('Activate failed: ' + e.message);
  }
}

async function doDeactivate() {
  try {
    await fetch(`${EMS}/deactivate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({}),
    });
    poll();
  } catch (e) {
    alert('Deactivate failed: ' + e.message);
  }
}

// Tooltip
function showTooltip(e, m) {
  const tt = document.getElementById('tooltip');
  tt.innerHTML = `
    <div class="tt-ip">${m.ip} (${m.section})</div>
    <div class="tt-row">${m.state} — ${m.hashrate_ghs.toFixed(0)} GH/s</div>
    <div class="tt-row">${m.power_watts.toFixed(0)}W · ${m.temperature_c}°C · Fan ${m.fan_speed_pct.toFixed(0)}%</div>
    ${m.model ? `<div class="tt-row">${m.model}</div>` : ''}
  `;
  tt.style.display = 'block';
  tt.style.left = `${e.clientX + 12}px`;
  tt.style.top = `${e.clientY + 12}px`;
}

function hideTooltip() {
  document.getElementById('tooltip').style.display = 'none';
}

function setConnected(ok) {
  const el = document.getElementById('conn-status');
  el.textContent = ok ? 'Connected' : 'Disconnected';
  document.getElementById('state-dot').className = 'status-dot ' + (ok ? stateDotClass(state.status?.state) : 'dot-red');
}

function stateDotClass(st) {
  if (st === 'running') return 'dot-green';
  if (st === 'activating' || st === 'deactivating') return 'dot-yellow';
  return 'dot-gray';
}

function setText(id, text) { const e = document.getElementById(id); if(e) e.textContent = text; }
function setStyle(id, prop, val) { const e = document.getElementById(id); if(e) e.style[prop] = val; }
