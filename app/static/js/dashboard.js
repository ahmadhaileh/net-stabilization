/**
 * Net Stabilization Dashboard - Enhanced
 * Real-time miner monitoring and control
 */

// Configuration
const CONFIG = {
    pollInterval: 3000,
    apiBase: '/dashboard/api',
    emsBase: '/api',
};

// State
let state = {
    connected: false,
    status: null,
    miners: [],
    discoveredMiners: [],
    history: [],
    overrideEnabled: false,
    viewMode: 'rack',  // Default to rack view
    ratedPower: 0,
    lastScan: null,
    minerDetailsCache: {}, // Cache detailed miner data for instant modal population
};

// DOM Elements cache
const $ = (id) => document.getElementById(id);

// Initialize
document.addEventListener('DOMContentLoaded', () => {
    setupEventListeners();
    startPolling();
});

function setupEventListeners() {
    // Fleet control buttons
    $('btn-idle-all')?.addEventListener('click', handleIdleAll);
    $('btn-resume-all')?.addEventListener('click', handleResumeAll);
    $('btn-emergency')?.addEventListener('click', handleEmergencyStop);

    // Power control
    $('btn-set-power')?.addEventListener('click', handleSetPower);
    $('btn-preview-power')?.addEventListener('click', handlePreviewPower);
    $('power-slider')?.addEventListener('input', handleSliderChange);
    $('power-input')?.addEventListener('change', handlePowerInputChange);

    // Override toggle
    $('override-toggle')?.addEventListener('change', handleOverrideToggle);
    
    // Power mode select
    $('power-mode-select')?.addEventListener('change', handlePowerModeChange);

    // Discovery
    $('btn-scan')?.addEventListener('click', handleScan);
    $('btn-add-miner')?.addEventListener('click', handleAddMiner);
    $('btn-scan-close')?.addEventListener('click', () => $('scan-modal').classList.add('hidden'));

    // View toggle
    $('view-grid')?.addEventListener('click', () => setViewMode('grid'));
    $('view-table')?.addEventListener('click', () => setViewMode('table'));
    $('view-rack')?.addEventListener('click', () => setViewMode('rack'));

    // History toggle
    $('history-toggle')?.addEventListener('click', toggleHistory);

    // Graph miner selector change handler
    $('graph-miner-select')?.addEventListener('change', handleGraphMinerChange);

    // Time scope buttons
    document.querySelectorAll('.scope-btn').forEach((btn) => {
        btn.addEventListener('click', () => setTimeScope(btn.dataset.scope));
    });
}

// =========================================================================
// Polling and Data Fetching
// =========================================================================

async function startPolling() {
    await Promise.all([fetchStatus(), fetchDiscoveredMiners(), fetchHealth(), fetchHistory()]);

    setInterval(fetchStatus, CONFIG.pollInterval);
    setInterval(fetchDiscoveredMiners, CONFIG.pollInterval);
    setInterval(fetchHistory, 10000);
}

async function fetchStatus() {
    try {
        const response = await fetch(`${CONFIG.apiBase}/status`);
        if (response.ok) {
            state.status = await response.json();
            state.connected = true;
            updateStatusDisplay();
        } else {
            throw new Error('Status request failed');
        }
    } catch (error) {
        console.error('Failed to fetch status:', error);
        state.connected = false;
        updateConnectionStatus(false);
    }
}

async function fetchDiscoveredMiners() {
    try {
        const response = await fetch(`${CONFIG.apiBase}/discovery/miners`);
        if (response.ok) {
            const data = await response.json();
            state.discoveredMiners = data.miners || [];
            updateMinersDisplay();

            // Update modal charts if modal is open
            if (currentModalMiner && $('miner-modal').classList.contains('active')) {
                const updated = state.discoveredMiners.find((m) => m.ip === currentModalMiner.ip);
                if (updated) {
                    currentModalMiner = updated;
                    // Only update basic live data, preserve detailed data
                    updateModalOverviewLive(updated);
                    addMinerChartDataPoint(updated);
                }
            }
        }
    } catch (error) {
        console.error('Failed to fetch discovered miners:', error);
    }
}

async function fetchHealth() {
    try {
        const response = await fetch(`${CONFIG.apiBase}/health`);
        if (response.ok) {
            const data = await response.json();
            updateHealthDisplay(data);
        }
    } catch (error) {
        console.error('Failed to fetch health:', error);
    }
}

async function fetchHistory() {
    try {
        const response = await fetch(`${CONFIG.apiBase}/history?limit=20`);
        if (response.ok) {
            const data = await response.json();
            state.history = data.commands || [];
            updateHistoryDisplay();
        }
    } catch (error) {
        console.error('Failed to fetch history:', error);
    }
}

// =========================================================================
// Display Updates
// =========================================================================

function updateStatusDisplay() {
    if (!state.status) return;

    updateConnectionStatus(true);

    // Fleet state
    const stateEl = $('fleet-state');
    const stateClass = state.status.state.toLowerCase();
    stateEl.className = `state-indicator ${stateClass}`;
    stateEl.innerHTML = `
        <span class="state-icon">●</span>
        <span class="state-text">${state.status.state.toUpperCase()}</span>
    `;

    // State subtitle
    const subtitles = {
        standby: 'Fleet in idle mode - ready for dispatch',
        running: 'Fleet actively mining',
        activating: 'Bringing miners online...',
        deactivating: 'Putting miners into idle mode...',
        fault: 'Error detected - check miners',
        unknown: 'Waiting for miner data...',
    };
    $('state-subtitle').textContent = subtitles[stateClass] || '';

    // Counts
    $('miners-online').textContent = state.status.online_miners;
    $('miners-total').textContent = state.status.total_miners;
    $('miners-mining').textContent = state.status.mining_miners;

    // Power
    state.ratedPower = state.status.rated_power_kw;
    $('active-power').textContent = state.status.active_power_kw.toFixed(1);
    $('rated-power').textContent = state.status.rated_power_kw.toFixed(1);
    $('total-power').textContent = state.status.active_power_kw.toFixed(1) + ' kW';

    const powerPercent = state.ratedPower > 0 ? (state.status.active_power_kw / state.ratedPower) * 100 : 0;
    $('power-bar-fill').style.width = `${Math.min(powerPercent, 100)}%`;
    $('power-percent').textContent = `${powerPercent.toFixed(0)}%`;

    // Update slider max
    $('slider-max').textContent = state.ratedPower.toFixed(1) + ' kW';

    // Target marker
    if (state.status.target_power_kw !== null) {
        const targetPercent = (state.status.target_power_kw / state.ratedPower) * 100;
        $('target-marker').style.left = `${Math.min(targetPercent, 100)}%`;
        $('target-marker').style.display = 'block';
        $('ems-target').textContent = state.status.target_power_kw.toFixed(1) + ' kW';
    } else {
        $('target-marker').style.display = 'none';
        $('ems-target').textContent = '-- kW';
    }

    // EMS status
    const emsAvailable = $('ems-available');
    emsAvailable.textContent = state.status.is_available_for_dispatch ? 'YES' : 'NO';
    emsAvailable.className = `ems-value ${state.status.is_available_for_dispatch ? 'status-yes' : 'status-no'}`;

    const runningStatuses = { 0: 'STANDBY', 1: 'STANDBY', 2: 'RUNNING' };
    $('ems-running').textContent = runningStatuses[state.status.running_status] || 'STANDBY';

    if (state.status.last_ems_command) {
        $('ems-last-cmd').textContent = new Date(state.status.last_ems_command).toLocaleString();
    }

    // Override badge
    const overrideBadge = $('override-badge');
    if (state.status.manual_override_active) {
        overrideBadge.classList.remove('hidden');
        $('override-toggle').checked = true;
    } else {
        overrideBadge.classList.add('hidden');
        $('override-toggle').checked = false;
    }
    
    // Power mode toggle
    updatePowerModeDisplay(state.status.power_control_mode);

    // Calculate total hashrate
    updateTotalHashrate();
}

function updateHealthDisplay(health) {
    // Update network CIDR display
    const networkCidrEl = $('network-cidr');
    if (networkCidrEl && health.network_cidr) {
        networkCidrEl.textContent = health.network_cidr;
    }
}

function updateConnectionStatus(connected) {
    const dot = $('status-dot');
    const text = $('connection-status');

    if (connected) {
        dot.classList.add('connected');
        text.textContent = 'Connected';
    } else {
        dot.classList.remove('connected');
        text.textContent = 'Disconnected';
    }
}

function updateFleetMetrics() {
    let totalGhs = 0;
    let totalTemp = 0;
    let tempCount = 0;
    let totalPower = 0;
    let miningCount = 0;

    const IDLE_POWER_KW = 0.018; // 18W idle power consumption for control board

    state.discoveredMiners.forEach((m) => {
        const hashrate = parseFloat(m.hashrate_ghs) || 0;
        totalGhs += hashrate;

        if (m.temperature_c > 0) {
            totalTemp += m.temperature_c;
            tempCount++;
        }

        if (m.is_mining) {
            // Only count actual power, not rated power fallback
            totalPower += m.power_kw > 0 ? m.power_kw : 0;
            miningCount++;
        } else if (m.is_online) {
            // Idle but online - control board still consumes power
            totalPower += IDLE_POWER_KW;
        }
    });

    // Update header stats
    const ths = totalGhs / 1000;
    $('total-hashrate').textContent = ths >= 1 ? ths.toFixed(2) + ' TH/s' : totalGhs.toFixed(0) + ' GH/s';

    // Update fleet hashrate
    const fleetHashrateEl = $('fleet-hashrate');
    if (fleetHashrateEl) {
        fleetHashrateEl.textContent = ths >= 1 ? ths.toFixed(2) + ' TH/s' : totalGhs.toFixed(0) + ' GH/s';
    }

    // Update hashrate bar (assume max 100 TH/s for fleet)
    const hashrateBarEl = $('hashrate-bar-fill');
    if (hashrateBarEl) {
        const maxFleetHashrate = Math.max(state.discoveredMiners.length * 14, 14); // ~14 TH/s per S9
        const hashratePercent = Math.min((ths / maxFleetHashrate) * 100, 100);
        hashrateBarEl.style.width = hashratePercent + '%';
    }

    // Update average temperature
    const avgTemp = tempCount > 0 ? totalTemp / tempCount : 0;
    const fleetTempEl = $('fleet-temp');
    if (fleetTempEl) {
        fleetTempEl.textContent = avgTemp > 0 ? avgTemp.toFixed(0) + ' °C' : '-- °C';
    }

    // Update temp bar
    const tempBarEl = $('temp-bar-fill');
    if (tempBarEl) {
        const tempPercent = Math.min((avgTemp / 100) * 100, 100);
        tempBarEl.style.width = tempPercent + '%';
        tempBarEl.classList.remove('warning', 'critical');
        if (avgTemp >= 90) tempBarEl.classList.add('critical');
        else if (avgTemp >= 75) tempBarEl.classList.add('warning');
    }

    // Update efficiency (GH/W)
    const efficiency = totalPower > 0 ? totalGhs / (totalPower * 1000) : 0;
    const efficiencyEl = $('fleet-efficiency');
    if (efficiencyEl) {
        efficiencyEl.textContent = efficiency > 0 ? efficiency.toFixed(2) + ' GH/W' : '-- GH/W';
    }

    // Update efficiency bar (good efficiency is ~0.1 GH/W for S9)
    const efficiencyBarEl = $('efficiency-bar-fill');
    if (efficiencyBarEl) {
        const effPercent = Math.min((efficiency / 0.12) * 100, 100);
        efficiencyBarEl.style.width = effPercent + '%';
    }
}

function updateTotalHashrate() {
    updateFleetMetrics();
}

function updateMinersDisplay() {
    if (state.viewMode === 'grid') {
        updateMinersGrid();
    } else if (state.viewMode === 'table') {
        updateMinersTable();
    } else if (state.viewMode === 'rack') {
        updateMinersRack();
    }
    updateFleetMetrics();
    updateMinerDropdowns();
}

function updateMinersGrid() {
    const grid = $('miners-grid');

    if (state.discoveredMiners.length === 0) {
        grid.innerHTML = `
            <div class="miner-card placeholder">
                <div class="placeholder-content">
                    <svg class="placeholder-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
                    <span class="placeholder-text">No miners found. Click "Scan Network" to discover miners.</span>
                </div>
            </div>
        `;
        return;
    }

    grid.innerHTML = state.discoveredMiners
        .map((miner) => {
            const statusClass = miner.is_mining ? 'mining' : miner.is_online ? 'idle' : 'offline';
            const statusText = miner.is_mining ? 'Mining' : miner.is_online ? 'Idle' : 'Offline';

            const hashrate = parseFloat(miner.hashrate_ghs) || 0;
            const hashrateDisplay = hashrate >= 1000 ? (hashrate / 1000).toFixed(2) + ' TH/s' : hashrate.toFixed(0) + ' GH/s';
            const maxHashrate = 14000; // ~14 TH/s max for S9
            const hashratePercent = Math.min((hashrate / maxHashrate) * 100, 100);

            const temp = miner.temperature_c > 0 ? miner.temperature_c.toFixed(0) + '°C' : '--';
            const tempPercent = miner.temperature_c > 0 ? Math.min((miner.temperature_c / 100) * 100, 100) : 0;
            const tempClass = getTempClass(miner.temperature_c);

            const fan = miner.fan_speed_pct > 0 ? miner.fan_speed_pct.toFixed(0) + '%' : '--';
            const fanPercent = miner.fan_speed_pct || 0;

            const uptime = formatUptime(miner.uptime_seconds);
            const pool = extractPoolName(miner.pool_url);

            const power = miner.power_kw > 0 ? miner.power_kw.toFixed(2) : miner.rated_power_kw.toFixed(2);
            const efficiency = hashrate > 0 && miner.rated_power_kw > 0 ? (hashrate / (miner.rated_power_kw * 1000)).toFixed(2) : '--';

            // Board status - when miner is idle/not mining, we can't know board status
            // so show all as unknown (null) rather than simulating incorrect data
            // When mining, boards are working. When offline, boards are down.
            // When idle (online but not mining), we don't have board data, show as OK (not error)
            const boards = miner.is_mining ? [true, true, true] : miner.is_online ? [true, true, true] : [false, false, false];

            // Get firmware badge
            const firmwareType = miner.firmware_type || 'unknown';
            const firmwareBadgeText =
                firmwareType === 'vnish' ? 'Vnish' : firmwareType === 'braiins' ? 'BraiinsOS' : firmwareType === 'stock' ? 'Stock' : firmwareType === 'marathon' ? 'Marathon' : '';

            return `
            <div class="miner-card ${statusClass}">
                <div class="miner-header">
                    <div class="miner-status-badge ${statusClass}">
                        <span class="status-dot"></span>
                        ${statusText}
                    </div>
                    ${firmwareBadgeText ? `<span class="firmware-badge ${firmwareType}">${firmwareBadgeText}</span>` : ''}
                    <div class="miner-actions-mini">
                        <button class="btn-icon-small" onclick="controlMiner('${miner.id}', 'restart')" title="Soft Restart (CGMiner)">
                            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg>
                        </button>
                        <div class="dropdown">
                            <button class="btn-icon-small dropdown-toggle" onclick="toggleDropdown(this)" title="More Actions">
                                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="1"/><circle cx="12" cy="5" r="1"/><circle cx="12" cy="19" r="1"/></svg>
                            </button>
                            <div class="dropdown-menu">
                                <button class="dropdown-item" onclick="controlMiner('${miner.id}', 'reboot')">
                                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18.36 6.64a9 9 0 1 1-12.73 0"/><line x1="12" y1="2" x2="12" y2="12"/></svg>
                                    Full Reboot
                                </button>
                                <button class="dropdown-item danger" onclick="confirmReset('${miner.id}')">
                                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/></svg>
                                    Factory Reset
                                </button>
                                <div class="dropdown-divider"></div>
                                <button class="dropdown-item danger" onclick="removeMiner('${miner.id}')">
                                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
                                    Remove Miner
                                </button>
                            </div>
                        </div>
                    </div>
                </div>
                
                <div class="miner-body">
                    <div class="miner-identity">
                        <span class="miner-model">${escapeHtml(miner.model)}</span>
                        <span class="miner-ip">${miner.ip}</span>
                    </div>
                    
                    <div class="miner-hashrate-display">
                        <div class="hashrate-main">
                            <svg class="hashrate-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z"/></svg>
                            <span class="hashrate-value">${hashrateDisplay}</span>
                        </div>
                        <div class="hashrate-bar">
                            <div class="hashrate-bar-fill ${statusClass}" style="width: ${hashratePercent}%"></div>
                        </div>
                    </div>
                    
                    <div class="miner-metrics">
                        <div class="metric-item">
                            <div class="metric-header">
                                <svg class="metric-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 14.76V3.5a2.5 2.5 0 0 0-5 0v11.26a4.5 4.5 0 1 0 5 0z"/></svg>
                                <span class="metric-label">Temp</span>
                                <span class="metric-value ${tempClass}">${temp}</span>
                            </div>
                            <div class="metric-bar">
                                <div class="metric-bar-fill temp ${tempClass}" style="width: ${tempPercent}%"></div>
                            </div>
                        </div>
                        <div class="metric-item">
                            <div class="metric-header">
                                <svg class="metric-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9.59 4.59A2 2 0 1 1 11 8H2m10.59 11.41A2 2 0 1 0 14 16H2m15.73-8.27A2.5 2.5 0 1 1 19.5 12H2"/></svg>
                                <span class="metric-label">Fan</span>
                                <span class="metric-value">${fan}</span>
                            </div>
                            <div class="metric-bar">
                                <div class="metric-bar-fill fan" style="width: ${fanPercent}%"></div>
                            </div>
                        </div>
                    </div>
                    
                    <div class="miner-details">
                        <div class="detail-row">
                            <span class="detail-label">Power</span>
                            <span class="detail-value">${power} kW</span>
                        </div>
                        <div class="detail-row">
                            <span class="detail-label">Efficiency</span>
                            <span class="detail-value">${efficiency} GH/W</span>
                        </div>
                        <div class="detail-row">
                            <span class="detail-label">Uptime</span>
                            <span class="detail-value">${uptime}</span>
                        </div>
                        <div class="detail-row">
                            <span class="detail-label">Pool</span>
                            <span class="detail-value pool">${pool}</span>
                        </div>
                    </div>
                    
                    <div class="miner-boards">
                        <span class="boards-label">Hash Boards</span>
                        <div class="boards-indicators">
                            ${boards.map((ok, i) => `<span class="board-indicator ${ok ? 'ok' : 'error'}" title="Board ${i + 1}: ${ok ? 'OK' : 'Error'}"></span>`).join('')}
                        </div>
                    </div>
                </div>
                
                <div class="miner-footer">
                    ${
                        miner.is_mining
                            ? `<button class="btn btn-warning btn-small" onclick="controlMiner('${miner.id}', 'stop')">
                             <svg class="btn-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="6" y="4" width="4" height="16"/><rect x="14" y="4" width="4" height="16"/></svg>
                             <span class="btn-text">Idle</span>
                           </button>`
                            : `<button class="btn btn-success btn-small" onclick="controlMiner('${miner.id}', 'start')" ${!miner.is_online ? 'disabled' : ''}>
                             <svg class="btn-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="5 3 19 12 5 21 5 3"/></svg>
                             <span class="btn-text">Resume</span>
                           </button>`
                    }
                    <button class="btn btn-secondary btn-small" onclick="toggleFindMiner('${miner.ip}')" title="Toggle find mode (LED blink)">
                        <svg class="btn-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="5"/><line x1="12" y1="1" x2="12" y2="3"/><line x1="12" y1="21" x2="12" y2="23"/><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/><line x1="1" y1="12" x2="3" y2="12"/><line x1="21" y1="12" x2="23" y2="12"/><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/></svg>
                        <span class="btn-text">Find</span>
                    </button>
                    <a href="http://${miner.ip}" target="_blank" class="btn btn-secondary btn-small">
                        <svg class="btn-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="2" y1="12" x2="22" y2="12"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/></svg>
                        <span class="btn-text">Web UI</span>
                    </a>
                </div>
            </div>
        `;
        })
        .join('');
}

