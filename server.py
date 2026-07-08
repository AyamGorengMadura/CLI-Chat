"""
CliChat Server v3
Usage: python server.py
"""

import socket
import threading
import json
import os
import hashlib
import time
from datetime import datetime
from collections import defaultdict

HOST       = '0.0.0.0'
PORT       = 5000
MAX_HISTORY = 200

HISTORY_FILE = 'chat_history.json'
USERS_FILE   = 'registered_users.json'
BANNED_FILE  = 'banned_users.json'
AUDIT_FILE   = 'audit_log.json'
ADMIN_PASS   = 'admin123'

# Rate limiting
RATE_LIMIT_MSG   = 8    # max pesan per window
RATE_LIMIT_WINDOW = 5   # detik

START_TIME = time.time()

# ─── Enkripsi sederhana (XOR + key rolling) ────────────────────────────────────
# Key dishare antara server dan client, cukup untuk obfuscate plaintext di wire.
CIPHER_KEY = b'clichat_k3y_2025'

def xor_cipher(data: bytes, key: bytes = CIPHER_KEY) -> bytes:
    return bytes(b ^ key[i % len(key)] for i, b in enumerate(data))

def encrypt(text: str) -> str:
    """Text → XOR → hex string."""
    return xor_cipher(text.encode('utf-8')).hex()

def decrypt(hex_str: str) -> str:
    """Hex string → XOR → text."""
    try:
        return xor_cipher(bytes.fromhex(hex_str)).decode('utf-8')
    except:
        return hex_str   # fallback kalau bukan hex (pesan lama/plaintext)

# ─── Helpers ───────────────────────────────────────────────────────────────────

def hash_pw(password):
    return hashlib.sha256(password.encode('utf-8')).hexdigest()

