"""
AutoAbsen SimKuliah USK - Backend Server
Sistem otomatis absensi untuk simkuliah.usk.ac.id
"""

import os
import re
import time
import logging
from datetime import datetime, timedelta
from threading import Thread, Event

import requests
from bs4 import BeautifulSoup
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ===== Configuration =====
app = Flask(__name__, static_folder='.', static_url_path='')
CORS(app)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

SIMKULIAH_BASE = 'https://simkuliah.usk.ac.id'
SIMKULIAH_LOGIN_URL = f'{SIMKULIAH_BASE}/index.php/login/auth'
SIMKULIAH_ABSENSI_URL = f'{SIMKULIAH_BASE}/index.php/absensi'
SIMKULIAH_KONFIRMASI_URL = f'{SIMKULIAH_BASE}/index.php/absensi/konfirmasi_kehadiran'
SIMKULIAH_JADWAL_URL = f'{SIMKULIAH_BASE}/index.php/jadwal_kuliah/index'
SIMKULIAH_JADWAL_HARI_INI_URL = f'{SIMKULIAH_BASE}/index.php/jadwal_kuliah/jadwal_kuliah_hari_ini'
CHECK_INTERVAL = 60  # seconds

# ===== Global State =====
session_data = {
    'session': None,
    'npm': None,
    'name': None,
    'logged_in': False,
    'schedule': [],
    'engine_running': False,
    'last_check': None,
    'logs': [],
    'stop_event': Event(),
    'engine_thread': None,
    'absen_done_today': set(),
    'absen_delay': 1,  # minutes before class ends
}


# ===== Helper Functions =====
def add_log(message, level='info'):
    """Add a log entry."""
    now = datetime.now().strftime('%H:%M:%S')
    entry = {'time': now, 'message': message, 'level': level}
    session_data['logs'].append(entry)
    if len(session_data['logs']) > 100:
        session_data['logs'] = session_data['logs'][-100:]
    log_func = getattr(logger, level if level != 'success' else 'info', logger.info)
    log_func(message)


def save_debug(filename, content):
    """Save HTML content for debugging."""
    debug_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'debug')
    os.makedirs(debug_dir, exist_ok=True)
    with open(os.path.join(debug_dir, filename), 'w', encoding='utf-8') as f:
        f.write(content)


def create_session():
    """Create a new requests session with browser-like headers."""
    s = requests.Session()
    s.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'id-ID,id;q=0.9,en-US;q=0.8,en;q=0.7',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
    })
    return s


def login_simkuliah(npm, password):
    """Login to SimKuliah. Returns (session, user_name) or (None, error_msg)."""
    s = create_session()

    try:
        # Step 1: Visit login page to get cookies
        add_log('Mengakses halaman login SimKuliah...', 'info')
        initial = s.get(SIMKULIAH_BASE, timeout=15, verify=False)
        save_debug('login_page.html', initial.text)
        logger.info(f'[DEBUG] Login page status: {initial.status_code}, length: {len(initial.text)}')

        # Step 2: POST login with only username + password
        add_log(f'Mencoba login dengan NPM: {npm}...', 'info')
        login_data = {
            'username': npm,
            'password': password,
        }

        login_res = s.post(SIMKULIAH_LOGIN_URL, data=login_data, timeout=15,
                          allow_redirects=True, verify=False)
        save_debug('login_response.html', login_res.text)
        
        resp_text = login_res.text
        resp_lower = resp_text.lower()
        logger.info(f'[DEBUG] Login response status: {login_res.status_code}, length: {len(resp_text)}, URL: {login_res.url}')

        # Step 3: Detect login success using SIMPLE STRING MATCHING (no BS4)
        # Success indicators: user-profile class, /absensi link, /logout link
        has_user_profile = 'user-profile' in resp_text
        has_absensi_link = '/index.php/absensi' in resp_text
        has_logout_link = '/login/logout' in resp_text
        has_login_form = 'login dengan akun simpeg' in resp_lower
        
        logger.info(f'[DEBUG] has_user_profile={has_user_profile}, has_absensi={has_absensi_link}, has_logout={has_logout_link}, has_login_form={has_login_form}')

        # If we see dashboard elements, login succeeded
        if has_logout_link or has_absensi_link or has_user_profile:
            # Extract user name with regex: <span>NAME</span> inside user-profile block
            name_match = re.search(
                r'user-profile.*?<span>(.*?)</span>',
                resp_text,
                re.DOTALL
            )
            user_name = name_match.group(1).strip() if name_match else npm
            
            add_log(f'Login berhasil! Nama: {user_name}', 'success')
            logger.info(f'[DEBUG] Login SUCCESS. User: {user_name}')
            return s, user_name

        # If we still see the login form, credentials were wrong
        if has_login_form:
            logger.info('[DEBUG] Login FAILED - login form still present')
            return None, 'Login gagal. NPM atau password salah.'

        # Unknown state - log the first 500 chars for debugging
        logger.info(f'[DEBUG] Unknown login state. First 500 chars: {resp_text[:500]}')
        add_log('Status login tidak dikenali. Cek debug/login_response.html', 'warning')
        return None, 'Login gagal. Response tidak dikenali.'

    except requests.exceptions.ConnectionError:
        return None, 'Tidak dapat terhubung ke simkuliah.usk.ac.id. Periksa koneksi internet.'
    except requests.exceptions.Timeout:
        return None, 'Koneksi timeout. Server simkuliah mungkin sedang sibuk.'
    except Exception as e:
        add_log(f'Error saat login: {str(e)}', 'error')
        return None, f'Error: {str(e)}'