function updateMinersTable() {
    const tbody = $('miners-table-body');

    if (state.discoveredMiners.length === 0) {
        tbody.innerHTML = `<tr><td colspan="9" class="empty-row">No miners found</td></tr>`;
        return;
    }

    tbody.innerHTML = state.discoveredMiners
        .map((miner) => {
            const statusClass = miner.is_mining ? 'mining' : miner.is_online ? 'idle' : 'offline';
            const statusText = miner.is_mining ? 'Mining' : miner.is_online ? 'Idle' : 'Offline';

            const hashrate = parseFloat(miner.hashrate_ghs) || 0;
            const hashrateDisplay = hashrate >= 1000 ? (hashrate / 1000).toFixed(2) + ' TH/s' : hashrate.toFixed(0) + ' GH/s';

            const temp = miner.temperature_c > 0 ? miner.temperature_c.toFixed(0) + '°C' : '--';
            const fan = miner.fan_speed_pct > 0 ? miner.fan_speed_pct.toFixed(0) + '%' : '--';
            const power = miner.power_kw > 0 ? miner.power_kw.toFixed(2) + ' kW' : miner.rated_power_kw.toFixed(2) + ' kW';
            const uptime = formatUptime(miner.uptime_seconds);
            const pool = extractPoolName(miner.pool_url);

            return `
            <tr class="${statusClass} miner-row" data-ip="${miner.ip}">
                <td>
                    <span class="miner-status-badge ${statusClass}">
                        <span class="status-dot"></span>
                        ${statusText}
                    </span>
                </td>
                <td>
                    <div class="miner-name-cell">
                        <span class="model">${escapeHtml(miner.model)}</span>
                        <span class="ip">${miner.ip}</span>
                    </div>
                </td>
                <td class="mono">${hashrateDisplay}</td>
                <td class="${getTempClass(miner.temperature_c)}">${temp}</td>
                <td>${fan}</td>
                <td>${power}</td>
                <td class="pool-cell">${pool}</td>
                <td>${uptime}</td>
                <td class="actions-cell">
                    <div class="actions-wrapper">
                        ${
                            miner.is_mining
                                ? `<button class="btn btn-warning btn-xs" onclick="controlMiner('${miner.id}', 'stop')">Idle</button>`
                                : `<button class="btn btn-success btn-xs" onclick="controlMiner('${miner.id}', 'start')" ${!miner.is_online ? 'disabled' : ''}>Resume</button>`
                        }
                        <button class="btn btn-secondary btn-xs" onclick="toggleFindMiner('${miner.ip}')" title="Toggle find mode">Find</button>
                        <button class="btn btn-secondary btn-xs" onclick="controlMiner('${miner.id}', 'restart')">Restart</button>
                        <a href="http://${miner.ip}" target="_blank" class="btn btn-secondary btn-xs">Web</a>
                    </div>
                </td>
            </tr>
        `;
        })
        .join('');
}

function updateMinersRack() {
    const rackUnits = $('rack-units');
    const rackTotalMiners = $('rack-total-miners');
    const rackTotalHashrate = $('rack-total-hashrate');
    const rackTotalPower = $('rack-total-power');

    if (state.discoveredMiners.length === 0) {
        rackUnits.innerHTML = `
            <div class="rack-empty">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <rect x="4" y="2" width="16" height="6" rx="1" />
                    <rect x="4" y="9" width="16" height="6" rx="1" />
                    <rect x="4" y="16" width="16" height="6" rx="1" />
                </svg>
                <span class="rack-empty-text">No miners found. Click "Scan Network" to discover miners.</span>
            </div>
        `;
        rackTotalMiners.textContent = '0';
        rackTotalHashrate.textContent = '0 TH/s';
        rackTotalPower.textContent = '0 kW';
        return;
    }

    // Calculate totals
    let totalHashrate = 0;
    let totalPower = 0;
    
    state.discoveredMiners.forEach(miner => {
        totalHashrate += parseFloat(miner.hashrate_ghs) || 0;
        totalPower += miner.power_kw > 0 ? miner.power_kw : miner.rated_power_kw;
    });

    // Update stats (separate value from label now)
    rackTotalMiners.textContent = state.discoveredMiners.length;
    rackTotalHashrate.textContent = totalHashrate >= 1000 
        ? (totalHashrate / 1000).toFixed(1) + ' TH/s' 
        : totalHashrate.toFixed(0) + ' GH/s';
    rackTotalPower.textContent = totalPower.toFixed(1) + ' kW';

    // Render rack slots
    rackUnits.innerHTML = state.discoveredMiners
        .map((miner) => {
            const statusClass = miner.is_mining ? 'mining' : miner.is_online ? 'idle' : 'offline';
            const statusText = miner.is_mining ? 'Mining' : miner.is_online ? 'Idle' : 'Offline';

            const hashrate = parseFloat(miner.hashrate_ghs) || 0;
            const hashrateDisplay = hashrate >= 1000 
                ? (hashrate / 1000).toFixed(2) + ' TH/s' 
                : hashrate.toFixed(0) + ' GH/s';

            const temp = miner.temperature_c > 0 ? miner.temperature_c.toFixed(0) + '°C' : '--';
            const power = miner.power_kw > 0 ? miner.power_kw.toFixed(2) : miner.rated_power_kw.toFixed(2);
            const fan = miner.fan_speed_pct > 0 ? miner.fan_speed_pct.toFixed(0) + '%' : '--';
            const efficiency = (hashrate > 0 && miner.power_kw > 0) 
                ? (hashrate / miner.power_kw / 1000).toFixed(2) + ' TH/kW'
                : '--';
            const uptime = miner.uptime_hours > 0 
                ? (miner.uptime_hours >= 24 
                    ? Math.floor(miner.uptime_hours / 24) + 'd ' + (miner.uptime_hours % 24).toFixed(0) + 'h'
                    : miner.uptime_hours.toFixed(1) + 'h')
                : '--';

            return `
                <div class="rack-slot ${statusClass}" onclick="openMinerModal('${miner.ip}')" data-ip="${miner.ip}">
                    <span class="slot-indicator"></span>
                    <div class="rack-tooltip">
                        <div class="tooltip-header">
                            <span class="tooltip-ip">${miner.ip}</span>
                            <span class="tooltip-status ${statusClass}">${statusText}</span>
                        </div>
                        <div class="tooltip-stats">
                            <div class="tooltip-stat">
                                <span class="tooltip-stat-label">Hashrate</span>
                                <span class="tooltip-stat-value">${hashrateDisplay}</span>
                            </div>
                            <div class="tooltip-stat">
                                <span class="tooltip-stat-label">Temp</span>
                                <span class="tooltip-stat-value">${temp}</span>
                            </div>
                            <div class="tooltip-stat">
                                <span class="tooltip-stat-label">Power</span>
                                <span class="tooltip-stat-value">${power} kW</span>
                            </div>
                            <div class="tooltip-stat">
                                <span class="tooltip-stat-label">Fan</span>
                                <span class="tooltip-stat-value">${fan}</span>
                            </div>
                            <div class="tooltip-stat">
                                <span class="tooltip-stat-label">Efficiency</span>
                                <span class="tooltip-stat-value">${efficiency}</span>
                            </div>
                            <div class="tooltip-stat">
                                <span class="tooltip-stat-label">Uptime</span>
                                <span class="tooltip-stat-value">${uptime}</span>
                            </div>
                            <div class="tooltip-stat" style="grid-column: 1 / -1;">
                                <span class="tooltip-stat-label">Model</span>
                                <span class="tooltip-stat-value">${escapeHtml(miner.model)}</span>
                            </div>
                        </div>
                    </div>
                </div>
            `;
        })
        .join('');
}

function updateHistoryDisplay() {
    const list = $('history-list');

    if (state.history.length === 0) {
        list.innerHTML = '<div class="history-empty">No commands yet</div>';
        return;
    }

    list.innerHTML = state.history
        .map((cmd) => {
            const time = new Date(cmd.timestamp).toLocaleString();
            const params = Object.keys(cmd.parameters).length > 0 ? JSON.stringify(cmd.parameters) : '';

            return `
            <div class="history-item ${cmd.success ? 'success' : 'failed'}">
                <span class="history-time">${time}</span>
                <span class="history-source ${cmd.source}">${cmd.source.toUpperCase()}</span>
                <span class="history-command">
                    <strong>${cmd.command}</strong>
                    ${params ? `<code>${params}</code>` : ''}
                </span>
                <span class="history-result">${cmd.success ? '✓' : '✗'}</span>
            </div>
        `;
        })
        .join('');
}

// =========================================================================
// User Actions
// =========================================================================

async function handleIdleAll() {
    const confirmed = await showConfirm({
        title: 'Idle All Miners',
        message: 'Put all miners into idle mode?',
        type: 'warning',
        confirmText: 'Idle All',
    });
    if (!confirmed) return;

    try {
        const response = await fetch(`${CONFIG.emsBase}/deactivate`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({}),
        });
        const data = await response.json();

        if (data.accepted) {
            showToast('Idle command sent to fleet', 'success');
        } else {
            showToast(data.message || 'Failed to idle fleet', 'error');
        }
        await fetchStatus();
    } catch (error) {
        showToast('Error sending idle command', 'error');
    }
}

async function handleResumeAll() {
    const power = state.ratedPower;

    try {
        const response = await fetch(`${CONFIG.emsBase}/activate`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ activationPowerInKw: power }),
        });
        const data = await response.json();

        if (data.accepted) {
            showToast('Resume command sent to fleet', 'success');
        } else {
            showToast(data.message || 'Failed to resume fleet', 'error');
        }
        await fetchStatus();
    } catch (error) {
        showToast('Error sending resume command', 'error');
    }
}

async function handleEmergencyStop() {
    const confirmed = await showConfirm({
        title: '⚠️ EMERGENCY STOP',
        message: 'Are you sure you want to stop all miners immediately?\n\nThis action cannot be undone.',
        type: 'danger',
        confirmText: 'Stop All',
    });
    if (!confirmed) return;

    try {
        const response = await fetch(`${CONFIG.emsBase}/deactivate`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({}),
        });
        showToast('Emergency stop command sent', 'warning');
        await fetchStatus();
    } catch (error) {
        showToast('Error sending emergency stop', 'error');
    }
}

// =========================================================================
// Miner Dropdowns
// =========================================================================

function updateMinerDropdowns() {
    const graphMinerSelect = $('graph-miner-select');

    // Update graph miner selector
    if (graphMinerSelect && state.discoveredMiners.length > 0) {
        const currentValue = graphMinerSelect.value;
        let options = '<option value="fleet">Entire Fleet</option>';
        options += state.discoveredMiners.map((m) => `<option value="${m.ip}" ${m.ip === currentValue ? 'selected' : ''}>${m.ip}</option>`).join('');
        graphMinerSelect.innerHTML = options;
    }
}

async function handleSetPower() {
    const power = parseFloat($('power-input').value);
    if (isNaN(power) || power < 0) {
        showToast('Please enter a valid power value', 'error');
        return;
    }

    try {
        const response = await fetch(`${CONFIG.emsBase}/activate`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ activationPowerInKw: power }),
        });
        const data = await response.json();

        if (data.accepted) {
            showToast(`Target power set to ${power} kW`, 'success');
            // Hide allocation preview after applying
            $('power-allocation-section')?.classList.add('hidden');
        } else {
            showToast(data.message || 'Failed to set power target', 'error');
        }
        await fetchStatus();
    } catch (error) {
        showToast('Error setting power target', 'error');
    }
}

async function handlePreviewPower() {
    const power = parseFloat($('power-input').value);
    if (isNaN(power) || power < 0) {
        showToast('Please enter a valid power value', 'error');
        return;
    }

    const allocationSection = $('power-allocation-section');
    const allocationList = $('allocation-list');

    try {
        const response = await fetch(`${CONFIG.apiBase}/power/calculate`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ target_power_kw: power }),
        });
        const data = await response.json();

        if (data.error) {
            showToast(data.error, 'error');
            return;
        }

        // Update summary counts
        $('alloc-full-count').textContent = data.summary?.full_miners || 0;
        $('alloc-swing-count').textContent = data.summary?.swing_miners || 0;
        $('alloc-idle-count').textContent = data.summary?.idle_miners || 0;
        $('alloc-estimated-power').textContent = data.summary?.estimated_power_kw?.toFixed(2) || '0.0';

        // Render allocation items
        if (allocationList) {
            allocationList.innerHTML = (data.allocation || []).map(alloc => {
                const actionClass = alloc.action;
                const freqText = alloc.action === 'idle' ? 'Idle' : `${alloc.frequency}MHz`;
                const powerText = alloc.action === 'idle' ? '0W' : `~${alloc.estimated_power}W`;
                
                return `
                    <div class="allocation-item ${actionClass}">
                        <span class="status-indicator"></span>
                        <span class="miner-ip">${alloc.ip}</span>
                        <div class="miner-details">
                            <span class="miner-freq">${freqText}</span>
                            <span class="miner-power">${powerText}</span>
                        </div>
                    </div>
                `;
            }).join('');
        }

        // Show the section
        allocationSection?.classList.remove('hidden');
        showToast(`Preview: ${data.summary?.full_miners || 0} full + ${data.summary?.swing_miners || 0} swing miners`, 'info');

    } catch (error) {
        console.error('Failed to preview power allocation:', error);
        showToast('Error calculating power allocation', 'error');
    }
}

