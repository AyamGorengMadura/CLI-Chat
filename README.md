<div align="center">

# 💬 cli-chat

**A minimal Python command-line chat application with separate client and server architectures**

Part of [Project Nexus: Dozor](https://github.com/AyamGorengMadura/nexus-core)

![Python](https://img.shields.io/badge/Python-3.10+-3776AB?style=flat-square&logo=python&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-green?style=flat-square)
![Status](https://img.shields.io/badge/Status-Development-yellow?style=flat-square)

</div>

---

## 📖 Overview

`cli-chat` is a lightweight terminal chat project built around a simple client-server model:
- `server.py` — Handles incoming client connections and message distribution.
- `client.py` — Connects to the server and sends/receives messages directly from the terminal.

This repo is intentionally small and easy to understand, making it a good foundation for learning sockets, networking, and real-time messaging in Python.

## 📂 Project Structure

```text
cli-chat/
├── client.py
├── server.py
├── README.md
├── requirements.txt
└── .gitignore
🛠️ Requirements
Python 3.10 or newer

🚀 Setup
1. Clone Repository
Bash
git clone [https://github.com/AyamGorengMadura/cli-chat.git](https://github.com/AyamGorengMadura/cli-chat.git)
cd cli-chat
2. Virtual Environment (Optional)
Linux/macOS:

Bash
python -m venv .venv
source .venv/bin/activate
Windows:

PowerShell
python -m venv .venv
.venv\Scripts\activate
3. Install Dependencies
Bash
pip install -r requirements.txt
💻 Running the App
1. Start the Server
Bash
python server.py
2. Start the Client
Open another terminal window or tab and run:

Bash
python client.py
⚙️ How It Works
The server starts and listens for incoming client connections.

A client connects to the server.

Messages sent by the client are received and processed through the chat flow.

Additional features such as broadcasting, usernames, or rooms can be added on top of this structure.

🗺️ Future Improvements

[ ] Improve connection error handling

[ ] Add tests for networking logic
