// ===== AutoAbsen SimKuliah USK - Frontend Logic =====

const API_BASE = ''; // relative - works on any domain
let statusInterval = null;
let isLoggedIn = false;

// ===== DOM Elements =====
const loginSection = document.getElementById('loginSection');
const dashboardSection = document.getElementById('dashboardSection');
const loginForm = document.getElementById('loginForm');
const loginBtn = document.getElementById('loginBtn');
const loginError = document.getElementById('loginError');
const togglePassword = document.getElementById('togglePassword');
const passwordInput = document.getElementById('password');
const headerStatus = document.getElementById('headerStatus');
const userName = document.getElementById('userName');
const userNpm = document.getElementById('userNpm');
const scheduleContent = document.getElementById('scheduleContent');
const engineDesc = document.getElementById('engineDesc');
const engineDot = document.getElementById('engineDot');
const engineStatusText = document.getElementById('engineStatusText');
const engineTime = document.getElementById('engineTime');
const startBtn = document.getElementById('startBtn');
const stopBtn = document.getElementById('stopBtn');
const logContent = document.getElementById('logContent');
const logoutBtn = document.getElementById('logoutBtn');

// ===== Toggle Password =====
if (togglePassword) {
    togglePassword.addEventListener('click', () => {
        const type = passwordInput.type === 'password' ? 'text' : 'password';
        passwordInput.type = type;
    });
}

// ===== Login =====
loginForm?.addEventListener('submit', async (e) => {
    e.preventDefault();

    const npm = document.getElementById('npm').value.trim();
    const password = passwordInput.value;

    if (!npm || !password) {
        showError('NPM dan password wajib diisi');
        return;
    }

    loginBtn.classList.add('loading');
    loginBtn.disabled = true;
    hideError();

    try {
        const res = await fetch(`${API_BASE}/api/login`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ npm, password })
        });

        if (!res.ok) {
            throw new Error(`HTTP error ${res.status}`);
        }

        const data = await res.json();
        console.log('LOGIN RESPONSE:', data);

        if (data.success) {
            isLoggedIn = true;

            userName.textContent = data.name || npm;
            userNpm.textContent = `NPM: ${npm}`;

            updateHeaderStatus('online', 'Online');
            showDashboard();
            fetchSchedule();
            startStatusPolling();
        } else {
            showError(data.message || 'Login gagal. Periksa NPM dan password.');
        }

    } catch (err) {
        console.error(err);
        showError('Tidak dapat terhubung ke server. Pastikan backend berjalan di port 5000.');
    } finally {
        loginBtn.classList.remove('loading');
        loginBtn.disabled = false;
    }
});

// ===== Logout =====
logoutBtn?.addEventListener('click', async () => {
    try {
        await fetch(`${API_BASE}/api/logout`, { method: 'POST' });
    } catch (e) { }

    isLoggedIn = false;
    stopStatusPolling();
    updateHeaderStatus('offline', 'Offline');

    dashboardSection.style.display = 'none';
    loginSection.style.display = '';
    loginForm.reset();
});

// ===== UI Helpers =====
function showDashboard() {
    loginSection.style.display = 'none';
    dashboardSection.style.display = '';
}

function showError(msg) {
    loginError.textContent = msg;
    loginError.classList.add('show');
}

function hideError() {
    loginError.textContent = '';
    loginError.classList.remove('show');
}

function updateHeaderStatus(dotClass, text) {
    const dot = headerStatus.querySelector('.status-dot');
    const statusText = headerStatus.querySelector('.status-text');

    if (!dot || !statusText) return;

    dot.className = `status-dot ${dotClass}`;
    statusText.textContent = text;
}

// ===== Fetch Schedule =====
async function fetchSchedule() {
    scheduleContent.innerHTML = `
        <div class="loading-state">
            <span>Memuat jadwal...</span>
        </div>`;

    try {
        const res = await fetch(`${API_BASE}/api/schedule`);
        const data = await res.json();

        if (data.success && data.schedule?.length > 0) {
            renderSchedule(data.schedule);
        } else {
            scheduleContent.innerHTML =
                `<div class="empty-state">Tidak ada jadwal ditemukan</div>`;
        }

    } catch (err) {
        scheduleContent.innerHTML =
            `<div class="empty-state">Gagal memuat jadwal</div>`;
    }
}