function handleSliderChange(e) {
    const percent = e.target.value;
    const power = (percent / 100) * state.ratedPower;
    $('slider-value').textContent = power.toFixed(1) + ' kW';
    $('power-input').value = power.toFixed(1);
}

function handlePowerInputChange(e) {
    const power = parseFloat(e.target.value) || 0;
    const percent = state.ratedPower > 0 ? (power / state.ratedPower) * 100 : 0;
    $('power-slider').value = Math.min(percent, 100);
    $('slider-value').textContent = power.toFixed(1) + ' kW';
}

async function handleOverrideToggle(e) {
    const enabled = e.target.checked;

    try {
        const response = await fetch(`${CONFIG.apiBase}/override`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                enabled: enabled,
                target_power_kw: enabled ? parseFloat($('power-input').value) || 0 : null,
            }),
        });
        const data = await response.json();

        if (data.success) {
            showToast(enabled ? 'Manual override enabled' : 'Manual override disabled', enabled ? 'warning' : 'success');
        } else {
            showToast(data.message || 'Failed to toggle override', 'error');
            e.target.checked = !enabled;
        }
        await fetchStatus();
    } catch (error) {
        showToast('Error toggling override', 'error');
        e.target.checked = !enabled;
    }
}

// Power Mode Select Functions
function updatePowerModeDisplay(mode) {
    const select = $('power-mode-select');
    if (select) {
        select.value = mode;
    }
}

async function handlePowerModeChange(e) {
    const mode = e.target.value;
    const previousValue = mode === 'frequency' ? 'on_off' : 'frequency';
    
    try {
        const response = await fetch(`${CONFIG.apiBase}/power-mode`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ mode: mode }),
        });
        const data = await response.json();
        
        if (data.success) {
            showToast(`Power mode: ${mode === 'frequency' ? 'Frequency' : 'On/Off'}`, 'success');
        } else {
            showToast(data.message || 'Failed to change power mode', 'error');
            e.target.value = previousValue; // Revert
        }
    } catch (error) {
        showToast('Error changing power mode', 'error');
        e.target.value = previousValue; // Revert
    }
}

async function handleScan() {
    const modal = $('scan-modal');
    const results = $('scan-results');
    const progress = modal.querySelector('.scan-progress');

    modal.classList.remove('hidden');
    results.classList.add('hidden');
    progress.style.display = 'flex';

    try {
        const response = await fetch(`${CONFIG.apiBase}/discovery/scan`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({}),
        });
        const data = await response.json();

        progress.style.display = 'none';
        results.classList.remove('hidden');
        $('scan-count').textContent = data.miners_found || 0;

        state.lastScan = new Date().toLocaleString();
        $('last-scan').textContent = state.lastScan;

        await fetchDiscoveredMiners();
        showToast(`Found ${data.miners_found} miners`, 'success');
    } catch (error) {
        progress.innerHTML = '<span class="error">Scan failed</span>';
        showToast('Network scan failed', 'error');
    }
}

async function handleAddMiner() {
    const ip = $('add-miner-ip').value.trim();
    const power = parseInt($('add-miner-power').value) || 1400;

    if (!ip) {
        showToast('Please enter an IP address', 'error');
        return;
    }

    try {
        const response = await fetch(`${CONFIG.apiBase}/discovery/add`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                ip: ip,
                port: 4028,
                rated_power_watts: power,
            }),
        });
        const data = await response.json();

        if (data.success) {
            showToast(`Added miner at ${ip}`, 'success');
            $('add-miner-ip').value = '';
            await fetchDiscoveredMiners();
        } else {
            showToast(data.error || 'Failed to add miner', 'error');
        }
    } catch (error) {
        showToast('Error adding miner', 'error');
    }
}

async function controlMiner(minerId, action) {
    // Close any open dropdowns
    document.querySelectorAll('.dropdown-menu.show').forEach(m => m.classList.remove('show'));
    
    // Show immediate feedback for longer operations
    const longOps = {
        'reboot': 'Rebooting... (takes ~60-90 seconds)',
        'reset': 'Factory resetting...'
    };
    
    if (longOps[action]) {
        showToast(longOps[action], 'info');
    }
    
    try {
        const response = await fetch(`${CONFIG.apiBase}/miners/${minerId}/control`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ action: action }),
        });
        const data = await response.json();

        if (data.success) {
            showToast(data.message || `${action} command sent`, 'success');
        } else {
            showToast(data.message || `Failed to ${action} miner`, 'error');
        }

        // Longer delay for system-level operations
        const delay = ['reboot', 'reset'].includes(action) ? 5000 : 1000;
        setTimeout(fetchDiscoveredMiners, delay);
    } catch (error) {
        showToast(`Error: ${action} command failed`, 'error');
    }
}

function toggleDropdown(button) {
    const dropdown = button.closest('.dropdown');
    const menu = dropdown.querySelector('.dropdown-menu');
    
    // Close other open dropdowns
    document.querySelectorAll('.dropdown-menu.show').forEach(m => {
        if (m !== menu) m.classList.remove('show');
    });
    
    menu.classList.toggle('show');
    
    // Close on outside click
    const closeHandler = (e) => {
        if (!dropdown.contains(e.target)) {
            menu.classList.remove('show');
            document.removeEventListener('click', closeHandler);
        }
    };
    
    if (menu.classList.contains('show')) {
        setTimeout(() => document.addEventListener('click', closeHandler), 0);
    }
}

async function confirmReset(minerId) {
    // Close dropdown first
    document.querySelectorAll('.dropdown-menu.show').forEach(m => m.classList.remove('show'));
    
    const confirmed = await showConfirm({
        title: '⚠️ Factory Reset',
        message: 'This will clear ALL pool configurations and reset mining settings to defaults.\n\nNetwork settings will NOT be affected.\n\nAre you sure?',
        type: 'danger',
        confirmText: 'Reset',
    });
    if (confirmed) {
        controlMiner(minerId, 'reset');
    }
}

async function removeMiner(minerId) {
    const confirmed = await showConfirm({
        title: 'Remove Miner',
        message: 'Remove this miner from the fleet?',
        type: 'warning',
        confirmText: 'Remove',
    });
    if (!confirmed) return;

    try {
        const response = await fetch(`${CONFIG.apiBase}/discovery/miners/${minerId}`, {
            method: 'DELETE',
        });
        const data = await response.json();

        if (data.success) {
            showToast('Miner removed', 'success');
            await fetchDiscoveredMiners();
        } else {
            showToast('Failed to remove miner', 'error');
        }
    } catch (error) {
        showToast('Error removing miner', 'error');
    }
}

// =========================================================================
// View Controls
// =========================================================================

function setViewMode(mode) {
    state.viewMode = mode;

    $('view-grid').classList.toggle('active', mode === 'grid');
    $('view-table').classList.toggle('active', mode === 'table');
    $('view-rack').classList.toggle('active', mode === 'rack');

    $('miners-grid').classList.toggle('hidden', mode !== 'grid');
    $('miners-table-container').classList.toggle('hidden', mode !== 'table');
    $('miners-rack').classList.toggle('hidden', mode !== 'rack');

    updateMinersDisplay();
}

function toggleHistory() {
    const section = $('history-section');
    section.classList.toggle('collapsed');
}

// =========================================================================
// Utilities
// =========================================================================

function formatUptime(seconds) {
    if (!seconds) return '--';

    const days = Math.floor(seconds / 86400);
    const hours = Math.floor((seconds % 86400) / 3600);
    const mins = Math.floor((seconds % 3600) / 60);

    if (days > 0) return `${days}d ${hours}h`;
    if (hours > 0) return `${hours}h ${mins}m`;
    return `${mins}m`;
}

function extractPoolName(url) {
    if (!url) return '--';
    try {
        const match = url.match(/\/\/([^:\/]+)/);
        if (match) {
            const host = match[1];
            // Extract main domain part
            const parts = host.split('.');
            if (parts.length >= 2) {
                return parts[parts.length - 2];
            }
            return host;
        }
    } catch (e) {}
    return '--';
}

function getTempClass(temp) {
    if (!temp || temp <= 0) return '';
    if (temp >= 90) return 'temp-hot';
    if (temp >= 80) return 'temp-warm';
    return 'temp-ok';
}

function escapeHtml(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function showToast(message, type = 'info') {
    const container = $('toast-container');
    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    toast.innerHTML = `
        <span class="toast-icon">${type === 'success' ? '✓' : type === 'error' ? '✗' : type === 'warning' ? '⚠' : 'ℹ'}</span>
        <span class="toast-message">${message}</span>
    `;

    container.appendChild(toast);

    setTimeout(() => {
        toast.classList.add('fade-out');
        setTimeout(() => toast.remove(), 300);
    }, 3000);
}

// Custom confirm modal
function showConfirm(options) {
    return new Promise((resolve) => {
        const modal = $('confirm-modal');
        const iconEl = $('confirm-modal-icon');
        const titleEl = $('confirm-modal-title');
        const messageEl = $('confirm-modal-message');
        const cancelBtn = $('confirm-modal-cancel');
        const confirmBtn = $('confirm-modal-confirm');

        // Set content
        titleEl.textContent = options.title || 'Confirm Action';
        messageEl.textContent = options.message || 'Are you sure you want to proceed?';
        
        // Set icon style based on type
        const type = options.type || 'warning';
        iconEl.className = 'confirm-modal-icon ' + (type === 'danger' ? 'danger' : type === 'info' ? 'info' : '');
        
        // Set icon SVG based on type
        if (type === 'danger') {
            iconEl.innerHTML = `
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/>
                    <line x1="12" y1="9" x2="12" y2="13"/>
                    <line x1="12" y1="17" x2="12.01" y2="17"/>
                </svg>
            `;
        } else {
            iconEl.innerHTML = `
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <circle cx="12" cy="12" r="10"/>
                    <line x1="12" y1="8" x2="12" y2="12"/>
                    <line x1="12" y1="16" x2="12.01" y2="16"/>
                </svg>
            `;
        }

        // Set button text and style
        cancelBtn.textContent = options.cancelText || 'Cancel';
        confirmBtn.textContent = options.confirmText || 'Confirm';
        confirmBtn.className = 'btn ' + (type === 'danger' ? 'btn-danger' : 'btn-primary');

        // Show modal
        modal.classList.remove('hidden');

        // Cleanup function
        const cleanup = () => {
            modal.classList.add('hidden');
            cancelBtn.removeEventListener('click', handleCancel);
            confirmBtn.removeEventListener('click', handleConfirm);
            modal.removeEventListener('click', handleBackdrop);
            document.removeEventListener('keydown', handleKeydown);
        };

        const handleCancel = () => {
            cleanup();
            resolve(false);
        };

        const handleConfirm = () => {
            cleanup();
            resolve(true);
        };

        const handleBackdrop = (e) => {
            if (e.target === modal) {
                handleCancel();
            }
        };

        const handleKeydown = (e) => {
            if (e.key === 'Escape') {
                handleCancel();
            } else if (e.key === 'Enter') {
                handleConfirm();
            }
        };

        cancelBtn.addEventListener('click', handleCancel);
        confirmBtn.addEventListener('click', handleConfirm);
        modal.addEventListener('click', handleBackdrop);
        document.addEventListener('keydown', handleKeydown);

        // Focus confirm button
        confirmBtn.focus();
    });
}

// Make functions globally available
window.controlMiner = controlMiner;
window.removeMiner = removeMiner;
window.openMinerModal = openMinerModal;
window.blinkMiner = blinkMiner;
window.toggleFindMiner = toggleFindMiner;
window.toggleDropdown = toggleDropdown;
window.confirmReset = confirmReset;

// Toggle miner find mode (LED blinking)
async function toggleFindMiner(minerIp) {
    try {
        const response = await fetch(`${CONFIG.apiBase}/miner/${minerIp}/blink`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
        });
        const data = await response.json();
        if (data.success) {
            const stateText = data.is_enabled ? 'ON' : 'OFF';
            showToast(`Find mode ${stateText}`, data.is_enabled ? 'success' : 'info');
        } else {
            showToast(data.error || 'Find mode not supported on this miner', 'warning');
        }
    } catch (error) {
        showToast('Failed to toggle find mode', 'error');
    }
}

// Legacy blink function (for backwards compatibility)
async function blinkMiner(minerIp) {
    return toggleFindMiner(minerIp);
}

// =========================================================================
// Pool Configuration Handlers
// =========================================================================

async function handleUpdatePoolSingle() {
    const minerIP = getSelectedMinerIP();
    const poolUrl = $('pool-url').value.trim();
    const worker = $('pool-worker').value.trim();
    const password = $('pool-password').value.trim() || 'x';

    if (!poolUrl) {
        showToast('Please enter a pool URL', 'error');
        return;
    }
    if (!worker) {
        showToast('Please enter a worker name', 'error');
        return;
    }

    try {
        showToast(`Updating pool on ${minerIP}...`, 'info');
        const response = await fetch(`${CONFIG.apiBase}/pool/update`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                miner_ip: minerIP,
                pool_url: poolUrl,
                worker: worker,
                password: password,
            }),
        });
        const data = await response.json();

        if (data.success) {
            showToast(`Pool updated on ${minerIP}`, 'success');
        } else {
            showToast(data.error || 'Failed to update pool', 'error');
        }
    } catch (error) {
        showToast('Error updating pool', 'error');
    }
}

async function handleUpdatePoolAll() {
    const poolUrl = $('pool-url').value.trim();
    const worker = $('pool-worker').value.trim();
    const password = $('pool-password').value.trim() || 'x';

    if (!poolUrl) {
        showToast('Please enter a pool URL', 'error');
        return;
    }
    if (!worker) {
        showToast('Please enter a worker name', 'error');
        return;
    }

    const confirmed = await showConfirm({
        title: 'Update All Pools',
        message: `Update pool settings on ALL ${state.discoveredMiners.length} miners?`,
        type: 'info',
        confirmText: 'Update All',
    });
    if (!confirmed) return;

    showToast('Updating pool on all miners...', 'info');

    let successCount = 0;
    let failCount = 0;

    for (const miner of state.discoveredMiners) {
        try {
            const response = await fetch(`${CONFIG.apiBase}/pool/update`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    miner_ip: miner.ip,
                    pool_url: poolUrl,
                    worker: worker,
                    password: password,
                }),
            });
            const data = await response.json();

            if (data.success) {
                successCount++;
            } else {
                failCount++;
            }
        } catch (error) {
            failCount++;
        }
    }

    showToast(`Pool updated: ${successCount} success, ${failCount} failed`, successCount > 0 ? 'success' : 'error');
}

// =========================================================================
// Anomaly Detection System
// =========================================================================

let anomalies = [];
let anomalyStats = {
    critical: 0,
    warning: 0,
    info: 0,
    fleetAvailability: 100,
    maxSustainedPower: 0,
    totalDowntime: 0,
    errorRate: 0,
};

function addAnomaly(type, message, minerIp = null) {
    const anomaly = {
        id: Date.now(),
        type: type, // 'critical', 'warning', 'info'
        message: message,
        minerIp: minerIp,
        timestamp: new Date(),
    };

    anomalies.unshift(anomaly);
    if (anomalies.length > 100) anomalies.pop(); // Keep last 100

    anomalyStats[type]++;

    updateAnomalyDisplay();
    saveAnomalies();

    // Show toast for critical
    if (type === 'critical') {
        showToast(`🚨 ${message}`, 'error');
    }
}

function clearAnomalies() {
    anomalies = [];
    anomalyStats = { critical: 0, warning: 0, info: 0, fleetAvailability: 100, maxSustainedPower: 0, totalDowntime: 0, errorRate: 0 };
    updateAnomalyDisplay();
    saveAnomalies();
}

function updateAnomalyDisplay() {
    // Update counts
    const criticalEl = $('anomaly-critical');
    const warningEl = $('anomaly-warning');
    const infoEl = $('anomaly-info');

    if (criticalEl) criticalEl.textContent = `${anomalyStats.critical} Critical`;
    if (warningEl) warningEl.textContent = `${anomalyStats.warning} Warnings`;
    if (infoEl) infoEl.textContent = `${anomalyStats.info} Info`;

    // Update stats
    const availEl = $('fleet-availability');
    const powerEl = $('max-sustained-power');
    const downtimeEl = $('total-downtime');

    if (availEl) availEl.textContent = anomalyStats.fleetAvailability.toFixed(1) + '%';
    if (powerEl) powerEl.textContent = anomalyStats.maxSustainedPower.toFixed(1) + ' kW';
    if (downtimeEl) downtimeEl.textContent = formatDowntime(anomalyStats.totalDowntime);

    // Update list
    const list = $('anomaly-list');
    if (!list) return;

    if (anomalies.length === 0) {
        list.innerHTML = '<div class="anomaly-empty">No anomalies detected - Fleet operating normally</div>';
        return;
    }

    list.innerHTML = anomalies
        .slice(0, 50)
        .map(
            (a) => `
        <div class="anomaly-item ${a.type}">
            <svg class="anomaly-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                ${
                    a.type === 'critical'
                        ? '<circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/>'
                        : a.type === 'warning'
                        ? '<path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/>'
                        : '<circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/>'
                }
            </svg>
            <div class="anomaly-details">
                <div class="anomaly-message">${escapeHtml(a.message)}</div>
                <div class="anomaly-meta">
                    ${a.minerIp ? `<span class="anomaly-miner">${a.minerIp}</span>` : ''}
                    <span class="anomaly-time">${formatAnomalyTime(a.timestamp)}</span>
                </div>
            </div>
        </div>
    `,
        )
        .join('');
}