def fetch_schedule(s):
    """Fetch jadwal kuliah from SimKuliah."""
    schedule = []
    try:
        # Try jadwal semester page
        add_log('Mengambil jadwal kuliah...', 'info')
        res = s.get(SIMKULIAH_JADWAL_URL, timeout=15, verify=False)
        save_debug('jadwal_semester.html', res.text)

        soup = BeautifulSoup(res.text, 'lxml')
        tables = soup.find_all('table')

        for table in tables:
            rows = table.find_all('tr')
            if len(rows) < 2:
                continue

            header_text = rows[0].get_text().lower()
            if not any(k in header_text for k in ['hari', 'mata kuliah', 'matakuliah', 'jam', 'waktu', 'mk', 'kode']):
                continue

            headers = [th.get_text(strip=True).lower() for th in rows[0].find_all(['th', 'td'])]
            
            col_day = next((i for i, h in enumerate(headers) if 'hari' in h), None)
            col_course = next((i for i, h in enumerate(headers) if any(k in h for k in ['mata kuliah', 'matakuliah', 'mk', 'nama'])), None)
            col_code = next((i for i, h in enumerate(headers) if 'kode' in h), None)
            col_time_start = next((i for i, h in enumerate(headers) if any(k in h for k in ['jam mulai', 'mulai', 'waktu'])), None)
            col_time_end = next((i for i, h in enumerate(headers) if any(k in h for k in ['jam selesai', 'berakhir', 'selesai'])), None)
            col_room = next((i for i, h in enumerate(headers) if any(k in h for k in ['ruang', 'room'])), None)

            for row in rows[1:]:
                cells = row.find_all(['td'])
                if len(cells) < 2:
                    continue

                def get_cell(idx):
                    if idx is not None and idx < len(cells):
                        return cells[idx].get_text(strip=True)
                    return ''

                day = get_cell(col_day)
                course = get_cell(col_course)
                code = get_cell(col_code)
                time_start = get_cell(col_time_start)
                time_end = get_cell(col_time_end)
                room = get_cell(col_room)

                if course or code:
                    display_name = f'{code} - {course}' if code and course else (course or code)
                    time_str = f'{time_start} - {time_end}' if time_start and time_end else (time_start or time_end or '')
                    schedule.append({
                        'day': day,
                        'course': display_name,
                        'time': time_str,
                        'room': room,
                        'status': 'upcoming',
                    })

            if schedule:
                break

        if schedule:
            update_schedule_status(schedule)
            add_log(f'Ditemukan {len(schedule)} jadwal kuliah', 'success')
        else:
            add_log('Jadwal tidak ditemukan di halaman jadwal. Cek debug/jadwal_semester.html', 'warning')

        return schedule

    except Exception as e:
        add_log(f'Error mengambil jadwal: {str(e)}', 'error')
        return []


