# client.py - buat clichat, chat cli based.
import sys
import subprocess

if sys.platform == 'win32':
    try:
        import _curses
    except ImportError:
        print('Installing windows-curses...')
        subprocess.check_call([sys.executable, '-m', 'pip', 'install', 'windows-curses', '-q'])
        print('Done.\n')

import socket
import threading
import curses
import json
import hashlib
import time
import os

HOST = '' # << Server's IP
PORT = 5000

RECONNECT_DELAY = 3    # detik antar percobaan reconnect
MAX_RECONNECT   = 5    # maksimal percobaan

# ─── Enkripsi (harus sama dengan server) ───────────────────────────────────────
CIPHER_KEY = b'clichat_k3y_2025'

def xor_cipher(data: bytes, key: bytes = CIPHER_KEY) -> bytes:
    return bytes(b ^ key[i % len(key)] for i, b in enumerate(data))

def encrypt(text: str) -> str:
    return xor_cipher(text.encode('utf-8')).hex()

def decrypt(hex_str: str) -> str:
    try:
        return xor_cipher(bytes.fromhex(hex_str)).decode('utf-8')
    except:
        return hex_str

# ─── Warna ─────────────────────────────────────────────────────────────────────

C_DEFAULT = 0
C_SYSTEM  = 1
C_JOIN    = 2
C_LEAVE   = 3
C_KICK    = 4
C_INPUT   = 5
C_HEADER  = 6
C_OWN     = 7
C_DM      = 8    # notif DM
USER_COLOR_PAIRS = list(range(10, 17))
USER_COLORS = [
    curses.COLOR_CYAN, curses.COLOR_GREEN, curses.COLOR_YELLOW,
    curses.COLOR_MAGENTA, curses.COLOR_RED, curses.COLOR_WHITE, curses.COLOR_BLUE,
]

_user_color_cache = {}

def get_user_color(username):
    if username not in _user_color_cache:
        idx = int(hashlib.md5(username.encode()).hexdigest(), 16) % len(USER_COLOR_PAIRS)
        _user_color_cache[username] = USER_COLOR_PAIRS[idx]
    return _user_color_cache[username]

def init_colors():
    curses.start_color()
    curses.use_default_colors()
    curses.init_pair(C_SYSTEM,  curses.COLOR_CYAN,    -1)
    curses.init_pair(C_JOIN,    curses.COLOR_GREEN,   -1)
    curses.init_pair(C_LEAVE,   curses.COLOR_YELLOW,  -1)
    curses.init_pair(C_KICK,    curses.COLOR_RED,     -1)
    curses.init_pair(C_INPUT,   curses.COLOR_WHITE,   -1)
    curses.init_pair(C_HEADER,  curses.COLOR_MAGENTA, -1)
    curses.init_pair(C_OWN,     curses.COLOR_BLUE,    -1)
    curses.init_pair(C_DM,      curses.COLOR_CYAN,    -1)
    for i, color in enumerate(USER_COLORS):
        curses.init_pair(USER_COLOR_PAIRS[i], color, -1)

TAG_COLOR = {
    'system': C_SYSTEM,
    'join':   C_JOIN,
    'leave':  C_LEAVE,
    'kick':   C_KICK,
    None:     C_DEFAULT,
}

# ─── ChatUI ────────────────────────────────────────────────────────────────────