function formatDowntime(minutes) {
    if (minutes < 60) return `${Math.round(minutes)}m`;
    const hours = Math.floor(minutes / 60);
    const mins = Math.round(minutes % 60);
    return `${hours}h ${mins}m`;
}

function formatAnomalyTime(timestamp) {
    const date = new Date(timestamp);
    return date.toLocaleString();
}

function checkForAnomalies() {
    // Delegate to the new fleet-level anomaly detection
    runFleetAnomalyDetection();
}

// =========================================================================
// Sophisticated Fleet-Level Anomaly Detection
// =========================================================================

let fleetMetrics = {
    hashrateHistory: [], // Track fleet hashrate over time for variance
    tempHistory: [], // Track temps for spread calculation
    powerHistory: [], // Track power
    lastUpdate: 0,
};

function runFleetAnomalyDetection() {
    if (state.discoveredMiners.length === 0) return;

    const now = Date.now();
    let onlineCount = 0;
    let miningCount = 0;
    let totalPower = 0;
    let totalHashrate = 0;
    let temperatures = [];
    let hashrates = [];
    let efficiencies = [];

    // Collect data from all miners
    state.discoveredMiners.forEach((miner) => {
        if (!miner.is_online) {
            // Offline miner detection
            const recentOffline = anomalies.find((a) => a.minerIp === miner.ip && a.message.includes('went offline') && now - new Date(a.timestamp).getTime() < 300000);
            if (!recentOffline) {
                addAnomaly('critical', `Miner went offline`, miner.ip);
            }
        } else {
            onlineCount++;
            const temp = miner.temperature_c || miner.temp_chip || 0;
            const hashrate = parseFloat(miner.hashrate_ghs) || parseFloat(miner.hashrate) || 0;
            const power = miner.power_kw > 0 ? miner.power_kw : miner.power_w ? miner.power_w / 1000 : miner.rated_power_kw || 0;

            if (temp > 0) temperatures.push({ ip: miner.ip, temp });
            if (hashrate > 0) hashrates.push({ ip: miner.ip, hashrate });
            if (hashrate > 0 && power > 0) {
                efficiencies.push({ ip: miner.ip, efficiency: hashrate / 1000 / power }); // TH/kW
            }

            // Individual miner checks
            if (temp >= 95) {
                const recent = anomalies.find((a) => a.minerIp === miner.ip && a.message.includes('temperature') && now - new Date(a.timestamp).getTime() < 300000);
                if (!recent) addAnomaly('critical', `Critical temperature: ${temp.toFixed(0)}°C`, miner.ip);
            } else if (temp >= 85) {
                const recent = anomalies.find((a) => a.minerIp === miner.ip && a.message.includes('temperature') && now - new Date(a.timestamp).getTime() < 600000);
                if (!recent) addAnomaly('warning', `High temperature: ${temp.toFixed(0)}°C`, miner.ip);
            }

            if (miner.is_mining) {
                miningCount++;
                totalPower += power;
                totalHashrate += hashrate;
            }
        }
    });

    const totalMiners = state.discoveredMiners.length;

    // ========== FLEET-LEVEL ANOMALIES ==========

    // 1. Fleet Availability Check
    const availability = totalMiners > 0 ? (onlineCount / totalMiners) * 100 : 0;
    if (availability < 80 && onlineCount > 0) {
        const recent = anomalies.find((a) => a.message.includes('Fleet availability') && now - new Date(a.timestamp).getTime() < 600000);
        if (!recent) addAnomaly('critical', `Fleet availability dropped to ${availability.toFixed(0)}% (${totalMiners - onlineCount} miners offline)`);
    } else if (availability < 95 && onlineCount > 0) {
        const recent = anomalies.find((a) => a.message.includes('Fleet availability') && now - new Date(a.timestamp).getTime() < 600000);
        if (!recent) addAnomaly('warning', `Fleet availability at ${availability.toFixed(0)}% (${totalMiners - onlineCount} miners offline)`);
    }

    // 2. Temperature Spread Analysis (detect cooling issues)
    if (temperatures.length >= 2) {
        const temps = temperatures.map((t) => t.temp);
        const avgTemp = temps.reduce((a, b) => a + b, 0) / temps.length;
        const maxTemp = Math.max(...temps);
        const minTemp = Math.min(...temps);
        const tempSpread = maxTemp - minTemp;

        // Update display
        const spreadEl = $('temp-spread');
        if (spreadEl) spreadEl.textContent = `${tempSpread.toFixed(0)}°C`;

        // Large spread indicates potential cooling problems
        if (tempSpread > 20) {
            const hotMiner = temperatures.find((t) => t.temp === maxTemp);
            const recent = anomalies.find((a) => a.message.includes('Temperature spread') && now - new Date(a.timestamp).getTime() < 600000);
            if (!recent) addAnomaly('warning', `Temperature spread too high: ${tempSpread.toFixed(0)}°C (hottest: ${hotMiner?.ip})`);
        }

        // Fleet-wide overheating
        if (avgTemp > 78) {
            const recent = anomalies.find((a) => a.message.includes('Fleet average temperature') && now - new Date(a.timestamp).getTime() < 600000);
            if (!recent) addAnomaly('critical', `Fleet average temperature critical: ${avgTemp.toFixed(0)}°C`);
        }
    }

    // 3. Hashrate Variance Analysis (detect underperforming miners)
    if (hashrates.length >= 2) {
        const rates = hashrates.map((h) => h.hashrate);
        const avgHashrate = rates.reduce((a, b) => a + b, 0) / rates.length;
        const variance = rates.reduce((sum, h) => sum + Math.pow(h - avgHashrate, 2), 0) / rates.length;
        const stdDev = Math.sqrt(variance);
        const coefficientOfVariation = avgHashrate > 0 ? (stdDev / avgHashrate) * 100 : 0;

        // Update display
        const varianceEl = $('hashrate-variance');
        if (varianceEl) varianceEl.textContent = `±${coefficientOfVariation.toFixed(1)}%`;

        // High variance means some miners underperforming
        if (coefficientOfVariation > 25) {
            // Find underperformers (more than 1.5 std dev below mean)
            const underperformers = hashrates.filter((h) => h.hashrate < avgHashrate - 1.5 * stdDev);
            if (underperformers.length > 0) {
                const recent = anomalies.find((a) => a.message.includes('Hashrate variance') && now - new Date(a.timestamp).getTime() < 600000);
                if (!recent) {
                    const ips = underperformers
                        .slice(0, 3)
                        .map((u) => u.ip)
                        .join(', ');
                    addAnomaly('warning', `Hashrate variance high (±${coefficientOfVariation.toFixed(0)}%). Underperformers: ${ips}`);
                }
            }
        }

        // Sudden fleet-wide hashrate drop
        fleetMetrics.hashrateHistory.push({ time: now, value: totalHashrate });
        if (fleetMetrics.hashrateHistory.length > 60) fleetMetrics.hashrateHistory.shift(); // Keep 5 min at 5s intervals

        if (fleetMetrics.hashrateHistory.length >= 10) {
            const recentAvg = fleetMetrics.hashrateHistory.slice(-5).reduce((s, h) => s + h.value, 0) / 5;
            const olderAvg = fleetMetrics.hashrateHistory.slice(-10, -5).reduce((s, h) => s + h.value, 0) / 5;
            if (olderAvg > 0 && recentAvg < olderAvg * 0.7) {
                const drop = (((olderAvg - recentAvg) / olderAvg) * 100).toFixed(0);
                const recent = anomalies.find((a) => a.message.includes('Fleet hashrate dropped') && now - new Date(a.timestamp).getTime() < 300000);
                if (!recent) addAnomaly('critical', `Fleet hashrate dropped ${drop}% in the last minute`);
            }
        }
    }

    // 4. Efficiency Analysis
    if (efficiencies.length >= 2) {
        const effs = efficiencies.map((e) => e.efficiency);
        const avgEfficiency = effs.reduce((a, b) => a + b, 0) / effs.length;

        // Update display
        const effEl = $('efficiency-score');
        if (effEl) effEl.textContent = avgEfficiency.toFixed(1) + ' TH/kW';

        // Find inefficient miners (20% below average)
        const inefficient = efficiencies.filter((e) => e.efficiency < avgEfficiency * 0.8);
        if (inefficient.length > 0 && inefficient.length >= Math.ceil(efficiencies.length * 0.2)) {
            const recent = anomalies.find((a) => a.message.includes('efficiency') && now - new Date(a.timestamp).getTime() < 900000);
            if (!recent) {
                addAnomaly('info', `${inefficient.length} miners running below average efficiency`);
            }
        }
    }

    // 5. Power Utilization Pattern
    fleetMetrics.powerHistory.push({ time: now, value: totalPower });
    if (fleetMetrics.powerHistory.length > 60) fleetMetrics.powerHistory.shift();

    // Sudden power spike/drop
    if (fleetMetrics.powerHistory.length >= 5) {
        const recentPower = fleetMetrics.powerHistory.slice(-3).reduce((s, p) => s + p.value, 0) / 3;
        const olderPower = fleetMetrics.powerHistory.slice(-6, -3).reduce((s, p) => s + p.value, 0) / 3;
        if (olderPower > 0.5 && Math.abs(recentPower - olderPower) / olderPower > 0.3) {
            const change = (((recentPower - olderPower) / olderPower) * 100).toFixed(0);
            const recent = anomalies.find((a) => a.message.includes('Power consumption') && now - new Date(a.timestamp).getTime() < 300000);
            if (!recent) {
                const direction = recentPower > olderPower ? 'spiked' : 'dropped';
                addAnomaly('warning', `Power consumption ${direction} ${Math.abs(change)}%`);
            }
        }
    }

    // ========== UPDATE STATS ==========
    anomalyStats.fleetAvailability = availability;
    anomalyStats.maxSustainedPower = Math.max(anomalyStats.maxSustainedPower, totalPower);

    // Error rate (anomalies per hour)
    const oneHourAgo = now - 3600000;
    const recentAnomalies = anomalies.filter((a) => new Date(a.timestamp).getTime() > oneHourAgo);
    anomalyStats.errorRate = recentAnomalies.length;

    // Update error rate display
    const errorRateEl = $('error-rate');
    if (errorRateEl) errorRateEl.textContent = recentAnomalies.length + '/hr';

    updateAnomalyDisplay();
    fleetMetrics.lastUpdate = now;
}

function saveAnomalies() {
    try {
        localStorage.setItem('net-stabilization-anomalies', JSON.stringify(anomalies));
        localStorage.setItem('net-stabilization-anomaly-stats', JSON.stringify(anomalyStats));
    } catch (e) {
        console.warn('Could not save anomalies to localStorage');
    }
}

function loadAnomalies() {
    try {
        const saved = localStorage.getItem('net-stabilization-anomalies');
        const savedStats = localStorage.getItem('net-stabilization-anomaly-stats');
        if (saved) {
            anomalies = JSON.parse(saved);
        }
        if (savedStats) {
            anomalyStats = { ...anomalyStats, ...JSON.parse(savedStats) };
        }
        updateAnomalyDisplay();
    } catch (e) {
        console.warn('Could not load anomalies from localStorage');
    }
}

// =========================================================================
// Historical Graphs with Chart.js
// =========================================================================

let charts = {};
let historyData = {
    hashrate: [],
    power: [],
    temp: [],
    // Per-miner history (keyed by IP)
    miners: {},
};
let currentTimeScope = '1h';

function initCharts() {
    if (typeof Chart === 'undefined') {
        console.warn('Chart.js not loaded');
        return;
    }

    const baseChartOptions = {
        responsive: true,
        maintainAspectRatio: false,
        animation: { duration: 300 },
        interaction: {
            intersect: false,
            mode: 'index',
        },
        plugins: {
            legend: { display: false },
            tooltip: {
                backgroundColor: 'rgba(15, 20, 25, 0.95)',
                titleColor: '#e6edf3',
                bodyColor: '#8b949e',
                borderColor: 'rgba(255, 255, 255, 0.1)',
                borderWidth: 1,
                padding: 12,
                displayColors: false,
            },
        },
        scales: {
            x: {
                type: 'time',
                time: {
                    displayFormats: {
                        minute: 'HH:mm',
                        hour: 'HH:mm',
                        day: 'MMM d',
                    },
                    tooltipFormat: 'MMM d, HH:mm:ss',
                },
                grid: {
                    color: 'rgba(255,255,255,0.06)',
                    drawBorder: false,
                },
                ticks: {
                    color: '#8b949e',
                    maxRotation: 0,
                    autoSkip: true,
                    maxTicksLimit: 6,
                },
            },
            y: {
                grid: {
                    color: 'rgba(255,255,255,0.06)',
                    drawBorder: false,
                },
                ticks: {
                    color: '#8b949e',
                    padding: 8,
                },
                beginAtZero: true,
            },
        },
    };

    // Hashrate chart with TH/s units
    const hashrateCtx = $('hashrate-chart')?.getContext('2d');
    if (hashrateCtx) {
        charts.hashrate = new Chart(hashrateCtx, {
            type: 'line',
            data: {
                datasets: [
                    {
                        data: [],
                        borderColor: '#58a6ff',
                        backgroundColor: 'rgba(88, 166, 255, 0.1)',
                        fill: true,
                        tension: 0.4,
                        pointRadius: 0,
                        borderWidth: 2,
                    },
                ],
            },
            options: {
                ...baseChartOptions,
                scales: {
                    ...baseChartOptions.scales,
                    y: {
                        ...baseChartOptions.scales.y,
                        ticks: {
                            ...baseChartOptions.scales.y.ticks,
                            callback: (value) => `${value.toFixed(1)} TH/s`,
                        },
                    },
                },
                plugins: {
                    ...baseChartOptions.plugins,
                    tooltip: {
                        ...baseChartOptions.plugins.tooltip,
                        callbacks: {
                            label: (ctx) => `Hashrate: ${ctx.parsed.y.toFixed(2)} TH/s`,
                        },
                    },
                },
            },
        });
    }

    // Power chart with kW units
    const powerCtx = $('power-chart')?.getContext('2d');
    if (powerCtx) {
        charts.power = new Chart(powerCtx, {
            type: 'line',
            data: {
                datasets: [
                    {
                        data: [],
                        borderColor: '#d29922',
                        backgroundColor: 'rgba(210, 153, 34, 0.1)',
                        fill: true,
                        tension: 0.4,
                        pointRadius: 0,
                        borderWidth: 2,
                    },
                ],
            },
            options: {
                ...baseChartOptions,
                scales: {
                    ...baseChartOptions.scales,
                    y: {
                        ...baseChartOptions.scales.y,
                        ticks: {
                            ...baseChartOptions.scales.y.ticks,
                            callback: (value) => `${value.toFixed(2)} kW`,
                        },
                    },
                },
                plugins: {
                    ...baseChartOptions.plugins,
                    tooltip: {
                        ...baseChartOptions.plugins.tooltip,
                        callbacks: {
                            label: (ctx) => `Power: ${ctx.parsed.y.toFixed(3)} kW`,
                        },
                    },
                },
            },
        });
    }

    // Temperature chart with °C units
    const tempCtx = $('temp-chart')?.getContext('2d');
    if (tempCtx) {
        charts.temp = new Chart(tempCtx, {
            type: 'line',
            data: {
                datasets: [
                    {
                        data: [],
                        borderColor: '#f85149',
                        backgroundColor: 'rgba(248, 81, 73, 0.1)',
                        fill: true,
                        tension: 0.4,
                        pointRadius: 0,
                        borderWidth: 2,
                    },
                ],
            },
            options: {
                ...baseChartOptions,
                scales: {
                    ...baseChartOptions.scales,
                    y: {
                        ...baseChartOptions.scales.y,
                        suggestedMin: 20,
                        suggestedMax: 100,
                        ticks: {
                            ...baseChartOptions.scales.y.ticks,
                            callback: (value) => `${value}°C`,
                        },
                    },
                },
                plugins: {
                    ...baseChartOptions.plugins,
                    tooltip: {
                        ...baseChartOptions.plugins.tooltip,
                        callbacks: {
                            label: (ctx) => `Temperature: ${ctx.parsed.y.toFixed(1)}°C`,
                        },
                    },
                },
            },
        });
    }
}