function renderSchedule(schedule) {
    let html = `
        <table class="schedule-table">
            <thead>
                <tr>
                    <th>Hari</th>
                    <th>Mata Kuliah</th>
                    <th>Waktu</th>
                    <th>Status</th>
                </tr>
            </thead>
            <tbody>
    `;

    schedule.forEach(item => {
        const badgeClass =
            item.status === 'active' ? 'badge-active' :
                item.status === 'upcoming' ? 'badge-upcoming' :
                    'badge-done';

        const badgeText =
            item.status === 'active' ? '● Berlangsung' :
                item.status === 'upcoming' ? '◷ Akan Datang' :
                    '✓ Selesai';

        html += `
            <tr>
                <td>${item.day || '-'}</td>
                <td>${item.course || '-'}</td>
                <td>${item.time || '-'}</td>
                <td><span class="${badgeClass}">${badgeText}</span></td>
            </tr>
        `;
    });

    html += `</tbody></table>`;
    scheduleContent.innerHTML = html;
}

// ===== Engine Controls =====
async function startEngine() {
    startBtn.disabled = true;
    const delay = parseInt(document.getElementById('absenDelay').value) || 1;

    try {
        const res = await fetch(`${API_BASE}/api/engine/start`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ absen_delay: delay })
        });

        const data = await res.json();

        if (data.success) {
            updateEngineUI(true);
        } else {
            alert(data.message || 'Gagal memulai engine');
            startBtn.disabled = false;
        }

    } catch (err) {
        alert('Tidak dapat terhubung ke server');
        startBtn.disabled = false;
    }
}

async function stopEngine() {
    stopBtn.disabled = true;

    try {
        const res = await fetch(`${API_BASE}/api/engine/stop`, {
            method: 'POST'
        });

        const data = await res.json();

        if (data.success) {
            updateEngineUI(false);
        } else {
            alert(data.message || 'Gagal menghentikan engine');
            stopBtn.disabled = false;
        }

    } catch (err) {
        alert('Tidak dapat terhubung ke server');
        stopBtn.disabled = false;
    }
}

function updateEngineUI(running) {
    if (running) {
        engineDot.className = 'status-dot running';
        engineStatusText.textContent = 'Running';
        engineDesc.textContent = 'Engine sedang berjalan dan memantau jadwal';
        startBtn.disabled = true;
        stopBtn.disabled = false;
        updateHeaderStatus('running', 'Engine Active');
    } else {
        engineDot.className = 'status-dot offline';
        engineStatusText.textContent = 'Stopped';
        engineDesc.textContent = 'Engine belum berjalan';
        startBtn.disabled = false;
        stopBtn.disabled = true;
        updateHeaderStatus('online', 'Online');
    }
}

// ===== Status Polling =====
function startStatusPolling() {
    fetchStatus();
    statusInterval = setInterval(fetchStatus, 5000);
}

function stopStatusPolling() {
    if (statusInterval) {
        clearInterval(statusInterval);
        statusInterval = null;
    }
}

async function fetchStatus() {
    try {
        const res = await fetch(`${API_BASE}/api/status`);
        const data = await res.json();

        if (data.success) {
            updateEngineUI(data.engine_running);

            if (data.last_check) {
                engineTime.textContent =
                    `Terakhir cek: ${data.last_check}`;
            }

            renderLogs(data.logs || []);
        }

    } catch (err) { }
}

// ===== Logs =====
function renderLogs(logs) {
    if (!logs.length) {
        logContent.innerHTML =
            `<div class="log-empty">Belum ada log aktivitas</div>`;
        return;
    }

    const html = logs.map(log => `
        <div class="log-entry ${log.level || 'info'}">
            <span class="log-time">${log.time || ''}</span>
            <span class="log-msg">${log.message || ''}</span>
        </div>
    `).join('');

    logContent.innerHTML = html;
    logContent.scrollTop = logContent.scrollHeight;
}

async function clearLog() {
    try {
        await fetch(`${API_BASE}/api/logs/clear`, { method: 'POST' });
        renderLogs([]);
    } catch (e) { }
}