def load_json(path, default):
    if os.path.exists(path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            pass
    return default

def save_json(path, data):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def uptime_str():
    secs     = int(time.time() - START_TIME)
    h, r     = divmod(secs, 3600)
    m, s     = divmod(r, 60)
    return f'{h}j {m}m {s}d'

# ─── Storage ───────────────────────────────────────────────────────────────────

registered_users = load_json(USERS_FILE, {})
chat_history     = load_json(HISTORY_FILE, [])
banned_users     = load_json(BANNED_FILE, [])
audit_log        = load_json(AUDIT_FILE, [])

# Migrate plain text passwords ke hash
_migrated = False
for _u, _d in registered_users.items():
    pw = _d.get('password', '')
    if len(pw) != 64:
        _d['password'] = hash_pw(pw)
        _migrated = True
if _migrated:
    save_json(USERS_FILE, registered_users)

def save_users():   save_json(USERS_FILE, registered_users)
def save_history(): save_json(HISTORY_FILE, chat_history[-MAX_HISTORY:])
def save_banned():  save_json(BANNED_FILE, banned_users)
def save_audit():   save_json(AUDIT_FILE, audit_log[-500:])

def log_action(action, by, target=None, detail=None):
    """Catat aksi moderasi ke audit log."""
    entry = {
        'time':   datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'action': action,
        'by':     by,
        'target': target,
        'detail': detail,
    }
    audit_log.append(entry)
    save_audit()
    # Print ke server console juga
    t_str = f' → {target}' if target else ''
    d_str = f' ({detail})' if detail else ''
    print(f'[AUDIT] {entry["time"]} | {by} {action}{t_str}{d_str}')

# ─── State ─────────────────────────────────────────────────────────────────────

# socket → { username, role, addr, join_time, last_msg_time }
clients    = {}
muted      = set()
lock       = threading.Lock()

# Rate limiting: username → list timestamp pesan
rate_buckets   = {}
rate_lock      = threading.Lock()

def is_rate_limited(username):
    """Return True kalau user kirim pesan terlalu cepat."""
    now = time.time()
    with rate_lock:
        bucket = rate_buckets.get(username, [])
        # Buang timestamp yang udah lewat window
        bucket = [t for t in bucket if now - t < RATE_LIMIT_WINDOW]
        if len(bucket) >= RATE_LIMIT_MSG:
            rate_buckets[username] = bucket
            return True
        bucket.append(now)
        rate_buckets[username] = bucket
        return False

# ── Traffic monitor ────────────────────────────────────────────────────────────
traffic    = {'bytes_in': 0, 'bytes_out': 0, 'msg_count': 0}
traffic_lk = threading.Lock()

def traffic_add(bytes_in=0, bytes_out=0, msg=False):
    with traffic_lk:
        traffic['bytes_in']  += bytes_in
        traffic['bytes_out'] += bytes_out
        if msg:
            traffic['msg_count'] += 1

# ── Slowmode ───────────────────────────────────────────────────────────────────
slowmode_seconds = 0          # 0 = off
last_msg_time    = {}         # username → timestamp pesan terakhir

# ─── Messaging ─────────────────────────────────────────────────────────────────

def make_payload(text, tag=None, sender=None, encrypted=False):
    return json.dumps({
        'type':      'msg',
        'tag':       tag,
        'text':      text,
        'sender':    sender,
        'encrypted': encrypted,
    }) + '\n'

def broadcast(message, exclude=None, tag=None, sender=None, encrypt_msg=False):
    if encrypt_msg:
        wire_text = encrypt(message)
    else:
        wire_text = message

    payload = make_payload(wire_text, tag=tag, sender=sender, encrypted=encrypt_msg)
    raw     = payload.encode('utf-8')
    ts      = datetime.now().strftime('%H:%M:%S')

    with lock:
        chat_history.append({'time': ts, 'text': message, 'tag': tag, 'sender': sender})
        save_history()
        dead = []
        for sock, info in clients.items():
            if sock == exclude:
                continue
            try:
                sock.send(raw)
                traffic_add(bytes_out=len(raw))
            except:
                dead.append(sock)
        for sock in dead:
            _remove(sock)

def send_to(sock, message, tag=None, sender=None, encrypt_msg=False):
    if encrypt_msg:
        wire_text = encrypt(message)
    else:
        wire_text = message
    payload = make_payload(wire_text, tag=tag, sender=sender, encrypted=encrypt_msg)
    raw     = payload.encode('utf-8')
    try:
        sock.send(raw)
        traffic_add(bytes_out=len(raw))
    except:
        pass

def send_history(sock):
    if not chat_history:
        return
    send_to(sock, '─── Riwayat 20 Pesan Terakhir ───', tag='system')
    for entry in chat_history[-20:]:
        text = entry['text']
        if not text.startswith('['):
            text = f"[{entry['time']}] {text}"
        send_to(sock, text, tag=entry.get('tag'), sender=entry.get('sender'))
    send_to(sock, '─────────────────────────────────', tag='system')

# ─── Auth ──────────────────────────────────────────────────────────────────────

def recv_line(sock):
    data = b''
    while True:
        chunk = sock.recv(1)
        if not chunk:
            return None
        traffic_add(bytes_in=1)
        if chunk == b'\n':
            return data.decode('utf-8').strip()
        data += chunk

def auth_flow(sock, addr):
    send_to(sock, 'Selamat datang di CliChat!', tag='join')
    send_to(sock, 'USERNAME:', tag='system')
    username = recv_line(sock)
    if not username:
        return None

    if username in banned_users:
        send_to(sock, 'Akun kamu telah di-ban.', tag='kick')
        return None

    if username in registered_users:
        send_to(sock, f'Halo lagi, {username}! PASSWORD:', tag='system')
        password = recv_line(sock)
        if hash_pw(password) != registered_users[username]['password']:
            send_to(sock, 'Password salah. Koneksi ditutup.', tag='kick')
            return None
        role = registered_users[username]['role']
        send_to(sock, f'Login berhasil sebagai {role}.', tag='join')
    else:
        send_to(sock, 'Username baru. Buat PASSWORD:', tag='system')
        password = recv_line(sock)
        if not password:
            return None
        send_to(sock, 'Kode admin (Enter kalau bukan admin):', tag='system')
        admin_code = recv_line(sock)
        role = 'admin' if admin_code == ADMIN_PASS else 'user'
        registered_users[username] = {
            'password': hash_pw(password),
            'role':     role,
            'status':   '',
        }
        save_users()
        send_to(sock, f'Registrasi berhasil! Role: {role}', tag='join')

    return username, role

# ─── Ping ──────────────────────────────────────────────────────────────────────

def measure_rtt(sock):
    try:
        ping_payload = json.dumps({'type': 'ping'}) + '\n'
        t0 = time.time()
        sock.send(ping_payload.encode('utf-8'))
        sock.settimeout(3)
        buf = b''
        while True:
            chunk = sock.recv(256)
            if not chunk:
                break
            buf += chunk
            if b'\n' in buf:
                line = buf.split(b'\n')[0]
                try:
                    data = json.loads(line)
                    if data.get('type') == 'pong':
                        rtt = (time.time() - t0) * 1000
                        sock.settimeout(None)
                        return rtt
                except:
                    pass
                break
        sock.settimeout(None)
    except:
        pass
    return None

# ─── Command Handler ────────────────────────────────────────────────────────────

def handle_command(cmd, sock):
    global slowmode_seconds

    info     = clients[sock]
    username = info['username']
    role     = info['role']
    parts    = cmd.strip().split(None, 2)
    cmd_name = parts[0].lower()

    # /help
    if cmd_name == '/help':
        lines = [
            '┌── Perintah CliChat ────────────────────────────┐',
            '  /help                    Tampilkan ini',
            '  /who                     Siapa yang online',
            '  /dm <user> <pesan>       Pesan privat',
            '  /history                 Riwayat chat',
            '  /find <keyword>          Cari di history',
            '  /ping <user>             Cek latensi ke user',
            '  /status <teks>           Set status kamu',
            '  /uptime                  Uptime server',
            '  /stats                   Traffic server',
            '  /keluar                  Keluar',
        ]
        if role == 'admin':
            lines += [
                '  ── Admin ──────────────────────────────────',
                '  /kick <user>             Kick user',
                '  /ban <user>              Ban user (permanen)',
                '  /unban <user>            Unban user',
                '  /mute <user>             Mute user',
                '  /unmute <user>           Unmute user',
                '  /whois <user>            Info detail user',
                '  /slowmode <detik>        Set slowmode (0=off)',
                '  /announce <pesan>        Broadcast sistem',
                '  /modlog [n]              Audit log (default 10)',
            ]
        lines.append('└────────────────────────────────────────────────┘')
        send_to(sock, '\n'.join(lines), tag='system')

    # /who
    elif cmd_name == '/who':
        sm_info = f' | slowmode: {slowmode_seconds}d' if slowmode_seconds > 0 else ''
        with lock:
            lines = []
            for v in clients.values():
                status   = registered_users.get(v['username'], {}).get('status', '')
                mute_tag = ' [muted]' if v['username'] in muted else ''
                status_str = f' — {status}' if status else ''
                lines.append(f"  • {v['username']} [{v['role']}]{mute_tag}{status_str}")
        send_to(sock, f'Online ({len(lines)}){sm_info}:\n' + '\n'.join(lines), tag='system')

    # /dm
    elif cmd_name == '/dm' and len(parts) >= 3:
        target_name = parts[1]
        msg         = parts[2]
        with lock:
            target_sock = next((s for s, v in clients.items() if v['username'] == target_name), None)
        if target_sock:
            send_to(target_sock, f'[DM ← {username}] {msg}', tag='system')
            send_to(sock,        f'[DM → {target_name}] {msg}', tag='system')
        else:
            send_to(sock, f'"{target_name}" tidak ditemukan atau offline.', tag='kick')

    # /history
    elif cmd_name == '/history':
        send_history(sock)

    # /find
    elif cmd_name == '/find' and len(parts) >= 2:
        keyword = parts[1].lower()
        results = [e['text'] for e in chat_history if keyword in e.get('text', '').lower()][-20:]
        if results:
            send_to(sock, f'Hasil "{keyword}":\n' + '\n'.join(f'  {r}' for r in results), tag='system')
        else:
            send_to(sock, f'Tidak ada hasil untuk "{keyword}".', tag='system')

    # /ping
    elif cmd_name == '/ping' and len(parts) >= 2:
        target_name = parts[1]
        with lock:
            target_sock = next((s for s, v in clients.items() if v['username'] == target_name), None)
        if not target_sock:
            send_to(sock, f'"{target_name}" tidak ditemukan.', tag='kick')
            return
        rtt = measure_rtt(target_sock)
        if rtt is not None:
            send_to(sock, f'Ping ke {target_name}: {rtt:.1f} ms', tag='system')
        else:
            send_to(sock, f'Ping ke {target_name} timeout.', tag='kick')

    # /status
    elif cmd_name == '/status':
        raw        = cmd.strip()
        new_status = raw[len('/status'):].strip()
        registered_users[username]['status'] = new_status
        save_users()
        send_to(sock, f'Status: "{new_status}"', tag='system')

    # /uptime
    elif cmd_name == '/uptime':
        send_to(sock, f'Uptime: {uptime_str()}', tag='system')

    # /stats — traffic monitor
    elif cmd_name == '/stats':
        with traffic_lk:
            b_in   = traffic['bytes_in']
            b_out  = traffic['bytes_out']
            msgs   = traffic['msg_count']
        elapsed = max(1, time.time() - START_TIME)
        stats = (
            f'Traffic Monitor:\n'
            f'  Uptime      : {uptime_str()}\n'
            f'  Pesan       : {msgs}\n'
            f'  Data masuk  : {b_in/1024:.2f} KB ({b_in/elapsed:.1f} B/s)\n'
            f'  Data keluar : {b_out/1024:.2f} KB ({b_out/elapsed:.1f} B/s)\n'
            f'  Client online: {len(clients)}'
        )
        send_to(sock, stats, tag='system')

    # /keluar
    elif cmd_name == '/keluar':
        send_to(sock, 'Sampai jumpa!', tag='leave')
        raise ConnectionAbortedError('keluar')

    # /kick
    elif cmd_name == '/kick' and len(parts) >= 2:
        if role != 'admin':
            send_to(sock, 'Akses ditolak.', tag='kick'); return
        target_name = parts[1]
        with lock:
            target_sock = next((s for s, v in clients.items() if v['username'] == target_name), None)
        if target_sock:
            send_to(target_sock, 'Kamu di-kick oleh admin.', tag='kick')
            broadcast(f'{target_name} di-kick oleh admin.', tag='kick')
            log_action('KICK', username, target=target_name)
            with lock: _remove(target_sock)
        else:
            send_to(sock, f'"{target_name}" tidak ditemukan.', tag='kick')

    # /ban
    elif cmd_name == '/ban' and len(parts) >= 2:
        if role != 'admin':
            send_to(sock, 'Akses ditolak.', tag='kick'); return
        target_name = parts[1]
        if target_name not in banned_users:
            banned_users.append(target_name)
            save_banned()
        with lock:
            target_sock = next((s for s, v in clients.items() if v['username'] == target_name), None)
        if target_sock:
            send_to(target_sock, 'Kamu di-ban oleh admin.', tag='kick')
            broadcast(f'{target_name} di-ban oleh admin.', tag='kick')
            log_action('BAN', username, target=target_name)
            with lock: _remove(target_sock)
        else:
            send_to(sock, f'{target_name} di-ban (offline).', tag='system')

    # /unban
    elif cmd_name == '/unban' and len(parts) >= 2:
        if role != 'admin':
            send_to(sock, 'Akses ditolak.', tag='kick'); return
        target_name = parts[1]
        if target_name in banned_users:
            banned_users.remove(target_name)
            save_banned()
            log_action('UNBAN', username, target=target_name)
            send_to(sock, f'{target_name} di-unban.', tag='system')
        else:
            send_to(sock, f'{target_name} tidak ada di daftar ban.', tag='kick')

    # /mute
    elif cmd_name == '/mute' and len(parts) >= 2:
        if role != 'admin':
            send_to(sock, 'Akses ditolak.', tag='kick'); return
        target_name = parts[1]
        muted.add(target_name)
        log_action('MUTE', username, target=target_name)
        with lock:
            target_sock = next((s for s, v in clients.items() if v['username'] == target_name), None)
        if target_sock:
            send_to(target_sock, 'Kamu di-mute oleh admin.', tag='kick')
        send_to(sock, f'{target_name} di-mute.', tag='system')

    # /unmute
    elif cmd_name == '/unmute' and len(parts) >= 2:
        if role != 'admin':
            send_to(sock, 'Akses ditolak.', tag='kick'); return
        target_name = parts[1]
        muted.discard(target_name)
        log_action('UNMUTE', username, target=target_name)
        with lock:
            target_sock = next((s for s, v in clients.items() if v['username'] == target_name), None)
        if target_sock:
            send_to(target_sock, 'Kamu di-unmute. Bisa kirim pesan lagi.', tag='join')
        send_to(sock, f'{target_name} di-unmute.', tag='system')

    # /whois
    elif cmd_name == '/whois' and len(parts) >= 2:
        if role != 'admin':
            send_to(sock, 'Akses ditolak.', tag='kick'); return
        target_name = parts[1]
        with lock:
            target_info = next((v for v in clients.values() if v['username'] == target_name), None)
        user_data = registered_users.get(target_name)
        if target_info:
            addr     = target_info['addr']
            joined   = target_info.get('join_time', '-')
            status   = user_data.get('status', '-') if user_data else '-'
            is_muted = 'Ya' if target_name in muted else 'Tidak'
            send_to(sock, (
                f'Whois: {target_name}\n'
                f'  IP      : {addr[0]}:{addr[1]}\n'
                f'  Role    : {target_info["role"]}\n'
                f'  Join    : {joined}\n'
                f'  Status  : {status}\n'
                f'  Muted   : {is_muted}'
            ), tag='system')
        elif user_data:
            send_to(sock, f'{target_name} terdaftar tapi offline.', tag='system')
        else:
            send_to(sock, f'{target_name} tidak ditemukan.', tag='kick')

    # /slowmode
    elif cmd_name == '/slowmode' and len(parts) >= 2:
        if role != 'admin':
            send_to(sock, 'Akses ditolak.', tag='kick'); return
        try:
            secs = int(parts[1])
            if secs < 0:
                raise ValueError
        except ValueError:
            send_to(sock, 'Format: /slowmode <detik> (angka >= 0)', tag='kick')
            return
        slowmode_seconds = secs
        log_action('SLOWMODE', username, detail=f'{secs}d')
        if secs == 0:
            broadcast('Slowmode dimatikan.', tag='system')
        else:
            broadcast(f'Slowmode aktif: {secs} detik antar pesan.', tag='system')

    # /modlog
    elif cmd_name == '/modlog':
        if role != 'admin':
            send_to(sock, 'Akses ditolak.', tag='kick'); return
        try:
            n = int(parts[1]) if len(parts) >= 2 else 10
            n = max(1, min(n, 50))
        except:
            n = 10
        if not audit_log:
            send_to(sock, 'Audit log kosong.', tag='system')
        else:
            lines = ['Audit Log:']
            for e in audit_log[-n:]:
                t_str = f' → {e["target"]}' if e.get("target") else ''
                d_str = f' ({e["detail"]})' if e.get("detail") else ''
                lines.append(f'  [{e["time"]}] {e["by"]} {e["action"]}{t_str}{d_str}')
            send_to(sock, '\n'.join(lines), tag='system')

    # /announce
    elif cmd_name == '/announce' and len(parts) >= 2:
        if role != 'admin':
            send_to(sock, 'Akses ditolak.', tag='kick'); return
        msg = ' '.join(parts[1:])
        broadcast(f'📢 [PENGUMUMAN] {msg}', tag='system')

    else:
        send_to(sock, 'Perintah tidak dikenal. /help untuk bantuan.', tag='kick')

# ─── Client Handler ─────────────────────────────────────────────────────────────

def _remove(sock):
    if sock in clients:
        info = clients.pop(sock)
        try: sock.close()
        except: pass
        return info
    return None

def handle_client(sock, addr):
    print(f'[+] Koneksi baru: {addr}')
    try:
        result = auth_flow(sock, addr)
        if not result:
            sock.close()
            return
        username, role = result

        join_time = datetime.now().strftime('%H:%M:%S')
        with lock:
            clients[sock] = {
                'username':  username,
                'role':      role,
                'addr':      addr,
                'join_time': join_time,
            }
        last_msg_time[username] = 0

        send_history(sock)
        ts = datetime.now().strftime('%H:%M')
        broadcast(f'[{ts}] ✦ {username} bergabung!', exclude=sock, tag='join', sender=username)
        print(f'[+] {username} ({role}) dari {addr}')

        buffer = b''
        while True:
            chunk = sock.recv(1024)
            if not chunk:
                break
            traffic_add(bytes_in=len(chunk))
            buffer += chunk
            while b'\n' in buffer:
                line, buffer = buffer.split(b'\n', 1)
                try:
                    data = json.loads(line.decode('utf-8'))
                    if data.get('type') == 'pong':
                        continue
                    # Decrypt kalau encrypted
                    raw_text = data.get('text', '')
                    if data.get('encrypted'):
                        raw_text = decrypt(raw_text)
                    msg = raw_text.strip()
                except:
                    msg = line.decode('utf-8').strip()

                if not msg:
                    continue

                if msg.startswith('/'):
                    handle_command(msg, sock)
                else:
                    if username in muted:
                        send_to(sock, 'Kamu sedang di-mute.', tag='kick')
                        continue

                    # Rate limiting (anti-spam otomatis)
                    if role != 'admin' and is_rate_limited(username):
                        send_to(sock, f'Terlalu cepat! Maks {RATE_LIMIT_MSG} pesan per {RATE_LIMIT_WINDOW} detik.', tag='kick')
                        continue

                    # Slowmode check
                    if slowmode_seconds > 0 and role != 'admin':
                        elapsed = time.time() - last_msg_time.get(username, 0)
                        if elapsed < slowmode_seconds:
                            sisa = slowmode_seconds - elapsed
                            send_to(sock, f'Slowmode: tunggu {sisa:.1f}d lagi.', tag='kick')
                            continue

                    last_msg_time[username] = time.time()
                    ts  = datetime.now().strftime('%H:%M:%S')
                    out = f'[{ts}] {username}: {msg}'
                    broadcast(out, exclude=sock, sender=username, encrypt_msg=True)
                    send_to(sock, out, sender=username, encrypt_msg=True)
                    traffic_add(msg=True)
                    print(out)

    except ConnectionAbortedError:
        pass
    except Exception as e:
        print(f'[-] Error: {e}')
    finally:
        with lock:
            info = _remove(sock)
        if info:
            muted.discard(info['username'])
            last_msg_time.pop(info['username'], None)
            ts = datetime.now().strftime('%H:%M:%S')
            broadcast(f'[{ts}] ✦ {info["username"]} keluar.', tag='leave')
            print(f'[-] {info["username"]} disconnect.')

# ─── Main ───────────────────────────────────────────────────────────────────────

def main():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((HOST, PORT))
    server.listen()
    print('╔══ CliChat Server v3 ═══════════════╗')
    print(f'║  Host  : {HOST}:{PORT}')
    print(f'║  Admin : {ADMIN_PASS}')
    print(f'║  Enkripsi: XOR cipher aktif')
    print('╚════════════════════════════════════╝')

    try:
        while True:
            sock, addr = server.accept()
            t = threading.Thread(target=handle_client, args=(sock, addr), daemon=True)
            t.start()
    except KeyboardInterrupt:
        print('\nServer dimatikan.')
    finally:
        server.close()

if __name__ == '__main__':
    main()