function recordHistoryPoint() {
    const now = new Date();

    let totalHashrate = 0;
    let totalTemp = 0;
    let tempCount = 0;

    // Use backend's calculated active power DIRECTLY for graph consistency
    // This ensures graph matches the status API display
    const totalPower = state.status?.active_power_kw || 0;

    state.discoveredMiners.forEach((m) => {
        const hashrateGhs = parseFloat(m.hashrate_ghs) || parseFloat(m.hashrate) || 0;
        const tempC = m.temperature_c || m.temp_chip || m.temp || 0;

        totalHashrate += hashrateGhs;
        if (tempC > 0) {
            totalTemp += tempC;
            tempCount++;
        }

        // Record per-miner history
        if (m.ip) {
            if (!historyData.miners[m.ip]) {
                historyData.miners[m.ip] = { hashrate: [], power: [], temp: [] };
            }
            const minerPowerKw = m.power_kw || 0;
            historyData.miners[m.ip].hashrate.push({ x: now, y: hashrateGhs / 1000 }); // TH/s
            historyData.miners[m.ip].power.push({ x: now, y: minerPowerKw });
            historyData.miners[m.ip].temp.push({ x: now, y: tempC });
        }
    });

    const avgTemp = tempCount > 0 ? totalTemp / tempCount : 0;

    historyData.hashrate.push({ x: now, y: totalHashrate / 1000 }); // Convert to TH/s
    historyData.power.push({ x: now, y: totalPower });
    historyData.temp.push({ x: now, y: avgTemp });

    // Keep data based on time scope - store up to 30 days
    const maxAge = 30 * 24 * 60 * 60 * 1000;
    const cutoff = Date.now() - maxAge;

    historyData.hashrate = historyData.hashrate.filter((p) => p.x.getTime() > cutoff);
    historyData.power = historyData.power.filter((p) => p.x.getTime() > cutoff);
    historyData.temp = historyData.temp.filter((p) => p.x.getTime() > cutoff);

    // Trim per-miner history
    Object.keys(historyData.miners).forEach((ip) => {
        historyData.miners[ip].hashrate = historyData.miners[ip].hashrate.filter((p) => p.x.getTime() > cutoff);
        historyData.miners[ip].power = historyData.miners[ip].power.filter((p) => p.x.getTime() > cutoff);
        historyData.miners[ip].temp = historyData.miners[ip].temp.filter((p) => p.x.getTime() > cutoff);
    });

    updateCharts();
    saveHistory();

    // Update current values
    $('graph-hashrate-current').textContent = (totalHashrate / 1000).toFixed(2) + ' TH/s';
    $('graph-power-current').textContent = totalPower.toFixed(2) + ' kW';
    $('graph-temp-current').textContent = avgTemp > 0 ? avgTemp.toFixed(0) + ' °C' : '-- °C';

    // Run fleet anomaly detection
    runFleetAnomalyDetection();
}

// Time scope definitions
const TIME_SCOPES = {
    '5m': 5 * 60 * 1000,
    '15m': 15 * 60 * 1000,
    '1h': 60 * 60 * 1000,
    '6h': 6 * 60 * 60 * 1000,
    '24h': 24 * 60 * 60 * 1000,
};

function updateCharts() {
    const now = Date.now();
    const scopeDuration = TIME_SCOPES[currentTimeScope] || TIME_SCOPES['5m'];
    const cutoff = now - scopeDuration;

    // Calculate time bounds for the selected scope
    const minTime = new Date(cutoff);
    const maxTime = new Date(now);

    // Get data source based on selected miner
    let dataSource;
    if (selectedGraphMiner && selectedGraphMiner !== 'fleet' && historyData.miners[selectedGraphMiner]) {
        dataSource = historyData.miners[selectedGraphMiner];
    } else {
        dataSource = historyData;
    }

    const filteredHashrate = (dataSource.hashrate || []).filter((p) => p.x.getTime() > cutoff);
    const filteredPower = (dataSource.power || []).filter((p) => p.x.getTime() > cutoff);
    const filteredTemp = (dataSource.temp || []).filter((p) => p.x.getTime() > cutoff);

    // Update empty hints based on data availability
    updateGraphEmptyHints(filteredHashrate.length, filteredPower.length, filteredTemp.length);

    // Update each chart with data and explicit time bounds
    if (charts.hashrate) {
        charts.hashrate.data.datasets[0].data = filteredHashrate;
        charts.hashrate.options.scales.x.min = minTime;
        charts.hashrate.options.scales.x.max = maxTime;
        charts.hashrate.update('none');
    }

    if (charts.power) {
        charts.power.data.datasets[0].data = filteredPower;
        charts.power.options.scales.x.min = minTime;
        charts.power.options.scales.x.max = maxTime;
        charts.power.update('none');
    }

    if (charts.temp) {
        charts.temp.data.datasets[0].data = filteredTemp;
        charts.temp.options.scales.x.min = minTime;
        charts.temp.options.scales.x.max = maxTime;
        charts.temp.update('none');
    }
}

function updateGraphEmptyHints(hashrateCount, powerCount, tempCount) {
    const hashrateHint = $('hashrate-empty');
    const powerHint = $('power-empty');
    const tempHint = $('temp-empty');

    if (hashrateHint) hashrateHint.classList.toggle('hidden', hashrateCount > 0);
    if (powerHint) powerHint.classList.toggle('hidden', powerCount > 0);
    if (tempHint) tempHint.classList.toggle('hidden', tempCount > 0);
}

// Auto-select best time scope based on available data
function autoSelectTimeScope() {
    const dataLength = historyData.hashrate.length;
    if (dataLength === 0) {
        // No data, default to 5m to show data as soon as it comes in
        setTimeScope('5m');
        return;
    }

    // Find oldest data point
    const oldestTime = historyData.hashrate[0]?.x?.getTime() || Date.now();
    const dataAge = Date.now() - oldestTime;

    // Select appropriate scope based on data age
    if (dataAge > 6 * 60 * 60 * 1000) {
        setTimeScope('24h');
    } else if (dataAge > 60 * 60 * 1000) {
        setTimeScope('6h');
    } else if (dataAge > 15 * 60 * 1000) {
        setTimeScope('1h');
    } else if (dataAge > 5 * 60 * 1000) {
        setTimeScope('15m');
    } else {
        setTimeScope('5m');
    }
}

function setTimeScope(scope) {
    currentTimeScope = scope;
    document.querySelectorAll('.scope-btn').forEach((btn) => {
        btn.classList.toggle('active', btn.dataset.scope === scope);
    });
    updateCharts();
}

// Track currently selected miner for charts
let selectedGraphMiner = 'fleet';

function handleGraphMinerChange() {
    const select = $('graph-miner-select');
    if (select) {
        selectedGraphMiner = select.value;
        updateCharts();
        // Update current values display
        if (selectedGraphMiner === 'fleet') {
            updateGraphCurrentValues();
        } else {
            updateGraphCurrentValuesForMiner(selectedGraphMiner);
        }
    }
}

function updateGraphCurrentValuesForMiner(minerIp) {
    const miner = state.discoveredMiners.find((m) => m.ip === minerIp);
    if (miner) {
        const hashrate = miner.hashrate || 0;
        const power = miner.power_w ? miner.power_w / 1000 : 0;
        const temp = miner.temp_chip || miner.temp || 0;

        $('graph-hashrate-current').textContent = (hashrate / 1000).toFixed(2) + ' TH/s';
        $('graph-power-current').textContent = power.toFixed(2) + ' kW';
        $('graph-temp-current').textContent = temp > 0 ? temp.toFixed(0) + ' °C' : '-- °C';
    }
}

function updateGraphCurrentValues() {
    // Sum all fleet values
    let totalHashrate = 0;
    let totalPower = 0;
    let totalTemp = 0;
    let tempCount = 0;

    state.discoveredMiners.forEach((m) => {
        if (m.is_online) {
            totalHashrate += m.hashrate || 0;
            totalPower += m.power_w ? m.power_w / 1000 : 0;
            if (m.temp_chip || m.temp) {
                totalTemp += m.temp_chip || m.temp;
                tempCount++;
            }
        }
    });

    const avgTemp = tempCount > 0 ? totalTemp / tempCount : 0;

    $('graph-hashrate-current').textContent = (totalHashrate / 1000).toFixed(2) + ' TH/s';
    $('graph-power-current').textContent = totalPower.toFixed(2) + ' kW';
    $('graph-temp-current').textContent = avgTemp > 0 ? avgTemp.toFixed(0) + ' °C' : '-- °C';
}

function saveHistory() {
    try {
        // Only save last 24 hours to localStorage (to keep it manageable)
        const cutoff = Date.now() - 24 * 60 * 60 * 1000;
        const toSave = {
            hashrate: historyData.hashrate.filter((p) => p.x.getTime() > cutoff),
            power: historyData.power.filter((p) => p.x.getTime() > cutoff),
            temp: historyData.temp.filter((p) => p.x.getTime() > cutoff),
        };
        localStorage.setItem('net-stabilization-history', JSON.stringify(toSave));
    } catch (e) {
        console.warn('Could not save history to localStorage');
    }
}

function loadHistory() {
    try {
        const saved = localStorage.getItem('net-stabilization-history');
        if (saved) {
            const parsed = JSON.parse(saved);
            historyData.hashrate = (parsed.hashrate || []).map((p) => ({ x: new Date(p.x), y: p.y }));
            historyData.power = (parsed.power || []).map((p) => ({ x: new Date(p.x), y: p.y }));
            historyData.temp = (parsed.temp || []).map((p) => ({ x: new Date(p.x), y: p.y }));
        }
    } catch (e) {
        console.warn('Could not load history from localStorage');
    }
}

// =========================================================================
// Miner Detail Modal
// =========================================================================

let currentModalMiner = null;
let currentMinerDetails = null;
let modalCharts = {
    hashrate: null,
    temp: null,
    power: null,
};
let modalChartData = {
    hashrate: [],
    temp: [],
    power: [],
    timestamps: [],
    // Full data with timestamps for filtering
    fullData: {
        hashrate: [],
        temp: [],
        power: [],
    },
};
let modalTimeScope = '5m'; // Default modal time scope

// Set modal chart time scope
function setModalTimeScope(scope) {
    modalTimeScope = scope;
    document.querySelectorAll('.modal-scope-btn').forEach((btn) => {
        btn.classList.toggle('active', btn.dataset.scope === scope);
    });
    updateModalChartsWithScope();
}

// Update modal charts based on time scope
function updateModalChartsWithScope() {
    const scopeMs = {
        '5m': 5 * 60 * 1000,
        '15m': 15 * 60 * 1000,
        '1h': 60 * 60 * 1000,
        '6h': 6 * 60 * 60 * 1000,
    };

    const now = Date.now();
    const scopeDuration = scopeMs[modalTimeScope] || scopeMs['5m'];
    const cutoff = now - scopeDuration;

    // Calculate time bounds for the selected scope
    const minTime = new Date(cutoff);
    const maxTime = new Date(now);

    // Filter data based on scope
    const filteredHashrate = modalChartData.fullData.hashrate.filter((p) => p.x > cutoff);
    const filteredTemp = modalChartData.fullData.temp.filter((p) => p.x > cutoff);
    const filteredPower = modalChartData.fullData.power.filter((p) => p.x > cutoff);

    // Update charts with time-based data
    if (modalCharts.hashrate) {
        modalCharts.hashrate.data.datasets[0].data = filteredHashrate.map((p) => ({ x: new Date(p.x), y: p.y }));
        modalCharts.hashrate.options.scales.x.min = minTime;
        modalCharts.hashrate.options.scales.x.max = maxTime;
        modalCharts.hashrate.update('none');
    }
    if (modalCharts.temp) {
        modalCharts.temp.data.datasets[0].data = filteredTemp.map((p) => ({ x: new Date(p.x), y: p.y }));
        modalCharts.temp.options.scales.x.min = minTime;
        modalCharts.temp.options.scales.x.max = maxTime;
        modalCharts.temp.update('none');
    }
    if (modalCharts.power) {
        modalCharts.power.data.datasets[0].data = filteredPower.map((p) => ({ x: new Date(p.x), y: p.y }));
        modalCharts.power.options.scales.x.min = minTime;
        modalCharts.power.options.scales.x.max = maxTime;
        modalCharts.power.update('none');
    }
}

// Helper to extract pool name from URL
function extractPoolName(poolUrl) {
    if (!poolUrl) return null;
    try {
        // Extract domain from stratum URL
        const match = poolUrl.match(/stratum\+tcp:\/\/([^:\/]+)/);
        if (match) {
            const domain = match[1];
            // Extract short name from domain
            if (domain.includes('f2pool')) return 'f2pool';
            if (domain.includes('antpool')) return 'antpool';
            if (domain.includes('viabtc')) return 'viabtc';
            if (domain.includes('slush')) return 'slushpool';
            if (domain.includes('nicehash')) return 'nicehash';
            if (domain.includes('poolin')) return 'poolin';
            if (domain.includes('btc.com')) return 'btc.com';
            if (domain.includes('binance')) return 'binance';
            return domain.split('.')[0];
        }
    } catch (e) {}
    return poolUrl;
}

async function openMinerModal(minerIp) {
    const miner = state.discoveredMiners.find((m) => m.ip === minerIp);
    if (!miner) {
        showToast('Miner not found', 'error');
        return;
    }

    currentModalMiner = miner;
    
    // Check for cached details - use immediately if available
    const cachedDetails = state.minerDetailsCache[minerIp];
    currentMinerDetails = cachedDetails || null;

    // Load historical data from the global history storage if available
    const existingHistory = historyData.miners[minerIp];
    if (existingHistory) {
        // Copy existing historical data into modal chart data
        // Convert Date objects to timestamps for consistent filtering
        modalChartData = {
            hashrate: [],
            temp: [],
            power: [],
            timestamps: [],
            fullData: {
                hashrate: existingHistory.hashrate.map(p => ({ 
                    x: p.x instanceof Date ? p.x.getTime() : p.x, 
                    y: p.y 
                })),
                temp: existingHistory.temp.map(p => ({ 
                    x: p.x instanceof Date ? p.x.getTime() : p.x, 
                    y: p.y 
                })),
                power: existingHistory.power.map(p => ({ 
                    x: p.x instanceof Date ? p.x.getTime() : p.x, 
                    y: p.y * 1000 // Convert kW to W for modal display
                })),
            },
        };
    } else {
        // Reset chart data if no history exists
        modalChartData = {
            hashrate: [],
            temp: [],
            power: [],
            timestamps: [],
            fullData: { hashrate: [], temp: [], power: [] },
        };
    }

    // Reset time scope to default
    modalTimeScope = '5m';
    document.querySelectorAll('.modal-scope-btn').forEach((btn) => {
        btn.classList.toggle('active', btn.dataset.scope === '5m');
    });

    // Update modal header
    $('modal-miner-name').textContent = miner.model || 'Miner Details';
    $('modal-miner-ip').textContent = miner.ip;

    // If we have cached details, use them immediately for full population
    if (cachedDetails) {
        updateModalWithDetails(miner, cachedDetails);
    } else {
        // Update tabs with basic data first (will show '--' for detailed fields)
        updateModalOverview(miner);
    }
    updateModalBoards(miner);
    updateModalPools(miner);
    updateModalConfig(miner);
    initMinerCharts(miner);

    // Show modal
    $('miner-modal').classList.add('active');

    // Fetch fresh detailed data from API in background to update cache
    try {
        const details = await fetchMinerDetails(miner.ip);
        if (details) {
            currentMinerDetails = details;
            state.minerDetailsCache[minerIp] = details; // Cache for future use
            updateModalWithDetails(miner, details);
        }
    } catch (e) {
        console.warn('Failed to fetch detailed miner info:', e);
    }

    // Auto-load chip hashrate data for Vnish firmware
    if (miner.firmware_type === 'vnish' || !miner.firmware_type) {
        loadChipHashrateAuto(miner.ip);
    }
}

async function fetchMinerDetails(minerIp) {
    try {
        // Use the new comprehensive details endpoint
        const response = await fetch(`${CONFIG.apiBase}/miner/${minerIp}/details`);
        if (response.ok) {
            return await response.json();
        }
    } catch (e) {
        console.warn('Could not fetch miner details:', e);
    }
    return null;
}