def update_schedule_status(schedule):
    """Update status (active/upcoming/done) based on current time."""
    now = datetime.now()
    day_names = ['Senin', 'Selasa', 'Rabu', 'Kamis', 'Jumat', 'Sabtu', 'Minggu']
    today = day_names[now.weekday()]
    current_time = now.strftime('%H:%M')
    day_index = {d: i for i, d in enumerate(day_names)}

    for item in schedule:
        item_day = item.get('day', '').strip()
        item_time = item.get('time', '')

        time_match = re.search(r'(\d{1,2}[:.]\d{2})\s*[-–]\s*(\d{1,2}[:.]\d{2})', item_time)

        if item_day == today and time_match:
            start = time_match.group(1).replace('.', ':')
            end = time_match.group(2).replace('.', ':')
            if start <= current_time <= end:
                item['status'] = 'active'
            elif current_time < start:
                item['status'] = 'upcoming'
            else:
                item['status'] = 'done'
        elif item_day == today:
            item['status'] = 'upcoming'
        elif item_day in day_index:
            item['status'] = 'upcoming' if day_index[item_day] > now.weekday() else 'done'


def check_and_absen(s):
    """
    Check the absensi page and submit attendance if available.
    
    The absensi page shows active classes with a "Konfirmasi Kehadiran" button.
    The button triggers an AJAX POST to konfirmasi_kehadiran with specific params
    extracted from the page's JavaScript.
    """
    try:
        add_log('Memeriksa halaman absensi...', 'info')
        res = s.get(SIMKULIAH_ABSENSI_URL, timeout=15, verify=False)
        save_debug('absensi_page.html', res.text)

        soup = BeautifulSoup(res.text, 'lxml')
        page_text = res.text

        # Check if already absent
        if 'anda sudah absen' in page_text.lower() or 'sudah hadir' in page_text.lower():
            add_log('Anda sudah absen untuk kelas yang sedang berlangsung', 'info')
            return True

        # Check "Anda belum absen" indicator
        if 'anda belum absen' not in page_text.lower() and 'belum absen' not in page_text.lower():
            # No active class or no absen needed
            add_log('Tidak ada kelas aktif yang memerlukan absen saat ini', 'info')
            return False

        # Extract absen parameters from the page JavaScript
        # Pattern: $("#konfirmasi-kehadiran-{id}").on("click", function() { ... })
        # with data: { kelas, kd_mt_kul8, jadwal_mulai, jadwal_berakhir, pertemuan, sks_mengajar, id }
        
        konfirmasi_matches = re.findall(
            r'konfirmasi-kehadiran-(\d+)',
            page_text
        )
        
        if not konfirmasi_matches:
            add_log('Tombol konfirmasi kehadiran tidak ditemukan', 'warning')
            return False

        # Extract all parameters from the JS block
        for match_id in set(konfirmasi_matches):
            # Check if we already did this one today
            today_key = f"{datetime.now().strftime('%Y-%m-%d')}_{match_id}"
            if today_key in session_data['absen_done_today']:
                add_log(f'Absen ID {match_id} sudah dilakukan hari ini', 'info')
                continue

            # Extract the JS variables for this konfirmasi block
            pattern = (
                rf'konfirmasi-kehadiran-{match_id}.*?'
                r"var kelas\s*=\s*'([^']*)'.*?"
                r"var kd_mt_kul_8\s*=\s*'([^']*)'.*?"
                r"var jadwal_mulai\s*=\s*'([^']*)'.*?"
                r"var jadwal_berakhir\s*=\s*'([^']*)'.*?"
                r"var pertemuan\s*=\s*'([^']*)'.*?"
                r"var sks_mengajar\s*=\s*'([^']*)'.*?"
                r"var id\s*=\s*'([^']*)'"
            )
            
            js_match = re.search(pattern, page_text, re.DOTALL)
            
            if not js_match:
                add_log(f'Tidak dapat mengekstrak parameter absen untuk ID {match_id}', 'warning')
                continue

            kelas = js_match.group(1)
            kd_mt_kul8 = js_match.group(2)
            jadwal_mulai = js_match.group(3)
            jadwal_berakhir = js_match.group(4)
            pertemuan_val = js_match.group(5)
            sks_mengajar = js_match.group(6)
            absen_id = js_match.group(7)

            # Extract course name from card header
            course_match = re.search(
                r'Absensi Kelas.*?\|\s*([^|]+)\s*\|.*?Pertemuan',
                page_text
            )
            course_name = course_match.group(1).strip() if course_match else kd_mt_kul8

            add_log(f'Kelas aktif ditemukan: {course_name} (Pertemuan {pertemuan_val})', 'info')
            add_log(f'Jadwal: {jadwal_mulai} - {jadwal_berakhir}', 'info')

            # ===== DELAY CHECK: wait until X minutes before class ends =====
            absen_delay = session_data.get('absen_delay', 1)
            try:
                # Parse jadwal_berakhir (format: HH:MM or HH:MM:SS)
                end_parts = jadwal_berakhir.strip().split(':')
                end_hour, end_min = int(end_parts[0]), int(end_parts[1])
                now = datetime.now()
                target_time = now.replace(hour=end_hour, minute=end_min, second=0) - timedelta(minutes=absen_delay)
                
                if now < target_time:
                    remaining = (target_time - now).total_seconds() / 60
                    add_log(f'⏳ Menunggu waktu absen: {target_time.strftime("%H:%M")} ({remaining:.0f} menit lagi)', 'info')
                    continue  # Skip this one, check again on next loop
                else:
                    add_log(f'⏰ Waktu absen tercapai! Target: {target_time.strftime("%H:%M")}', 'info')
            except Exception as e:
                add_log(f'Tidak bisa parse jadwal_berakhir, absen langsung: {e}', 'warning')

            add_log(f'Mengirim konfirmasi kehadiran...', 'info')

            # POST to konfirmasi_kehadiran
            absen_data = {
                'kelas': kelas,
                'kd_mt_kul8': kd_mt_kul8,
                'jadwal_mulai': jadwal_mulai,
                'jadwal_berakhir': jadwal_berakhir,
                'pertemuan': pertemuan_val,
                'sks_mengajar': sks_mengajar,
                'id': absen_id,
            }

            absen_res = s.post(SIMKULIAH_KONFIRMASI_URL, data=absen_data, timeout=15, verify=False)
            save_debug('absen_response.html', absen_res.text)

            response_text = absen_res.text.strip()
            add_log(f'Response absen: {response_text}', 'info')

            if response_text == 'success' or 'berhasil' in response_text.lower():
                add_log(f'✓ Absen BERHASIL untuk {course_name}!', 'success')
                session_data['absen_done_today'].add(today_key)
                return True
            elif 'sudah' in response_text.lower():
                add_log(f'Absen sudah tercatat untuk {course_name}', 'info')
                session_data['absen_done_today'].add(today_key)
                return True
            else:
                add_log(f'Response tidak dikenal: {response_text[:200]}', 'warning')
                return False

        return False

    except Exception as e:
        add_log(f'Error saat absen: {str(e)}', 'error')
        return False