class ChatUI:
    def __init__(self, stdscr, username):
        self.stdscr   = stdscr
        self.username = username
        self.messages = []
        self.input    = ''
        self.history  = []
        self.hist_idx = -1
        self.dm_pending = False      # ada DM belum dibaca
        self.msg_lock = threading.Lock()
        self.lock     = threading.RLock()

        curses.curs_set(1)
        stdscr.keypad(True)
        stdscr.nodelay(False)
        init_colors()
        self._resize()

    def _resize(self):
        self.h, self.w = self.stdscr.getmaxyx()
        self.chat_h    = self.h - 3
        self.chat_w    = self.w

    def draw_header(self):
        dm_flag = ' 📨 DM' if self.dm_pending else ''
        title   = f' CliChat v3  [{self.username}]{dm_flag} '
        line    = title + '─' * max(0, self.w - len(title))
        attr    = curses.color_pair(C_HEADER) | curses.A_BOLD
        if self.dm_pending:
            attr |= curses.A_BLINK
        try:
            self.stdscr.addstr(0, 0, line[:self.w], attr)
        except curses.error:
            pass

    def draw_messages(self):
        with self.msg_lock:
            win_msgs = list(self.messages[-(self.chat_h):])
        for i, (text, cp) in enumerate(win_msgs):
            try:
                self.stdscr.move(1 + i, 0)
                self.stdscr.clrtoeol()
                self.stdscr.addstr(1 + i, 0, text[:self.w - 1], curses.color_pair(cp))
            except curses.error:
                pass
        for i in range(len(win_msgs), self.chat_h):
            try:
                self.stdscr.move(1 + i, 0)
                self.stdscr.clrtoeol()
            except curses.error:
                pass

    def draw_input(self):
        prompt        = f' {self.username} ❯ '
        max_input_w   = self.w - len(prompt) - 1
        display_input = self.input[-max_input_w:] if max_input_w > 0 else ''
        try:
            self.stdscr.move(self.h - 2, 0)
            self.stdscr.clrtoeol()
            self.stdscr.addstr(self.h - 2, 0, prompt, curses.color_pair(C_INPUT) | curses.A_BOLD)
            self.stdscr.addstr(self.h - 2, len(prompt), display_input, curses.color_pair(C_INPUT))
        except curses.error:
            pass

    def draw_status(self):
        hint = ' /help  /who  /dm  /find  /ping  /stats  /status  /keluar '
        try:
            self.stdscr.move(self.h - 1, 0)
            self.stdscr.clrtoeol()
            self.stdscr.addstr(self.h - 1, 0, hint[:self.w - 1], curses.color_pair(C_SYSTEM))
        except curses.error:
            pass

    def _do_refresh(self):
        self.draw_header()
        self.draw_messages()
        self.draw_input()
        self.draw_status()
        prompt_len        = len(f' {self.username} ❯ ')
        max_input_w       = self.w - prompt_len - 1
        visible_input_len = min(len(self.input), max_input_w)
        try:
            self.stdscr.move(self.h - 2, prompt_len + visible_input_len)
        except curses.error:
            pass
        self.stdscr.refresh()

    def refresh(self):
        with self.lock:
            self._do_refresh()

    def add_message(self, text, tag=None, sender=None):
        # Deteksi DM masuk → nyalain notif di header
        is_dm = '[DM ←' in text
        if is_dm:
            self.dm_pending = True

        if tag in TAG_COLOR and tag is not None:
            cp = TAG_COLOR[tag]
        elif is_dm:
            cp = C_DM
        elif sender and sender == self.username:
            cp = C_OWN
        elif sender:
            cp = get_user_color(sender)
        else:
            cp = C_DEFAULT

        with self.msg_lock:
            for line in text.split('\n'):
                while len(line) > self.chat_w - 1:
                    self.messages.append((line[:self.chat_w - 1], cp))
                    line = '  ' + line[self.chat_w - 1:]
                self.messages.append((line, cp))
        with self.lock:
            self._do_refresh()

    # ── Command History ─────────────────────────────────────────────────────────

    def history_up(self):
        if not self.history:
            return
        if self.hist_idx == -1:
            self.hist_idx = len(self.history) - 1
        elif self.hist_idx > 0:
            self.hist_idx -= 1
        self.input = self.history[self.hist_idx]
        self.refresh()

    def history_down(self):
        if self.hist_idx == -1:
            return
        self.hist_idx += 1
        if self.hist_idx >= len(self.history):
            self.hist_idx = -1
            self.input = ''
        else:
            self.input = self.history[self.hist_idx]
        self.refresh()

    def push_history(self, msg):
        if msg and (not self.history or self.history[-1] != msg):
            self.history.append(msg)
        self.hist_idx = -1

# ─── Network ───────────────────────────────────────────────────────────────────