function updateModalWithDetails(miner, details) {
    // Update Overview tab with comprehensive data
    if (details.shares) {
        $('modal-accepted').textContent = details.shares.accepted?.toLocaleString() || '--';
        $('modal-rejected').textContent = details.shares.rejected?.toLocaleString() || '--';
        $('modal-hw-errors').textContent = details.shares.hw_errors?.toLocaleString() || '--';

        // Add reject rate indicator if high
        const rejectRate = details.shares.reject_rate || 0;
        if (rejectRate > 1) {
            $('modal-rejected').style.color = rejectRate > 5 ? 'var(--error)' : 'var(--warning)';
        }
    }

    // Update frequency/voltage from actual running values
    if (details.summary) {
        if (details.summary.frequency_mhz) {
            $('modal-freq').textContent = details.summary.frequency_mhz + ' MHz';
        }
        if (details.summary.voltage_mv) {
            $('modal-voltage-val').textContent = details.summary.voltage_mv + ' mV';
        }
        if (details.summary.miner_version) {
            $('modal-firmware').textContent = details.summary.miner_version;
        }
    }

    // Update worker from pool info
    if (details.pools && details.pools.length > 0) {
        const activePool = details.pools.find((p) => p.status === 'Alive') || details.pools[0];
        if (activePool && activePool.worker) {
            $('modal-worker').textContent = activePool.worker;
        }
    }

    // Update boards with real data
    if (details.boards && details.boards.length > 0) {
        updateModalBoardsWithDetails(details.boards);

        // Update PCB temp in overview (max of boards)
        const maxPcbTemp = Math.max(...details.boards.map((b) => b.pcb_temp || 0));
        if (maxPcbTemp > 0) {
            $('modal-pcb-temp').textContent = maxPcbTemp + ' °C';
        }
    }

    // Update pools with detailed info
    if (details.pools) {
        updateModalPoolsWithDetails(details.pools);
    }

    // Update config with actual values
    if (details.config) {
        updateModalConfigWithDetails(details.config);
    }

    // Update system info tab
    if (details.system) {
        updateModalSystemInfo(details.system);
        currentMinerDetails.system = details.system;
    }
}

function closeModal() {
    $('miner-modal').classList.remove('active');
    currentModalMiner = null;
    // Destroy charts to free memory
    Object.values(modalCharts).forEach((chart) => {
        if (chart) chart.destroy();
    });
    modalCharts = { hashrate: null, temp: null, power: null };
}

// Update only live/changing data (called on polling interval)
function updateModalOverviewLive(miner) {
    const statusText = miner.is_mining ? 'Mining' : miner.is_online ? 'Idle' : 'Offline';
    const hashrate = parseFloat(miner.hashrate_ghs) || 0;
    const hashrateDisplay = hashrate >= 1000 ? (hashrate / 1000).toFixed(2) + ' TH/s' : hashrate.toFixed(0) + ' GH/s';
    const power = miner.power_kw > 0 ? miner.power_kw : miner.rated_power_kw;
    const powerWatts = power * 1000;
    const efficiency = hashrate > 0 && power > 0 ? ((power * 1000) / (hashrate / 1000)).toFixed(1) : '--';

    // Calculate status class
    let statusClass = 'status-offline';
    if (miner.is_mining) statusClass = 'status-mining';
    else if (miner.is_online) statusClass = 'status-idle';

    // Update only live-changing values
    const modalStatus = $('modal-status');
    modalStatus.textContent = statusText;
    modalStatus.className = 'detail-value';
    modalStatus.classList.add(statusClass);

    $('modal-hashrate').textContent = hashrateDisplay;
    $('modal-power').textContent = powerWatts.toFixed(0) + ' W';
    $('modal-efficiency').textContent = efficiency + ' J/TH';
    if (miner.temperature_c > 0) $('modal-chip-temp').textContent = miner.temperature_c.toFixed(0) + ' °C';
    if (miner.fan_speed_pct > 0) $('modal-fan-speed').textContent = miner.fan_speed_pct.toFixed(0) + '%';
    $('modal-uptime').textContent = formatUptime(miner.uptime_seconds);

    // Update control buttons state
    updateModalControlButtons(miner);
}

// Update all overview fields (called on modal open)
function updateModalOverview(miner) {
    const statusText = miner.is_mining ? 'Mining' : miner.is_online ? 'Idle' : 'Offline';
    const hashrate = parseFloat(miner.hashrate_ghs) || 0;
    const hashrateDisplay = hashrate >= 1000 ? (hashrate / 1000).toFixed(2) + ' TH/s' : hashrate.toFixed(0) + ' GH/s';
    const power = miner.power_kw > 0 ? miner.power_kw : miner.rated_power_kw;
    const powerWatts = power * 1000;
    const efficiency = hashrate > 0 && power > 0 ? ((power * 1000) / (hashrate / 1000)).toFixed(1) : '--';

    // Calculate status class
    let statusClass = 'status-offline';
    if (miner.is_mining) statusClass = 'status-mining';
    else if (miner.is_online) statusClass = 'status-idle';

    // Update values
    const modalStatus = $('modal-status');
    modalStatus.textContent = statusText;
    modalStatus.className = 'detail-value';
    modalStatus.classList.add(statusClass);

    $('modal-hashrate').textContent = hashrateDisplay;
    $('modal-power').textContent = powerWatts.toFixed(0) + ' W';
    $('modal-efficiency').textContent = efficiency + ' J/TH';
    $('modal-chip-temp').textContent = miner.temperature_c > 0 ? miner.temperature_c.toFixed(0) + ' °C' : '--';
    $('modal-pcb-temp').textContent = '--';
    $('modal-fan-speed').textContent = miner.fan_speed_pct > 0 ? miner.fan_speed_pct.toFixed(0) + '%' : '--';
    $('modal-uptime').textContent = formatUptime(miner.uptime_seconds);
    $('modal-accepted').textContent = miner.accepted_shares?.toLocaleString() || '--';
    $('modal-rejected').textContent = miner.rejected_shares?.toLocaleString() || '--';
    $('modal-hw-errors').textContent = miner.hw_errors?.toLocaleString() || '--';
    $('modal-firmware').textContent = miner.firmware || 'Vnish';

    // New fields
    $('modal-freq').textContent = miner.frequency_mhz ? miner.frequency_mhz + ' MHz' : '-- MHz';
    $('modal-voltage-val').textContent = miner.voltage_mv ? miner.voltage_mv + ' mV' : '-- mV';
    $('modal-pool').textContent = extractPoolName(miner.pool_url) || '--';
    $('modal-worker').textContent = miner.worker_name || '--';

    // Update firmware badge in modal header
    updateModalFirmwareBadge(miner);

    // Update control buttons state
    updateModalControlButtons(miner);
}

// Update firmware badge in modal header
function updateModalFirmwareBadge(miner) {
    const badge = $('modal-firmware-badge');
    if (!badge) return;

    const firmwareType = miner.firmware_type || 'unknown';
    const badgeText =
        firmwareType === 'vnish' ? 'Vnish' : firmwareType === 'braiins' ? 'BraiinsOS' : firmwareType === 'stock' ? 'Stock' : firmwareType === 'marathon' ? 'Marathon' : 'Unknown';

    badge.textContent = badgeText;
    badge.className = `firmware-badge ${firmwareType}`;
    badge.title = miner.firmware_version || 'Firmware Type';
}

function updateModalControlButtons(miner) {
    const startBtn = $('modal-btn-start');
    const stopBtn = $('modal-btn-stop');
    const rebootBtn = $('modal-btn-reboot');

    if (miner.is_online) {
        startBtn.disabled = miner.is_mining;
        stopBtn.disabled = !miner.is_mining;
        rebootBtn.disabled = false;
    } else {
        startBtn.disabled = true;
        stopBtn.disabled = true;
        rebootBtn.disabled = true;
    }
}

function updateModalBoards(miner) {
    const boardsContainer = $('modal-boards');
    const totalHashrate = miner.hashrate_ghs || 0;
    const baseTemp = miner.temperature_c || 65;

    // Generate board data - S9 has 3 boards with 63 chips each
    const boards = [
        { id: 1, status: miner.is_mining ? 'healthy' : miner.is_online ? 'warning' : 'error', hashrate: totalHashrate / 3, temp: baseTemp - 2, chips: 63 },
        { id: 2, status: miner.is_mining ? 'healthy' : miner.is_online ? 'warning' : 'error', hashrate: totalHashrate / 3, temp: baseTemp, chips: 63 },
        { id: 3, status: miner.is_mining ? 'healthy' : miner.is_online ? 'warning' : 'error', hashrate: totalHashrate / 3, temp: baseTemp + 2, chips: 63 },
    ];

    boardsContainer.innerHTML = boards
        .map(
            (board) => `
        <div class="board-card">
            <div class="board-header">
                <span class="board-title">Hash Board ${board.id}</span>
                <span class="board-status ${board.status}">${board.status}</span>
            </div>
            <div class="board-stats">
                <div class="board-stat">
                    <span class="board-stat-value">${(board.hashrate / 1000).toFixed(2)}</span>
                    <span class="board-stat-label">TH/s</span>
                </div>
                <div class="board-stat">
                    <span class="board-stat-value">${board.temp.toFixed(0)}°</span>
                    <span class="board-stat-label">Chip Temp</span>
                </div>
                <div class="board-stat">
                    <span class="board-stat-value">${board.chips}</span>
                    <span class="board-stat-label">Active Chips</span>
                </div>
                <div class="board-stat">
                    <span class="board-stat-value">${board.status === 'healthy' ? '0' : '--'}</span>
                    <span class="board-stat-label">HW Errors</span>
                </div>
            </div>
        </div>
    `,
        )
        .join('');
}

function updateModalBoardsWithDetails(boards) {
    const boardsContainer = $('modal-boards');
    if (!boards || boards.length === 0) return;

    boardsContainer.innerHTML = boards
        .map((board, idx) => {
            // Determine status from multiple factors
            let status = 'healthy';
            let statusText = 'OK';

            if (board.chips_ok < board.chips_total) {
                status = 'warning';
                statusText = `${board.chips_total - board.chips_ok} chips missing`;
            }
            if (board.chip_temp > 90) {
                status = 'warning';
                statusText = 'High temp';
            }
            if (board.chip_temp > 100 || board.chips_ok === 0) {
                status = 'error';
                statusText = board.chips_ok === 0 ? 'Offline' : 'Critical temp';
            }
            if (board.hw_errors > 100) {
                status = status === 'error' ? 'error' : 'warning';
                if (board.hw_errors > 1000) status = 'error';
            }

            // Format hashrate
            const hashrateDisplay = board.hashrate_ghs >= 1000 ? (board.hashrate_ghs / 1000).toFixed(2) + ' TH/s' : board.hashrate_ghs.toFixed(0) + ' GH/s';

            // Format power
            const powerDisplay = board.power_watts > 0 ? board.power_watts.toFixed(0) + ' W' : '--';

            return `
        <div class="board-card board-card-detailed">
            <div class="board-header">
                <span class="board-title">Hash Board ${board.id || idx + 1}</span>
                <span class="board-status ${status}">${statusText}</span>
            </div>
            <div class="board-stats-detailed">
                <div class="board-stat-row">
                    <div class="board-stat">
                        <span class="board-stat-label">Hashrate</span>
                        <span class="board-stat-value">${hashrateDisplay}</span>
                    </div>
                    <div class="board-stat">
                        <span class="board-stat-label">Power</span>
                        <span class="board-stat-value">${powerDisplay}</span>
                    </div>
                </div>
                <div class="board-stat-row">
                    <div class="board-stat">
                        <span class="board-stat-label">Chip Temp</span>
                        <span class="board-stat-value ${board.chip_temp > 90 ? 'temp-warning' : ''} ${board.chip_temp > 100 ? 'temp-critical' : ''}">${
                board.chip_temp || '--'
            }°C</span>
                    </div>
                    <div class="board-stat">
                        <span class="board-stat-label">PCB Temp</span>
                        <span class="board-stat-value">${board.pcb_temp || '--'}°C</span>
                    </div>
                </div>
                <div class="board-stat-row">
                    <div class="board-stat">
                        <span class="board-stat-label">Frequency</span>
                        <span class="board-stat-value">${board.frequency_mhz || '--'} MHz</span>
                    </div>
                    <div class="board-stat">
                        <span class="board-stat-label">Voltage</span>
                        <span class="board-stat-value">${board.voltage_mv || '--'} mV</span>
                    </div>
                </div>
                <div class="board-stat-row">
                    <div class="board-stat">
                        <span class="board-stat-label">Active Chips</span>
                        <span class="board-stat-value ${board.chips_ok < board.chips_total ? 'chip-warning' : ''}">${board.chips_ok || '--'} / ${board.chips_total || '--'}</span>
                    </div>
                    <div class="board-stat">
                        <span class="board-stat-label">HW Errors</span>
                        <span class="board-stat-value ${board.hw_errors > 100 ? 'error-warning' : ''} ${board.hw_errors > 1000 ? 'error-critical' : ''}">${
                board.hw_errors?.toLocaleString() || '0'
            }</span>
                    </div>
                </div>
                ${
                    board.chip_status
                        ? `
                <div class="chip-status-visual">
                    <span class="chip-status-label">Chip Status</span>
                    <div class="chip-grid">${renderChipStatus(board.chip_status)}</div>
                </div>
                `
                        : ''
                }
            </div>
        </div>
    `;
        })
        .join('');
}

// Render chip status visualization
function renderChipStatus(chipStatusStr) {
    if (!chipStatusStr) return '<span class="chip-status-na">No chip data available</span>';

    // Remove spaces and trim the string
    const cleanStr = chipStatusStr.replace(/\s+/g, '').trim();
    if (!cleanStr) return '<span class="chip-status-na">No chip data available</span>';

    // Each character represents a chip: 'o' = OK, 'x' = bad, '-' = missing
    let chipIndex = 0;
    return cleanStr
        .split('')
        .map((char) => {
            chipIndex++;
            let chipClass = 'chip-ok';
            let chipTitle = 'OK';
            if (char === 'x' || char === 'X') {
                chipClass = 'chip-bad';
                chipTitle = 'Bad';
            } else if (char === '-') {
                chipClass = 'chip-missing';
                chipTitle = 'Missing';
            } else if (char === 'o' || char === 'O') {
                chipClass = 'chip-ok';
                chipTitle = 'OK';
            }
            return `<span class="chip ${chipClass}" title="Chip ${chipIndex}: ${chipTitle}"></span>`;
        })
        .join('');
}

// =========================================================================
// Chip Hashrate Visualization (Vnish Feature)
// =========================================================================

async function loadChipHashrate() {
    if (!currentModalMiner) {
        showToast('No miner selected', 'error');
        return;
    }

    await loadChipHashrateAuto(currentModalMiner.ip);
}

// Auto-load chip hashrate without button click
async function loadChipHashrateAuto(minerIp) {
    const indicator = $('chip-loading-indicator');
    if (indicator) indicator.style.display = 'inline-flex';

    try {
        const response = await fetch(`/dashboard/api/miner/${minerIp}/chip-hashrate`);
        const data = await response.json();

        if (!data.success) {
            console.warn(data.error || 'Failed to load chip data');
            return;
        }

        // Update chip health summary
        $('healthy-chip-count').textContent = data.healthy_chips;
        $('warning-chip-count').textContent = data.warning_chips;
        $('dead-chip-count').textContent = data.dead_chips;

        // Update board cards with chip hashrate visualization
        updateBoardsWithChipHashrate(data.boards);
    } catch (error) {
        console.warn('Failed to load chip hashrate:', error);
    } finally {
        if (indicator) indicator.style.display = 'none';
    }
}

function updateBoardsWithChipHashrate(boards) {
    const boardsContainer = $('modal-boards');
    if (!boards || boards.length === 0) return;

    // Build updated board cards with chip visualization
    boardsContainer.innerHTML = boards
        .map((board) => {
            // Determine board status
            let status = 'healthy';
            let statusText = 'OK';
            if (board.bad_chips > 0 && board.bad_chips < 5) {
                status = 'warning';
                statusText = `${board.bad_chips} underperforming`;
            } else if (board.bad_chips >= 5) {
                status = 'error';
                statusText = `${board.bad_chips} chips degraded`;
            }

            return `
        <div class="board-card board-card-detailed">
            <div class="board-header">
                <span class="board-title">Hash Board ${board.id}</span>
                <span class="board-status ${status}">${statusText}</span>
            </div>
            <div class="board-stats-detailed">
                <div class="board-hashrate-stats">
                    <div class="board-hr-stat">
                        <span class="board-hr-stat-value">${board.total_hashrate_ghs.toFixed(2)}</span>
                        <span class="board-hr-stat-label">GH/s Total</span>
                    </div>
                    <div class="board-hr-stat">
                        <span class="board-hr-stat-value">${board.avg_hashrate_mhs.toFixed(1)}</span>
                        <span class="board-hr-stat-label">MH/s Avg</span>
                    </div>
                    <div class="board-hr-stat">
                        <span class="board-hr-stat-value">${board.min_hashrate_mhs}</span>
                        <span class="board-hr-stat-label">MH/s Min</span>
                    </div>
                    <div class="board-hr-stat">
                        <span class="board-hr-stat-value">${board.max_hashrate_mhs}</span>
                        <span class="board-hr-stat-label">MH/s Max</span>
                    </div>
                </div>
                <div class="chip-status-visual">
                    <span class="chip-status-label">Per-Chip Hashrate (hover for details)</span>
                    <div class="chip-hashrate-grid" id="chip-grid-${board.id}">
                        ${renderChipHashrateGrid(board.chips, board.avg_hashrate_mhs)}
                    </div>
                </div>
            </div>
        </div>
        `;
        })
        .join('');

    // Add tooltip event listeners
    setupChipTooltips();
}