def engine_loop(stop_event):
    """Main engine loop that checks absensi page periodically."""
    add_log('Engine dimulai! Memantau jadwal kuliah...', 'success')

    while not stop_event.is_set():
        try:
            s = session_data['session']
            if not s:
                add_log('Session tidak tersedia, engine berhenti', 'error')
                break

            session_data['last_check'] = datetime.now().strftime('%H:%M:%S')

            # Check absensi page and auto-absen if needed
            check_and_absen(s)

            # Also refresh schedule status
            if session_data['schedule']:
                update_schedule_status(session_data['schedule'])

            # Wait for next check
            stop_event.wait(CHECK_INTERVAL)

        except Exception as e:
            add_log(f'Error di engine loop: {str(e)}', 'error')
            stop_event.wait(CHECK_INTERVAL)

    add_log('Engine dihentikan.', 'warning')


# ===== API Routes =====
@app.route('/')
def serve_index():
    return send_from_directory('.', 'index.html')


@app.route('/<path:path>')
def serve_static(path):
    return send_from_directory('.', path)


@app.route('/api/login', methods=['POST'])
def api_login():
    data = request.get_json()
    npm = data.get('npm', '').strip()
    password = data.get('password', '')

    if not npm or not password:
        return jsonify({'success': False, 'message': 'NPM dan password diperlukan'})

    s, result = login_simkuliah(npm, password)

    if s is None:
        return jsonify({'success': False, 'message': result})

    session_data['session'] = s
    session_data['npm'] = npm
    session_data['name'] = result
    session_data['logged_in'] = True
    session_data['absen_done_today'] = set()

    return jsonify({'success': True, 'name': result, 'npm': npm})