def connect_to_server():
    """Buat koneksi ke server, return socket atau None."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.connect((HOST, PORT))
        return sock
    except Exception as e:
        print(f'  Gagal connect: {e}')
        sock.close()
        return None

def recv_loop(sock, ui, stop_event, reconnect_event):
    buffer = b''
    while not stop_event.is_set():
        try:
            chunk = sock.recv(1024)
            if not chunk:
                if not stop_event.is_set():
                    ui.add_message('Koneksi terputus. Mencoba reconnect...', tag='kick')
                    reconnect_event.set()
                break
            buffer += chunk
            while b'\n' in buffer:
                line, buffer = buffer.split(b'\n', 1)
                try:
                    data = json.loads(line.decode('utf-8'))
                    if data.get('type') == 'ping':
                        pong = json.dumps({'type': 'pong'}) + '\n'
                        sock.send(pong.encode('utf-8'))
                        continue
                    text = data.get('text', '')
                    # Decrypt kalau encrypted
                    if data.get('encrypted'):
                        text = decrypt(text)
                    ui.add_message(text, tag=data.get('tag'), sender=data.get('sender'))
                except:
                    ui.add_message(line.decode('utf-8', errors='replace'))
        except:
            if not stop_event.is_set():
                ui.add_message('Koneksi error. Mencoba reconnect...', tag='kick')
                reconnect_event.set()
            break

def auth_curses(stdscr, sock):
    """
    Auth flow fully inside curses — tidak pakai print() atau input() sama sekali.
    Return username string kalau berhasil, None kalau gagal.
    """
    curses.curs_set(1)
    stdscr.keypad(True)
    stdscr.clear()
    stdscr.scrollok(True)

    h, w     = stdscr.getmaxyx()
    username = None
    buffer   = b''
    log_row  = 0
    MAX_LOG  = h - 3

    def print_line(text):
        nonlocal log_row
        if log_row >= MAX_LOG:
            log_row = MAX_LOG - 1
            stdscr.scroll()
        try:
            stdscr.addstr(log_row, 0, text[:w - 1])
        except curses.error:
            pass
        log_row += 1
        stdscr.refresh()

    def read_input(prompt, hidden=False):
        input_row = min(log_row + 1, h - 2)
        result    = []
        try:
            stdscr.move(input_row, 0)
            stdscr.clrtoeol()
            stdscr.addstr(input_row, 0, prompt)
        except curses.error:
            pass
        stdscr.refresh()
        col = len(prompt)

        while True:
            try:
                key = stdscr.get_wch()
            except curses.error:
                continue

            if key in ('\n', '\r', curses.KEY_ENTER):
                break
            elif key in (curses.KEY_BACKSPACE, '\x7f', '\x08'):
                if result:
                    result.pop()
                    col -= 1
                    try:
                        stdscr.move(input_row, col)
                        stdscr.clrtoeol()
                    except curses.error:
                        pass
                    stdscr.refresh()
            elif isinstance(key, str) and len(key) == 1 and ord(key) >= 32:
                result.append(key)
                if not hidden:
                    try:
                        stdscr.addstr(input_row, col, key)
                    except curses.error:
                        pass
                col += 1
                stdscr.refresh()
            elif isinstance(key, int) and 32 <= key <= 126:
                result.append(chr(key))
                if not hidden:
                    try:
                        stdscr.addstr(input_row, col, chr(key))
                    except curses.error:
                        pass
                col += 1
                stdscr.refresh()

        try:
            stdscr.move(input_row, 0)
            stdscr.clrtoeol()
        except curses.error:
            pass
        return ''.join(result)

    print_line(f'  CliChat v3  —  {HOST}:{PORT}')
    print_line('  ' + '─' * min(36, w - 3))

    while True:
        try:
            chunk = sock.recv(4096)
        except Exception:
            return None
        if not chunk:
            return None
        buffer += chunk

        while b'\n' in buffer:
            line, buffer = buffer.split(b'\n', 1)
            try:
                data = json.loads(line.decode('utf-8'))
                text = data.get('text', '')
                tag  = data.get('tag')
                print_line(f'  {text}')

                needs_input = (
                    'USERNAME:' in text or
                    'PASSWORD:' in text or
                    'password' in text.lower() or
                    'Kode admin' in text
                )
                if needs_input:
                    is_pw = 'PASSWORD:' in text or 'password' in text.lower()
                    val   = read_input('  ❯ ', hidden=is_pw)
                    sock.send((val + '\n').encode('utf-8'))
                    if 'USERNAME:' in text and not username:
                        username = val

                if tag == 'join' and ('berhasil' in text or 'Login' in text or 'Registrasi' in text):
                    return username

                if tag == 'kick' and ('salah' in text or 'ditutup' in text or 'di-ban' in text):
                    print_line('  Koneksi ditutup. Tekan Enter.')
                    stdscr.get_wch()
                    return None

            except json.JSONDecodeError:
                print_line(line.decode('utf-8', errors='replace'))

# ─── Input Loop ────────────────────────────────────────────────────────────────

def input_loop(stdscr, sock_ref, ui, stop_event, reconnect_event):
    """
    sock_ref: list of [sock] — mutable reference supaya bisa di-update
    waktu reconnect tanpa restart loop.
    """
    stdscr.keypad(True)

    while not stop_event.is_set():
        # Kalau lagi reconnect, pause input
        if reconnect_event.is_set():
            time.sleep(0.2)
            continue

        try:
            key = stdscr.get_wch()
        except curses.error:
            continue
        except Exception:
            break

        sock = sock_ref[0]

        if key == curses.KEY_RESIZE:
            ui._resize()
            ui.refresh()

        elif key == curses.KEY_UP:
            ui.history_up()

        elif key == curses.KEY_DOWN:
            ui.history_down()

        elif key in ('\n', '\r', curses.KEY_ENTER):
            msg = ui.input.strip()
            ui.push_history(msg)
            ui.input = ''
            # Clear DM notif kalau user baru ngetik sesuatu
            if ui.dm_pending:
                ui.dm_pending = False
            if msg:
                try:
                    sock.send((msg + '\n').encode('utf-8'))
                except Exception:
                    ui.add_message('Gagal kirim pesan.', tag='kick')
                if msg == '/keluar':
                    stop_event.set()
                    break
            ui.refresh()

        elif key in (curses.KEY_BACKSPACE, '\x7f', '\x08'):
            ui.input = ui.input[:-1]
            ui.refresh()

        elif isinstance(key, int) and key > 255:
            pass

        elif isinstance(key, str) and len(key) == 1 and ord(key) >= 32:
            ui.input += key
            ui.refresh()

        elif isinstance(key, int) and 32 <= key <= 126:
            ui.input += chr(key)
            ui.refresh()

# ─── Reconnect Handler ─────────────────────────────────────────────────────────

def reconnect_loop(sock_ref, ui, username, stop_event, reconnect_event):
    """Thread yang handle reconnect otomatis."""
    while not stop_event.is_set():
        reconnect_event.wait()
        if stop_event.is_set():
            break

        for attempt in range(1, MAX_RECONNECT + 1):
            if stop_event.is_set():
                return
            ui.add_message(f'Reconnect {attempt}/{MAX_RECONNECT}...', tag='system')
            time.sleep(RECONNECT_DELAY)

            new_sock = connect_to_server()
            if not new_sock:
                continue

            # Re-auth otomatis (kirim username + password tersimpan)
            # Untuk simplisitas, notif user untuk re-auth manual lewat prompt
            # Ini limitasi XOR cipher — session baru butuh auth ulang
            ui.add_message('Reconnect berhasil! Silakan login ulang dengan /keluar lalu jalankan ulang client.', tag='join')
            new_sock.close()
            reconnect_event.clear()
            stop_event.set()
            return

        ui.add_message(f'Gagal reconnect setelah {MAX_RECONNECT}x. Keluar.', tag='kick')
        stop_event.set()
        reconnect_event.clear()


# ─── Main ───────────────────────────────────────────────────────────────────────

def main():
    sock = connect_to_server()
    if not sock:
        print(f'Gagal connect ke {HOST}:{PORT}')
        sys.exit(1)

    def run_app(stdscr):
        # Auth dan chat dalam satu curses session — tidak ada print()/input() sama sekali
        username = auth_curses(stdscr, sock)
        if not username:
            sock.close()
            return

        stdscr.clear()
        stdscr.refresh()

        ui              = ChatUI(stdscr, username)
        stop_event      = threading.Event()
        reconnect_event = threading.Event()
        sock_ref        = [sock]

        ui.refresh()

        recv_thread = threading.Thread(
            target=recv_loop,
            args=(sock, ui, stop_event, reconnect_event),
            daemon=True
        )
        recv_thread.start()

        recon_thread = threading.Thread(
            target=reconnect_loop,
            args=(sock_ref, ui, username, stop_event, reconnect_event),
            daemon=True
        )
        recon_thread.start()

        input_loop(stdscr, sock_ref, ui, stop_event, reconnect_event)
        sock.close()

    curses.wrapper(run_app)
    print('Keluar dari CliChat. Dadah!')

if __name__ == '__main__':
    main()