function renderChipHashrateGrid(chips, avgHashrate) {
    if (!chips || chips.length === 0) {
        return '<span class="chip-status-na">No chip data available</span>';
    }

    return chips
        .map((chip) => {
            // Determine chip class based on hashrate relative to average
            const hr = chip.hashrate_mhs;
            let chipClass = 'good';

            if (hr === 0) {
                chipClass = 'dead';
            } else if (hr >= avgHashrate * 1.05) {
                chipClass = 'excellent';
            } else if (hr >= avgHashrate * 0.95) {
                chipClass = 'good';
            } else if (hr >= avgHashrate * 0.85) {
                chipClass = 'average';
            } else if (hr >= avgHashrate * 0.7) {
                chipClass = 'below-average';
            } else if (hr >= avgHashrate * 0.5) {
                chipClass = 'poor';
            } else {
                chipClass = 'critical';
            }

            return `<span class="chip-hr ${chipClass}" 
                      data-chip-index="${chip.index}" 
                      data-chip-hashrate="${hr}"
                      title="Chip ${chip.index}: ${hr} MH/s"></span>`;
        })
        .join('');
}

function setupChipTooltips() {
    // Create tooltip element if it doesn't exist
    let tooltip = document.querySelector('.chip-hr-tooltip');
    if (!tooltip) {
        tooltip = document.createElement('div');
        tooltip.className = 'chip-hr-tooltip';
        tooltip.style.display = 'none';
        document.body.appendChild(tooltip);
    }

    // Add event listeners to all chip elements
    document.querySelectorAll('.chip-hr').forEach((chip) => {
        chip.addEventListener('mouseenter', (e) => {
            const idx = e.target.dataset.chipIndex;
            const hr = e.target.dataset.chipHashrate;
            tooltip.innerHTML = `<span class="chip-index">Chip ${idx}:</span> <span class="chip-hashrate">${hr} MH/s</span>`;
            tooltip.style.display = 'block';
        });

        chip.addEventListener('mousemove', (e) => {
            tooltip.style.left = e.pageX + 10 + 'px';
            tooltip.style.top = e.pageY - 25 + 'px';
        });

        chip.addEventListener('mouseleave', () => {
            tooltip.style.display = 'none';
        });
    });
}

function updateModalPools(miner) {
    const poolsContainer = $('modal-pools');

    const pool = {
        url: miner.pool_url || 'Not configured',
        worker: miner.worker || '--',
        status: miner.is_mining ? 'Connected' : 'Disconnected',
    };

    poolsContainer.innerHTML = `
        <div class="pools-list">
            <div class="pool-entry">
                <div class="pool-entry-header">
                    <span class="pool-number">Pool 1 (Active)</span>
                    <span class="pool-status ${miner.is_mining ? 'connected' : 'disconnected'}">${pool.status}</span>
                </div>
                <div class="pool-details">
                    <div class="detail-item">
                        <div class="detail-label">URL</div>
                        <div class="detail-value pool-url">${escapeHtml(pool.url)}</div>
                    </div>
                    <div class="detail-item">
                        <div class="detail-label">Worker</div>
                        <div class="detail-value">${escapeHtml(pool.worker)}</div>
                    </div>
                </div>
            </div>
            <div class="pool-entry inactive">
                <div class="pool-entry-header">
                    <span class="pool-number">Pool 2 (Backup)</span>
                    <span class="pool-status">Not configured</span>
                </div>
            </div>
            <div class="pool-entry inactive">
                <div class="pool-entry-header">
                    <span class="pool-number">Pool 3 (Backup)</span>
                    <span class="pool-status">Not configured</span>
                </div>
            </div>
        </div>
        <div class="pool-actions">
            <button class="btn btn-primary" id="modal-btn-edit-pool">Edit Pool Settings</button>
        </div>
    `;

    // Add pool edit handler
    $('modal-btn-edit-pool')?.addEventListener('click', () => {
        if (currentModalMiner) {
            openPoolConfig(currentModalMiner.ip);
        }
    });
}

function updateModalPoolsWithDetails(pools) {
    const poolsContainer = $('modal-pools');
    if (!pools) return;

    // Ensure we always show 3 pools
    while (pools.length < 3) {
        pools.push({ id: pools.length, url: '', worker: '', status: '' });
    }

    let html = '<div class="pools-list">';

    pools.forEach((pool, idx) => {
        const isAlive = pool.status === 'Alive';
        const isActive = isAlive && pool.stratum_active;
        const hasUrl = pool.url && pool.url.length > 0;

        // Determine status display
        let statusClass = 'disconnected';
        let statusText = 'Not configured';
        if (hasUrl) {
            if (isActive) {
                statusClass = 'connected';
                statusText = 'Active';
            } else if (isAlive) {
                statusClass = 'standby';
                statusText = 'Standby';
            } else {
                statusClass = 'disconnected';
                statusText = pool.status || 'Dead';
            }
        }

        // Format last share time
        let lastShareText = '--';
        if (pool.last_share_time && pool.last_share_time > 0) {
            const lastShare = new Date(pool.last_share_time * 1000);
            const now = new Date();
            const diffMs = now - lastShare;
            const diffMins = Math.floor(diffMs / 60000);
            if (diffMins < 1) lastShareText = 'Just now';
            else if (diffMins < 60) lastShareText = `${diffMins}m ago`;
            else lastShareText = `${Math.floor(diffMins / 60)}h ${diffMins % 60}m ago`;
        }

        // Calculate reject rate for this pool
        const totalShares = (pool.accepted || 0) + (pool.rejected || 0);
        const rejectRate = totalShares > 0 ? (((pool.rejected || 0) / totalShares) * 100).toFixed(2) : '0.00';

        html += `
            <div class="pool-entry ${!hasUrl ? 'inactive' : ''} ${isActive ? 'pool-active' : ''}">
                <div class="pool-entry-header">
                    <span class="pool-number">Pool ${idx + 1}${isActive ? ' (Active)' : hasUrl ? ' (Backup)' : ''}</span>
                    <span class="pool-status ${statusClass}">${statusText}</span>
                </div>
                ${
                    hasUrl
                        ? `
                <div class="pool-details-comprehensive">
                    <div class="pool-detail-row">
                        <div class="pool-detail-item full-width">
                            <span class="pool-detail-label">URL</span>
                            <span class="pool-detail-value pool-url">${escapeHtml(pool.url)}</span>
                        </div>
                    </div>
                    <div class="pool-detail-row">
                        <div class="pool-detail-item">
                            <span class="pool-detail-label">Worker</span>
                            <span class="pool-detail-value">${escapeHtml(pool.worker || '--')}</span>
                        </div>
                        <div class="pool-detail-item">
                            <span class="pool-detail-label">Priority</span>
                            <span class="pool-detail-value">${pool.priority !== undefined ? pool.priority : idx}</span>
                        </div>
                    </div>
                    <div class="pool-detail-row">
                        <div class="pool-detail-item">
                            <span class="pool-detail-label">Accepted</span>
                            <span class="pool-detail-value pool-accepted">${(pool.accepted || 0).toLocaleString()}</span>
                        </div>
                        <div class="pool-detail-item">
                            <span class="pool-detail-label">Rejected</span>
                            <span class="pool-detail-value pool-rejected ${pool.rejected > 0 ? 'has-rejects' : ''}">${(pool.rejected || 0).toLocaleString()}</span>
                        </div>
                        <div class="pool-detail-item">
                            <span class="pool-detail-label">Stale</span>
                            <span class="pool-detail-value">${(pool.stale || 0).toLocaleString()}</span>
                        </div>
                    </div>
                    <div class="pool-detail-row">
                        <div class="pool-detail-item">
                            <span class="pool-detail-label">Reject Rate</span>
                            <span class="pool-detail-value ${parseFloat(rejectRate) > 1 ? 'reject-warning' : ''} ${
                              parseFloat(rejectRate) > 5 ? 'reject-critical' : ''
                          }">${rejectRate}%</span>
                        </div>
                        <div class="pool-detail-item">
                            <span class="pool-detail-label">Difficulty</span>
                            <span class="pool-detail-value">${pool.difficulty ? parseFloat(pool.difficulty).toLocaleString() : '--'}</span>
                        </div>
                        <div class="pool-detail-item">
                            <span class="pool-detail-label">Last Share</span>
                            <span class="pool-detail-value">${lastShareText}</span>
                        </div>
                    </div>
                    ${
                        pool.best_share
                            ? `
                    <div class="pool-detail-row">
                        <div class="pool-detail-item">
                            <span class="pool-detail-label">Best Share</span>
                            <span class="pool-detail-value">${pool.best_share.toLocaleString()}</span>
                        </div>
                        <div class="pool-detail-item">
                            <span class="pool-detail-label">Getworks</span>
                            <span class="pool-detail-value">${(pool.getworks || 0).toLocaleString()}</span>
                        </div>
                    </div>
                    `
                            : ''
                    }
                </div>
                `
                        : ''
                }
            </div>
        `;
    });

    html += `</div>
        <div class="pool-actions">
            <button class="btn btn-primary" id="modal-btn-edit-pool">Edit Pool Settings</button>
        </div>`;

    poolsContainer.innerHTML = html;

    // Re-attach pool edit handler
    $('modal-btn-edit-pool')?.addEventListener('click', () => {
        if (currentModalMiner) {
            openPoolConfig(currentModalMiner.ip);
        }
    });
}

function openPoolConfig(minerIp) {
    // Show pool edit form in a sub-modal or inline
    const miner = state.discoveredMiners.find((m) => m.ip === minerIp);
    if (!miner) {
        showToast('Miner not found', 'error');
        return;
    }

    // Fetch current pool config and show edit form
    showPoolEditForm(minerIp);
}

async function showPoolEditForm(minerIp) {
    // Create and show pool edit form
    const poolsTab = $('modal-pools');
    if (!poolsTab) return;

    // Get current config from details
    const config = currentMinerDetails?.config || {};
    const pools = config.pools || [{}, {}, {}];

    let html = `
        <div class="pool-edit-form">
            <h4>Edit Pool Configuration</h4>
            <p class="pool-edit-note">Changes will be saved to the miner and require a restart to take effect.</p>
    `;

    for (let i = 0; i < 3; i++) {
        const pool = pools[i] || {};
        html += `
            <div class="pool-edit-group">
                <h5>Pool ${i + 1}</h5>
                <div class="form-row">
                    <label>URL:</label>
                    <input type="text" id="pool-url-${i}" class="form-input" value="${escapeHtml(pool.url || '')}" placeholder="stratum+tcp://pool.com:3333">
                </div>
                <div class="form-row">
                    <label>Worker:</label>
                    <input type="text" id="pool-user-${i}" class="form-input" value="${escapeHtml(pool.user || '')}" placeholder="wallet.worker">
                </div>
                <div class="form-row">
                    <label>Password:</label>
                    <input type="text" id="pool-pass-${i}" class="form-input" value="${escapeHtml(pool.pass || 'x')}" placeholder="x">
                </div>
            </div>
        `;
    }

    html += `
            <div class="pool-edit-actions">
                <button class="btn btn-secondary" id="pool-edit-cancel">Cancel</button>
                <button class="btn btn-primary" id="pool-edit-save">Save Pool Settings</button>
            </div>
        </div>
    `;

    poolsTab.innerHTML = html;

    // Add event handlers
    $('pool-edit-cancel')?.addEventListener('click', () => {
        // Restore original pool display
        if (currentMinerDetails?.pools) {
            updateModalPoolsWithDetails(currentMinerDetails.pools);
        } else {
            updateModalPools(currentModalMiner);
        }
    });

    $('pool-edit-save')?.addEventListener('click', () => savePoolConfig(minerIp));
}

async function savePoolConfig(minerIp) {
    const pools = [];
    for (let i = 0; i < 3; i++) {
        pools.push({
            url: $(`pool-url-${i}`)?.value || '',
            user: $(`pool-user-${i}`)?.value || '',
            pass: $(`pool-pass-${i}`)?.value || 'x',
        });
    }

    try {
        const response = await fetch(`${CONFIG.apiBase}/miner/${minerIp}/pools`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ pools }),
        });

        const result = await response.json();
        if (result.success) {
            showToast('Pool settings saved. Miner will restart.', 'success');
            // Refresh details after a delay
            setTimeout(async () => {
                const details = await fetchMinerDetails(minerIp);
                if (details) {
                    currentMinerDetails = details;
                    updateModalPoolsWithDetails(details.pools);
                }
            }, 3000);
        } else {
            showToast(`Failed to save: ${result.error || 'Unknown error'}`, 'error');
        }
    } catch (e) {
        showToast('Failed to save pool settings', 'error');
        console.error('Pool save error:', e);
    }
}

function updateModalConfig(miner) {
    // Set default values based on miner type (S9 defaults)
    $('modal-frequency').value = 550;
    $('modal-voltage').value = 880;
    $('modal-target-temp').value = 75;
    $('modal-shutdown-temp').value = 105;
    $('modal-fan-mode').value = 'auto';
    $('modal-fan-pwm').value = 100;
    $('modal-asicboost').value = 'true';
    $('modal-autodownscale').value = 'false';
    $('modal-downscale-step').value = 25;
    $('modal-downscale-min').value = 400;
    $('modal-beeper').value = 'true';

    // Clear per-board values (will use global)
    $('modal-freq1').value = '';
    $('modal-freq2').value = '';
    $('modal-freq3').value = '';
    $('modal-volt1').value = '';
    $('modal-volt2').value = '';
    $('modal-volt3').value = '';
}

function updateModalConfigWithDetails(config) {
    // Performance settings
    if (config.frequency) $('modal-frequency').value = config.frequency;
    if (config.voltage) {
        // Voltage might be in format "8.8" (volts) or "880" (mV)
        const voltage = parseFloat(config.voltage);
        $('modal-voltage').value = voltage < 100 ? Math.round(voltage * 100) : Math.round(voltage);
    }
    if (config.asicboost !== undefined) $('modal-asicboost').value = config.asicboost ? 'true' : 'false';

    // Per-board tuning
    if (config.frequency1) $('modal-freq1').value = config.frequency1;
    if (config.frequency2) $('modal-freq2').value = config.frequency2;
    if (config.frequency3) $('modal-freq3').value = config.frequency3;
    if (config.voltage1) {
        const v = parseFloat(config.voltage1);
        $('modal-volt1').value = v < 100 ? Math.round(v * 100) : Math.round(v);
    }
    if (config.voltage2) {
        const v = parseFloat(config.voltage2);
        $('modal-volt2').value = v < 100 ? Math.round(v * 100) : Math.round(v);
    }
    if (config.voltage3) {
        const v = parseFloat(config.voltage3);
        $('modal-volt3').value = v < 100 ? Math.round(v * 100) : Math.round(v);
    }

    // Thermal settings
    if (config.target_temp) $('modal-target-temp').value = config.target_temp;
    if (config.shutdown_temp) $('modal-shutdown-temp').value = config.shutdown_temp;
    if (config.fan_ctrl !== undefined) {
        $('modal-fan-mode').value = config.fan_ctrl ? 'manual' : 'auto';
    }
    if (config.fan_pwm) $('modal-fan-pwm').value = config.fan_pwm;

    // Auto-downscale
    if (config.autodownscale) {
        $('modal-autodownscale').value = config.autodownscale.enabled ? 'true' : 'false';
        if (config.autodownscale.step) $('modal-downscale-step').value = config.autodownscale.step;
        if (config.autodownscale.min) $('modal-downscale-min').value = config.autodownscale.min;
    }

    // Alerts
    if (config.beeper !== undefined) $('modal-beeper').value = config.beeper ? 'true' : 'false';
}