@app.route('/api/logout', methods=['POST'])
def api_logout():
    if session_data['engine_running']:
        session_data['stop_event'].set()
        session_data['engine_running'] = False

    session_data['session'] = None
    session_data['npm'] = None
    session_data['name'] = None
    session_data['logged_in'] = False
    session_data['schedule'] = []
    session_data['logs'] = []
    session_data['absen_done_today'] = set()

    return jsonify({'success': True})


@app.route('/api/schedule', methods=['GET'])
def api_schedule():
    if not session_data['logged_in'] or not session_data['session']:
        return jsonify({'success': False, 'message': 'Belum login'})

    schedule = fetch_schedule(session_data['session'])
    session_data['schedule'] = schedule

    return jsonify({'success': True, 'schedule': schedule})


@app.route('/api/engine/start', methods=['POST'])
def api_engine_start():
    if not session_data['logged_in']:
        return jsonify({'success': False, 'message': 'Belum login'})

    if session_data['engine_running']:
        return jsonify({'success': False, 'message': 'Engine sudah berjalan'})

    # Read absen_delay from request
    data = request.get_json(silent=True) or {}
    delay = data.get('absen_delay', 1)
    try:
        delay = max(0, min(120, int(delay)))
    except (ValueError, TypeError):
        delay = 1
    session_data['absen_delay'] = delay
    add_log(f'Absen delay diset: {delay} menit sebelum kelas berakhir', 'info')

    session_data['stop_event'] = Event()
    session_data['engine_running'] = True

    thread = Thread(target=engine_loop, args=(session_data['stop_event'],), daemon=True)
    thread.start()
    session_data['engine_thread'] = thread

    return jsonify({'success': True, 'message': f'Engine dimulai (absen {delay} menit sebelum berakhir)'})


@app.route('/api/engine/stop', methods=['POST'])
def api_engine_stop():
    if not session_data['engine_running']:
        return jsonify({'success': False, 'message': 'Engine tidak berjalan'})

    session_data['stop_event'].set()
    session_data['engine_running'] = False

    return jsonify({'success': True, 'message': 'Engine dihentikan'})


@app.route('/api/status', methods=['GET'])
def api_status():
    return jsonify({
        'success': True,
        'logged_in': session_data['logged_in'],
        'engine_running': session_data['engine_running'],
        'last_check': session_data['last_check'],
        'logs': session_data['logs'][-50:],
        'npm': session_data['npm'],
        'name': session_data['name'],
    })


@app.route('/api/logs/clear', methods=['POST'])
def api_clear_logs():
    session_data['logs'] = []
    return jsonify({'success': True})


# ===== Request Logging =====
@app.before_request
def log_request():
    """Log every incoming request for debugging."""
    logger.info(f'[REQUEST] {request.method} {request.path}')


# ===== Diagnostic Test Endpoint =====
@app.route('/api/test', methods=['GET'])
def api_test():
    """Quick test to verify server is responding."""
    return jsonify({
        'success': True,
        'message': 'Server is running!',
        'time': datetime.now().strftime('%H:%M:%S'),
        'logged_in': session_data['logged_in'],
    })


# ===== Main =====
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 3000))

    print('=' * 50)
    print('  AutoAbsen SimKuliah USK')
    print(f'  Server berjalan di http://localhost:{port}')
    print('=' * 50)

    app.run(host='0.0.0.0', port=port, debug=False, threaded=True)
