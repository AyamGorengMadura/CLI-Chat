# CliChat

Terminal chat app berbasis Python. Server-client architecture dengan persistent user, history, dan sistem admin.

## File

```
clichat/
├── server.py            ← Jalanin duluan di host/VM
├── client.py            ← Jalanin di tiap client
├── chat_history.json    ← Auto-generated, riwayat chat
└── registered_users.json ← Auto-generated, data user
```

## Cara Pakai

### Server
```bash
python server.py
```
Default listen di semua interface (`0.0.0.0:5000`).
Ganti `HOST` dan `PORT` di atas file kalau perlu.

### Client
```bash
# Kalau server di localhost
python client.py

# Kalau server di IP lain (misal VM)
python client.py 192.168.1.10
```

## Fitur

- **Persistent username** — registrasi sekali, login berikutnya pakai password
- **Riwayat chat** — 20 pesan terakhir otomatis muncul waktu join
- **Sistem role** — `user` dan `admin`
- **TUI color-coded** — join hijau, leave kuning, kick merah, sistem cyan
- **DM (Direct Message)** — `/dm <username> <pesan>`

## Perintah

| Perintah | Keterangan |
|---|---|
| `/help` | Tampilkan daftar perintah |
| `/who` | Siapa yang online |
| `/dm <user> <pesan>` | Kirim pesan privat |
| `/history` | Lihat riwayat chat |
| `/keluar` | Keluar dari CliChat |
| `/kick <user>` | *(Admin)* Kick user |
| `/announce <pesan>` | *(Admin)* Broadcast pengumuman |

## Setup Admin

Waktu registrasi, masukkan kode admin (`admin123` by default).
Ganti `ADMIN_PASS` di `server.py` sesuai kebutuhan.

## Dependensi

Cuma stdlib Python, tidak perlu install apapun:
- `socket`, `threading`, `curses`, `json`, `os`, `datetime`

## Integrasi SOK (DHCP/DNS)

Kalau server jalan di VM Ubuntu dengan Bridge Adapter:
1. Set VM ke Bridge → VM dapet IP dari DHCP router
2. Client connect: `python client.py <IP_VM>`
3. Opsional: setup DNS lokal biar bisa `python client.py clichat.local`