function updateModalSystemInfo(system) {
    if (!system) return;

    $('modal-hostname').textContent = system.hostname || '--';
    $('modal-mac').textContent = system.macaddr || '--';
    $('modal-ip-addr').textContent = system.ipaddress || '--';
    $('modal-gateway').textContent = system.gateway || '--';
    $('modal-dns').textContent = system.dnsservers || '--';
    $('modal-nettype').textContent = system.nettype || '--';
    $('modal-minertype').textContent = system.minertype || '--';
    $('modal-hwver').textContent = system.hardware_version || '--';
    $('modal-fwver').textContent = system.firmware_version || '--';
    $('modal-kernel').textContent = system.kernel_version || '--';
    
    // Additional system info
    if (system.uptime) $('modal-sys-uptime').textContent = system.uptime;
    else if (system.system_uptime) $('modal-sys-uptime').textContent = formatUptime(system.system_uptime);
    
    if (system.curtime) $('modal-curtime').textContent = system.curtime;
    if (system.loadaverage) $('modal-loadavg').textContent = system.loadaverage;
    if (system.bmminer_version) $('modal-cgminer-ver').textContent = system.bmminer_version;
    
    // Memory info
    if (system.mem_total && system.mem_free) {
        const memUsedMB = ((parseInt(system.mem_total) - parseInt(system.mem_free)) / 1024).toFixed(1);
        const memTotalMB = (parseInt(system.mem_total) / 1024).toFixed(1);
        $('modal-memory').textContent = `${memUsedMB} / ${memTotalMB} MB`;
    }
    
    if (system.netmask) $('modal-netmask').textContent = system.netmask;
}

// Apply configuration to miner
async function applyMinerConfig() {
    if (!currentModalMiner) {
        showToast('No miner selected', 'error');
        return;
    }

    const config = {
        frequency: parseInt($('modal-frequency').value) || null,
        voltage: parseInt($('modal-voltage').value) || null,
        target_temp: parseInt($('modal-target-temp').value) || null,
        shutdown_temp: parseInt($('modal-shutdown-temp').value) || null,
        fan_mode: $('modal-fan-mode').value,
        fan_pwm: $('modal-fan-mode').value === 'manual' ? parseInt($('modal-fan-pwm').value) : null,
        asicboost: $('modal-asicboost').value === 'true',
        beeper: $('modal-beeper').value === 'true',
        autodownscale_enabled: $('modal-autodownscale').value === 'true',
        autodownscale_step: parseInt($('modal-downscale-step').value) || null,
        autodownscale_min: parseInt($('modal-downscale-min').value) || null,
    };

    // Add per-board values if set
    if ($('modal-freq1').value) config.frequency1 = parseInt($('modal-freq1').value);
    if ($('modal-freq2').value) config.frequency2 = parseInt($('modal-freq2').value);
    if ($('modal-freq3').value) config.frequency3 = parseInt($('modal-freq3').value);
    if ($('modal-volt1').value) config.voltage1 = parseInt($('modal-volt1').value);
    if ($('modal-volt2').value) config.voltage2 = parseInt($('modal-volt2').value);
    if ($('modal-volt3').value) config.voltage3 = parseInt($('modal-volt3').value);

    // Add Vnish-specific auto-downscale parameters if present
    const downscaleTimer = $('modal-downscale-timer');
    const downscaleAfter = $('modal-downscale-after');
    const downscalePrec = $('modal-downscale-prec');
    if (downscaleTimer?.value) config.autodownscale_timer = parseInt(downscaleTimer.value);
    if (downscaleAfter?.value) config.autodownscale_after = parseInt(downscaleAfter.value);
    if (downscalePrec?.value) config.autodownscale_prec = parseInt(downscalePrec.value);

    // Remove null values
    Object.keys(config).forEach((key) => {
        if (config[key] === null || config[key] === undefined) {
            delete config[key];
        }
    });

    showToast('Applying configuration...', 'info');

    try {
        const response = await fetch(`${CONFIG.apiBase}/miner/${currentModalMiner.ip}/config`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(config),
        });

        const result = await response.json();

        if (result.success) {
            showToast('Configuration applied successfully', 'success');
        } else {
            showToast(`Failed to apply config: ${result.error}`, 'error');
        }
    } catch (e) {
        console.error('Failed to apply config:', e);
        showToast('Failed to apply configuration', 'error');
    }
}

// Apply preset configuration
async function applyPreset(preset) {
    if (!currentModalMiner) {
        showToast('No miner selected', 'error');
        return;
    }

    // Define presets (typical S9 values)
    const presets = {
        low: { frequency: 400, voltage: 840, description: 'Low Power (~10.5 TH/s)' },
        balanced: { frequency: 550, voltage: 880, description: 'Balanced (~13.5 TH/s)' },
        high: { frequency: 625, voltage: 900, description: 'Performance (~15 TH/s)' },
    };

    const config = presets[preset];
    if (!config) {
        showToast('Unknown preset', 'error');
        return;
    }

    // Update form fields
    $('modal-frequency').value = config.frequency;
    $('modal-voltage').value = config.voltage;

    // Visual feedback on preset buttons
    document.querySelectorAll('.btn-preset').forEach((btn) => btn.classList.remove('active'));
    document.querySelector(`.btn-preset[data-preset="${preset}"]`)?.classList.add('active');

    showToast(`Preset "${config.description}" selected. Click "Apply Configuration" to save.`, 'info');
}

// Reset config to defaults
function resetMinerConfig() {
    updateModalConfig(currentModalMiner);
    showToast('Reset to default values', 'info');
}

// Initialize miner-specific charts in modal
function initMinerCharts(miner) {
    const chartOptions = {
        responsive: true,
        maintainAspectRatio: false,
        animation: { duration: 0 },
        plugins: {
            legend: { display: false },
            tooltip: {
                backgroundColor: 'rgba(15, 20, 25, 0.95)',
                titleColor: '#e6edf3',
                bodyColor: '#8b949e',
                borderColor: 'rgba(255, 255, 255, 0.1)',
                borderWidth: 1,
                padding: 12,
                displayColors: false,
            },
        },
        scales: {
            x: {
                type: 'time',
                time: {
                    displayFormats: {
                        minute: 'HH:mm',
                        hour: 'HH:mm',
                    },
                    tooltipFormat: 'HH:mm:ss',
                },
                grid: { color: 'rgba(255,255,255,0.05)' },
                ticks: {
                    color: 'rgba(255,255,255,0.5)',
                    font: { size: 10 },
                    maxRotation: 0,
                    autoSkip: true,
                    maxTicksLimit: 6,
                },
            },
            y: {
                display: true,
                grid: { color: 'rgba(255,255,255,0.05)' },
                ticks: {
                    color: 'rgba(255,255,255,0.5)',
                    font: { size: 10 },
                },
                beginAtZero: true,
            },
        },
        elements: {
            point: { radius: 0 },
            line: { tension: 0.3, borderWidth: 2 },
        },
    };

    // Hashrate chart
    const hashrateCtx = $('modal-chart-hashrate')?.getContext('2d');
    if (hashrateCtx) {
        if (modalCharts.hashrate) modalCharts.hashrate.destroy();
        modalCharts.hashrate = new Chart(hashrateCtx, {
            type: 'line',
            data: {
                labels: [],
                datasets: [
                    {
                        data: [],
                        borderColor: '#10b981',
                        backgroundColor: 'rgba(16, 185, 129, 0.1)',
                        fill: true,
                    },
                ],
            },
            options: { ...chartOptions },
        });
    }

    // Temperature chart
    const tempCtx = $('modal-chart-temp')?.getContext('2d');
    if (tempCtx) {
        if (modalCharts.temp) modalCharts.temp.destroy();
        modalCharts.temp = new Chart(tempCtx, {
            type: 'line',
            data: {
                labels: [],
                datasets: [
                    {
                        data: [],
                        borderColor: '#f59e0b',
                        backgroundColor: 'rgba(245, 158, 11, 0.1)',
                        fill: true,
                    },
                ],
            },
            options: { ...chartOptions },
        });
    }

    // Power chart
    const powerCtx = $('modal-chart-power')?.getContext('2d');
    if (powerCtx) {
        if (modalCharts.power) modalCharts.power.destroy();
        modalCharts.power = new Chart(powerCtx, {
            type: 'line',
            data: {
                labels: [],
                datasets: [
                    {
                        data: [],
                        borderColor: '#6366f1',
                        backgroundColor: 'rgba(99, 102, 241, 0.1)',
                        fill: true,
                    },
                ],
            },
            options: { ...chartOptions },
        });
    }

    // Add initial data point (only if no historical data was loaded)
    if (modalChartData.fullData.hashrate.length === 0) {
        addMinerChartDataPoint(miner);
    }
    
    // Render the charts with existing historical data
    updateModalChartsWithScope();
}

// Add data point to miner charts
function addMinerChartDataPoint(miner) {
    const now = Date.now();
    const timeLabel = new Date(now).toLocaleTimeString();
    const hashrate = (miner.hashrate_ghs || miner.hashrate || 0) / 1000; // TH/s
    const temp = miner.temperature_c || miner.temp_chip || miner.temp || 0;
    const power = miner.power_kw ? miner.power_kw * 1000 : miner.power_w || 0; // W

    // Store timestamped data for scope filtering
    modalChartData.fullData.hashrate.push({ x: now, y: hashrate });
    modalChartData.fullData.temp.push({ x: now, y: temp });
    modalChartData.fullData.power.push({ x: now, y: power });

    // Store display data
    modalChartData.timestamps.push(timeLabel);
    modalChartData.hashrate.push(hashrate);
    modalChartData.temp.push(temp);
    modalChartData.power.push(power);

    // Keep last 6 hours of data (360 points at 1/min)
    const maxAge = 6 * 60 * 60 * 1000;
    const cutoff = now - maxAge;

    modalChartData.fullData.hashrate = modalChartData.fullData.hashrate.filter((p) => p.x > cutoff);
    modalChartData.fullData.temp = modalChartData.fullData.temp.filter((p) => p.x > cutoff);
    modalChartData.fullData.power = modalChartData.fullData.power.filter((p) => p.x > cutoff);

    // Keep legacy arrays in sync (last 30 for quick display)
    const maxPoints = 30;
    if (modalChartData.timestamps.length > maxPoints) {
        modalChartData.timestamps.shift();
        modalChartData.hashrate.shift();
        modalChartData.temp.shift();
        modalChartData.power.shift();
    }

    // Update charts based on current scope
    updateModalChartsWithScope();

    // Update chart values
    $('modal-chart-hashrate-val').textContent = hashrate.toFixed(2) + ' TH/s';
    $('modal-chart-temp-val').textContent = temp > 0 ? temp.toFixed(0) + ' °C' : '-- °C';
    $('modal-chart-power-val').textContent = power > 0 ? power.toFixed(0) + ' W' : '-- W';
}

async function applyModalConfig() {
    if (!currentModalMiner) return;

    const config = {
        frequency: parseInt($('modal-frequency').value) || 650,
        voltage: parseInt($('modal-voltage').value) || 850,
        target_temp: parseInt($('modal-target-temp').value) || 75,
        fan_mode: $('modal-fan-mode').value,
    };

    try {
        // TODO: Implement config update API
        showToast('Configuration update not yet implemented', 'warning');
    } catch (error) {
        showToast('Failed to apply configuration', 'error');
    }
}

function switchModalTab(tabId) {
    // Update tab buttons
    document.querySelectorAll('.modal-tab').forEach((tab) => {
        tab.classList.toggle('active', tab.dataset.tab === tabId);
    });

    // Update content
    document.querySelectorAll('.modal-content').forEach((content) => {
        content.classList.toggle('active', content.id === `tab-${tabId}`);
    });
}

async function handleModalControl(action) {
    if (!currentModalMiner) return;

    try {
        if (action === 'start') {
            await controlMiner(currentModalMiner.id, 'start');
        } else if (action === 'stop') {
            await controlMiner(currentModalMiner.id, 'stop');
        } else if (action === 'reboot') {
            await controlMiner(currentModalMiner.id, 'restart');
        }

        // Refresh miner data after action
        setTimeout(async () => {
            await fetchDiscoveredMiners();
            const updated = state.discoveredMiners.find((m) => m.ip === currentModalMiner.ip);
            if (updated) {
                updateModalOverview(updated);
            }
        }, 2000);
    } catch (error) {
        showToast(`Failed to ${action} miner`, 'error');
    }
}

// =========================================================================
// Automatic Network Scan
// =========================================================================

let autoScanInterval = null;
let autoScanEnabled = false;

function enableAutoScan(intervalMinutes = 5) {
    autoScanEnabled = true;
    autoScanInterval = setInterval(async () => {
        console.log('Auto-scanning network...');
        try {
            await fetch(`${CONFIG.apiBase}/discovery/scan`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ background: true }),
            });
        } catch (e) {
            console.warn('Auto-scan failed:', e);
        }
    }, intervalMinutes * 60 * 1000);

    // Do initial background scan (no modal)
    console.log('Running initial background scan...');
    fetch(`${CONFIG.apiBase}/discovery/scan`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ background: true }),
    }).catch((e) => console.warn('Initial background scan failed:', e));
}

function disableAutoScan() {
    autoScanEnabled = false;
    if (autoScanInterval) {
        clearInterval(autoScanInterval);
        autoScanInterval = null;
    }
}

// =========================================================================
// Additional Event Listeners Setup
// =========================================================================

function setupNewEventListeners() {
    // Pool configuration
    $('btn-update-pool-single')?.addEventListener('click', handleUpdatePoolSingle);
    $('btn-update-pool-all')?.addEventListener('click', handleUpdatePoolAll);

    // Anomaly detection
    $('btn-clear-anomalies')?.addEventListener('click', clearAnomalies);

    // Time scope buttons
    document.querySelectorAll('.scope-btn').forEach((btn) => {
        btn.addEventListener('click', () => setTimeScope(btn.dataset.scope));
    });

    // Modal
    $('btn-close-modal')?.addEventListener('click', closeModal);
    $('miner-modal')?.addEventListener('click', (e) => {
        if (e.target.id === 'miner-modal') closeModal();
    });

    // Modal tabs
    document.querySelectorAll('.modal-tab').forEach((tab) => {
        tab.addEventListener('click', () => switchModalTab(tab.dataset.tab));
    });

    // Modal control buttons
    $('modal-btn-start')?.addEventListener('click', () => handleModalControl('start'));
    $('modal-btn-stop')?.addEventListener('click', () => handleModalControl('stop'));
    $('modal-btn-reboot')?.addEventListener('click', () => handleModalControl('reboot'));

    // Modal config buttons
    $('modal-btn-apply-config')?.addEventListener('click', () => applyMinerConfig());
    $('modal-btn-reset-config')?.addEventListener('click', () => resetMinerConfig());

    // Chip hashrate load button
    $('btn-load-chip-hashrate')?.addEventListener('click', () => loadChipHashrate());

    // Preset buttons
    document.querySelectorAll('.btn-preset').forEach((btn) => {
        btn.addEventListener('click', () => applyPreset(btn.dataset.preset));
    });

    // Fan mode change handler - show/hide PWM input
    $('modal-fan-mode')?.addEventListener('change', (e) => {
        const pwmGroup = $('fan-pwm-group');
        if (pwmGroup) {
            pwmGroup.style.display = e.target.value === 'manual' ? 'flex' : 'none';
        }
    });

    // Modal chart time scope buttons
    document.querySelectorAll('.modal-scope-btn').forEach((btn) => {
        btn.addEventListener('click', () => setModalTimeScope(btn.dataset.scope));
    });

    // Make miner cards clickable to open modal
    document.addEventListener('click', (e) => {
        const card = e.target.closest('.miner-card:not(.placeholder)');
        if (card && !e.target.closest('button') && !e.target.closest('a')) {
            const ip = card.querySelector('.miner-ip')?.textContent;
            if (ip) openMinerModal(ip);
        }
    });

    // Make miner table rows clickable to open modal
    document.addEventListener('click', (e) => {
        const row = e.target.closest('.miner-row');
        if (row && !e.target.closest('button') && !e.target.closest('a')) {
            const ip = row.dataset.ip;
            if (ip) openMinerModal(ip);
        }
    });
}

// =========================================================================
// Initialize New Features
// =========================================================================

document.addEventListener('DOMContentLoaded', () => {
    // Load saved data
    loadAnomalies();
    loadHistory();

    // Setup new event listeners after a short delay to ensure DOM is ready
    setTimeout(() => {
        setupNewEventListeners();
        initCharts();

        // Auto-select best time scope based on available data
        autoSelectTimeScope();

        // Initial chart update with whatever data we have
        updateCharts();

        // Start anomaly checking
        setInterval(checkForAnomalies, 30000); // Check every 30 seconds

        // Start history recording (more frequent for responsive charts)
        setInterval(recordHistoryPoint, 10000); // Record every 10 seconds

        // Record first data point immediately
        setTimeout(recordHistoryPoint, 2000);

        // Enable auto-scan (every 5 minutes)
        enableAutoScan(5);
    }, 500);
});
