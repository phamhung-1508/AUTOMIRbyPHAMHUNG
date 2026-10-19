import sys
import os
import socket
import threading
import json
import sqlite3
import hashlib
import winreg
import subprocess
import ctypes
import re
import html
import time
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime

from embedded_tailscale import EmbeddedTailscale

def is_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False

if not is_admin():
    try:
        if getattr(sys, 'frozen', False):
            exe_path = sys.executable
            args = ""
        else:
            exe_path = sys.executable
            args = f'"{os.path.abspath(__file__)}"'
        ret = ctypes.windll.shell32.ShellExecuteW(None, "runas", exe_path, args, None, 1)
        if ret > 32:
            sys.exit(0)
    except Exception:
        pass

def setup_windows_firewall(port=9999):
    rule_name = "Mir4_Master_Socket_Server"
    try:
        chk_cmd = f'netsh advfirewall firewall show rule name="{rule_name}"'
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE
        res = subprocess.run(chk_cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, startupinfo=startupinfo)
        if res.returncode != 0 or rule_name.lower() not in res.stdout.decode('latin1', errors='ignore').lower():
            in_cmd = f'netsh advfirewall firewall add rule name="{rule_name}" dir=in action=allow protocol=TCP localport={port}'
            out_cmd = f'netsh advfirewall firewall add rule name="{rule_name}" dir=out action=allow protocol=TCP localport={port}'
            subprocess.run(in_cmd, shell=True, startupinfo=startupinfo)
            subprocess.run(out_cmd, shell=True, startupinfo=startupinfo)
    except Exception:
        pass

def get_machine_ips(ts_client=None):
    lan_ip = "127.0.0.1"
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        lan_ip = s.getsockname()[0]
        s.close()
    except Exception:
        pass

    vpn_ip = ""
    if ts_client:
        vpn_ip = ts_client.get_my_ip()

    wan_ip = ""
    try:
        req = urllib.request.Request("https://api.ipify.org", headers={'User-Agent': 'Mozilla/5.0'})
        wan_ip = urllib.request.urlopen(req, timeout=3).read().decode('utf8').strip()
    except Exception:
        pass
    return lan_ip, vpn_ip, wan_ip

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QTableWidget, QTableWidgetItem,
    QHeaderView, QMessageBox, QFrame, QCheckBox, QAbstractItemView,
    QMenu, QAction, QTabWidget, QTextEdit, QCompleter
)
from PyQt5.QtCore import Qt, pyqtSignal, QObject, QThread, QStringListModel
from PyQt5.QtGui import QFont, QColor, QIcon

SERVER_PORT = 9999
DB_NAME = "master_users.db"
APP_NAME = "Mir4MasterManager"
TASK_NAME = "Mir4MasterManager_AutoStart"
PREF_FILE = "login_pref.json"
TRACKER_DB = "mir4_tracker_history.db"

def get_app_dir():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

def init_db():
    db_path = os.path.join(get_app_dir(), DB_NAME)
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            username TEXT PRIMARY KEY,
            password_hash TEXT
        )
    ''')
    conn.commit()
    conn.close()

def init_tracker_db():
    db_path = os.path.join(get_app_dir(), TRACKER_DB)
    conn = sqlite3.connect(db_path, timeout=30.0)
    c = conn.cursor()
    c.execute("PRAGMA journal_mode=WAL;")
    c.execute('''CREATE TABLE IF NOT EXISTS characters (
                    char_name TEXT PRIMARY KEY,
                    current_server TEXT,
                    current_clan TEXT,
                    rank TEXT,
                    power TEXT,
                    level TEXT,
                    last_updated TEXT
                )''')
    c.execute('''CREATE TABLE IF NOT EXISTS clan_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    char_name TEXT,
                    clan_name TEXT,
                    timestamp TEXT
                )''')
    c.execute('''CREATE TABLE IF NOT EXISTS server_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    char_name TEXT,
                    server_name TEXT,
                    timestamp TEXT
                )''')
    c.execute("CREATE INDEX IF NOT EXISTS idx_char_name ON characters(char_name);")
    c.execute("CREATE INDEX IF NOT EXISTS idx_char_clan_hist ON clan_history(char_name);")
    c.execute("CREATE INDEX IF NOT EXISTS idx_char_srv_hist ON server_history(char_name);")
    conn.commit()
    conn.close()

def prune_old_tracker_records(conn=None):
    should_close = False
    try:
        if conn is None:
            db_path = os.path.join(get_app_dir(), TRACKER_DB)
            conn = sqlite3.connect(db_path, timeout=15.0)
            should_close = True

        c = conn.cursor()
        c.execute("DELETE FROM clan_history WHERE timestamp < datetime('now', '-100 days')")
        c.execute("DELETE FROM server_history WHERE timestamp < datetime('now', '-100 days')")
        c.execute("DELETE FROM characters WHERE last_updated < datetime('now', '-100 days')")
        conn.commit()
    except Exception:
        pass
    finally:
        if should_close and conn:
            conn.close()

def get_searched_char_names():
    try:
        db_path = os.path.join(get_app_dir(), TRACKER_DB)
        conn = sqlite3.connect(db_path, timeout=5.0)
        c = conn.cursor()
        c.execute("SELECT char_name FROM characters ORDER BY last_updated DESC LIMIT 500")
        rows = c.fetchall()
        conn.close()
        return [r[0] for r in rows if r[0]]
    except Exception:
        return []

def hash_pw(password):
    return hashlib.sha256(password.encode('utf-8')).hexdigest()

def register_user(username, password):
    if not username or not password:
        return False, "Tài khoản và mật khẩu không được để trống!"
    db_path = os.path.join(get_app_dir(), DB_NAME)
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    try:
        c.execute("INSERT INTO users VALUES (?, ?)", (username.strip().lower(), hash_pw(password)))
        conn.commit()
        return True, "Tạo tài khoản thành công!"
    except sqlite3.IntegrityError:
        return False, "Tên tài khoản này đã tồn tại!"
    finally:
        conn.close()

def authenticate_user(username, password):
    db_path = os.path.join(get_app_dir(), DB_NAME)
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("SELECT password_hash FROM users WHERE username = ?", (username.strip().lower(),))
    row = c.fetchone()
    conn.close()
    return bool(row and row[0] == hash_pw(password))

def set_autostart(enable=True):
    try:
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE

        if enable:
            if getattr(sys, 'frozen', False):
                exe_path = sys.executable
            else:
                exe_path = os.path.abspath(sys.argv[0])

            create_cmd = f'schtasks /create /tn "{TASK_NAME}" /tr "\"{exe_path}\"" /sc onlogon /rl highest /f'
            subprocess.run(create_cmd, shell=True, startupinfo=startupinfo)

            key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_ALL_ACCESS)
            cmd = f'"{exe_path}"' if exe_path.endswith('.exe') else f'"{sys.executable}" "{exe_path}"'
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, cmd)
            winreg.CloseKey(key)
        else:
            del_cmd = f'schtasks /delete /tn "{TASK_NAME}" /f'
            subprocess.run(del_cmd, shell=True, startupinfo=startupinfo)

            key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
            try:
                key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_ALL_ACCESS)
                winreg.DeleteValue(key, APP_NAME)
                winreg.CloseKey(key)
            except FileNotFoundError:
                pass
        return True
    except Exception:
        return False

def is_autostart_enabled():
    try:
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE
        chk_cmd = f'schtasks /query /tn "{TASK_NAME}"'
        res = subprocess.run(chk_cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, startupinfo=startupinfo)
        if res.returncode == 0:
            return True

        key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_READ)
        winreg.QueryValueEx(key, APP_NAME)
        winreg.CloseKey(key)
        return True
    except Exception:
        return False

class MasterServerSignals(QObject):
    client_updated = pyqtSignal(dict)
    client_disconnected = pyqtSignal(str)

class MasterServer(threading.Thread):
    def __init__(self, current_username, signals):
        super().__init__(daemon=True)
        self.current_username = current_username.strip().lower()
        self.signals = signals
        self.running = False
        self.active_sockets = {}
        self.lock = threading.Lock()

    def run(self):
        self.running = True
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(('0.0.0.0', SERVER_PORT))
        server.listen(100)

        while self.running:
            try:
                conn, addr = server.accept()
                threading.Thread(target=self.handle_client, args=(conn, addr), daemon=True).start()
            except Exception:
                break

    def handle_client(self, conn, addr):
        client_id = None
        buffer = ""
        try:
            while self.running:
                data = conn.recv(2048).decode('utf-8')
                if not data:
                    break
                buffer += data
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        payload = json.loads(line)
                    except Exception:
                        continue
                    
                    req_master = payload.get("master_user", "").strip().lower()
                    if req_master != self.current_username:
                        resp = json.dumps({"status": "REJECTED", "msg": "Sai tai khoan quan tri"}) + "\n"
                        conn.sendall(resp.encode('utf-8'))
                        conn.close()
                        return

                    client_id = payload.get("node_name", f"Node_{addr[0]}").strip()
                    with self.lock:
                        self.active_sockets[client_id] = conn

                    qt_val = payload.get("qt", False)
                    qt_status = qt_val if isinstance(qt_val, str) else ("ON" if qt_val else "OFF")

                    tinker_val = payload.get("tinker", False)
                    tinker_status = tinker_val if isinstance(tinker_val, str) else ("ON" if tinker_val else "OFF")

                    node_data = {
                        "node_name": client_id,
                        "ip": addr[0],
                        "status": "ONLINE",
                        "qt_status": qt_status,
                        "tinker_status": tinker_status,
                        "uptime": payload.get("uptime", "N/A"),
                        "last_seen": datetime.now().strftime("%H:%M:%S")
                    }
                    self.signals.client_updated.emit(node_data)
        except Exception:
            pass
        finally:
            with self.lock:
                if client_id in self.active_sockets:
                    del self.active_sockets[client_id]
            conn.close()
            if client_id:
                self.signals.client_disconnected.emit(client_id)

    def send_command(self, node_name, command_str):
        with self.lock:
            conn = self.active_sockets.get(node_name)
            if conn:
                try:
                    payload = json.dumps({"command": command_str}) + "\n"
                    conn.sendall(payload.encode('utf-8'))
                    return True
                except Exception:
                    return False
        return False

# ==================== MODULE BÓC TÁCH MÁY CHỦ MIR4 ====================

def get_live_mir4_server_list():
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8"
    }
    extracted_servers = []
    try:
        req = urllib.request.Request("https://forum.mir4global.com/rank?ranktype=1", headers=headers)
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status == 200:
                raw_html = resp.read().decode('utf-8', errors='ignore')
                matches = re.findall(r'worldgroupid=([0-9]+)&amp;worldid=([0-9]+)[^>]*>([^<]+)</a>', raw_html, re.IGNORECASE)
                if not matches:
                    matches = re.findall(r'worldgroupid=([0-9]+)&worldid=([0-9]+)[^>]*>([^<]+)</a>', raw_html, re.IGNORECASE)
                
                for g_id, w_id, s_name in matches:
                    clean_name = s_name.strip()
                    if clean_name and clean_name.lower() != "all":
                        extracted_servers.append({
                            "group_id": int(g_id),
                            "world_id": int(w_id),
                            "name": clean_name
                        })
    except Exception:
        pass

    if not extracted_servers:
        extracted_servers = [
            {"group_id": 1, "world_id": 811, "name": "ASIA011"},
            {"group_id": 1, "world_id": 812, "name": "ASIA012"},
            {"group_id": 1, "world_id": 821, "name": "ASIA021"},
            {"group_id": 1, "world_id": 822, "name": "ASIA022"},
            {"group_id": 1, "world_id": 823, "name": "ASIA023"},
            {"group_id": 1, "world_id": 824, "name": "ASIA024"},
            {"group_id": 1, "world_id": 831, "name": "ASIA031"},
            {"group_id": 1, "world_id": 832, "name": "ASIA032"},
            {"group_id": 1, "world_id": 833, "name": "ASIA033"},
            {"group_id": 1, "world_id": 834, "name": "ASIA034"},
            {"group_id": 1, "world_id": 841, "name": "ASIA041"},
            {"group_id": 1, "world_id": 842, "name": "ASIA042"},
            {"group_id": 2, "world_id": 921, "name": "ASIA21"},
            {"group_id": 2, "world_id": 922, "name": "ASIA22"},
            {"group_id": 2, "world_id": 923, "name": "ASIA23"},
            {"group_id": 2, "world_id": 931, "name": "ASIA31"},
            {"group_id": 2, "world_id": 932, "name": "ASIA32"},
            {"group_id": 2, "world_id": 933, "name": "ASIA33"},
            {"group_id": 3, "world_id": 941, "name": "ASIA321"},
            {"group_id": 3, "world_id": 942, "name": "ASIA322"},
            {"group_id": 3, "world_id": 943, "name": "ASIA323"},
            {"group_id": 3, "world_id": 951, "name": "ASIA331"},
            {"group_id": 3, "world_id": 952, "name": "ASIA332"},
            {"group_id": 3, "world_id": 953, "name": "ASIA333"},
            {"group_id": 3, "world_id": 954, "name": "ASIA334"},
            {"group_id": 11, "world_id": 411, "name": "INMENA011"},
            {"group_id": 11, "world_id": 412, "name": "INMENA012"},
            {"group_id": 11, "world_id": 421, "name": "INMENA021"},
            {"group_id": 21, "world_id": 711, "name": "EU011"},
            {"group_id": 21, "world_id": 712, "name": "EU012"},
            {"group_id": 21, "world_id": 714, "name": "EU014"},
            {"group_id": 21, "world_id": 721, "name": "EU021"},
            {"group_id": 21, "world_id": 722, "name": "EU022"},
            {"group_id": 21, "world_id": 723, "name": "EU023"},
            {"group_id": 21, "world_id": 724, "name": "EU024"},
            {"group_id": 31, "world_id": 511, "name": "SA011"},
            {"group_id": 31, "world_id": 512, "name": "SA012"},
            {"group_id": 31, "world_id": 513, "name": "SA013"},
            {"group_id": 31, "world_id": 514, "name": "SA014"},
            {"group_id": 31, "world_id": 521, "name": "SA021"},
            {"group_id": 31, "world_id": 522, "name": "SA022"},
            {"group_id": 31, "world_id": 523, "name": "SA023"},
            {"group_id": 41, "world_id": 611, "name": "NA011"},
            {"group_id": 41, "world_id": 612, "name": "NA012"},
            {"group_id": 41, "world_id": 613, "name": "NA013"},
            {"group_id": 41, "world_id": 614, "name": "NA014"},
            {"group_id": 41, "world_id": 621, "name": "NA021"},
            {"group_id": 41, "world_id": 622, "name": "NA022"},
            {"group_id": 41, "world_id": 623, "name": "NA023"},
            {"group_id": 41, "world_id": 631, "name": "NA031"},
            {"group_id": 41, "world_id": 632, "name": "NA032"},
            {"group_id": 41, "world_id": 633, "name": "NA033"},
            {"group_id": 99, "world_id": 991, "name": "BASIA011"},
            {"group_id": 99, "world_id": 992, "name": "BASIA012"},
            {"group_id": 99, "world_id": 993, "name": "BASIA013"},
            {"group_id": 99, "world_id": 994, "name": "BINMENA021"},
            {"group_id": 99, "world_id": 995, "name": "BEU031"},
            {"group_id": 99, "world_id": 996, "name": "BSA041"},
            {"group_id": 99, "world_id": 997, "name": "BNA051"}
        ]
    return extracted_servers

def parse_mir4_table_rows(html_content, target_server):
    results = []
    if not html_content:
        return results

    try:
        tr_blocks = re.findall(r'<tr[^>]*>(.*?)</tr>', html_content, re.DOTALL | re.IGNORECASE)
        for tr in tr_blocks:
            td_texts = re.findall(r'<td[^>]*>(.*?)</td>', tr, re.DOTALL | re.IGNORECASE)
            td_cleaned = []
            for td in td_texts:
                clean_td = re.sub(r'<[^>]+>', ' ', td)
                clean_td = html.unescape(clean_td).strip()
                clean_td = ' '.join(clean_td.split())
                td_cleaned.append(clean_td)

            if len(td_cleaned) >= 5:
                rank_raw = td_cleaned[0].replace("NEW", "").strip()
                name = td_cleaned[1]
                server = td_cleaned[2] if td_cleaned[2] else target_server
                clan = td_cleaned[3] if td_cleaned[3] else "Không có Clan"
                power = td_cleaned[4]
                results.append({
                    "rank": rank_raw,
                    "name": name,
                    "server": server,
                    "clan": clan,
                    "power": power,
                    "level": "Theo bảng Lực chiến"
                })
            elif len(td_cleaned) == 4:
                rank_raw = td_cleaned[0].replace("NEW", "").strip()
                name = td_cleaned[1]
                clan = td_cleaned[2] if td_cleaned[2] else "Không có Clan"
                power = td_cleaned[3]
                results.append({
                    "rank": rank_raw,
                    "name": name,
                    "server": target_server,
                    "clan": clan,
                    "power": power,
                    "level": "Theo bảng Lực chiến"
                })
    except Exception:
        pass
    return results

def save_character_record(data_obj, conn=None):
    should_close = False
    try:
        if conn is None:
            db_path = os.path.join(get_app_dir(), TRACKER_DB)
            conn = sqlite3.connect(db_path, timeout=10.0)
            should_close = True

        c = conn.cursor()
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        c_name = data_obj.get("name", "").strip()
        if not c_name:
            if should_close:
                conn.close()
            return

        server = data_obj.get("server", "Chưa xác định").strip()
        clan = data_obj.get("clan", "Không có Clan").strip()
        rank = str(data_obj.get("rank", "N/A")).strip()
        power = str(data_obj.get("power", "N/A")).strip()
        level = str(data_obj.get("level", "N/A")).strip()

        c.execute("SELECT current_server, current_clan FROM characters WHERE LOWER(char_name) = LOWER(?)", (c_name,))
        row = c.fetchone()

        if not row:
            c.execute("INSERT INTO characters VALUES (?, ?, ?, ?, ?, ?, ?)",
                      (c_name, server, clan, rank, power, level, now_str))
            if server and server != "Chưa xác định":
                c.execute("INSERT INTO server_history (char_name, server_name, timestamp) VALUES (?, ?, ?)",
                          (c_name, server, now_str))
            if clan:
                c.execute("INSERT INTO clan_history (char_name, clan_name, timestamp) VALUES (?, ?, ?)",
                          (c_name, clan, now_str))
        else:
            old_server, old_clan = row
            if server and server != "Chưa xác định" and server.lower() != (old_server or "").lower():
                c.execute("SELECT server_name FROM server_history WHERE LOWER(char_name)=LOWER(?) ORDER BY id DESC LIMIT 1", (c_name,))
                last_srv = c.fetchone()
                if not last_srv or last_srv[0].lower() != server.lower():
                    c.execute("INSERT INTO server_history (char_name, server_name, timestamp) VALUES (?, ?, ?)",
                              (c_name, server, now_str))

            if clan and clan.lower() != (old_clan or "").lower():
                c.execute("SELECT clan_name FROM clan_history WHERE LOWER(char_name)=LOWER(?) ORDER BY id DESC LIMIT 1", (c_name,))
                last_cln = c.fetchone()
                if not last_cln or last_cln[0].lower() != clan.lower():
                    c.execute("INSERT INTO clan_history (char_name, clan_name, timestamp) VALUES (?, ?, ?)",
                              (c_name, clan, now_str))

            c.execute("""
                UPDATE characters 
                SET current_server=?, current_clan=?, rank=?, power=?, level=?, last_updated=? 
                WHERE LOWER(char_name)=LOWER(?)
            """, (server, clan, rank, power, level, now_str, c_name))

        if should_close:
            conn.commit()
            conn.close()
    except Exception:
        if should_close and conn:
            conn.close()

# ==================== LUỒNG CÀO DỮ LIỆU TOP 1.000 / 12 GIỜ ====================

class Mir4FullCrawlerThread(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.running = True

    def run(self):
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Referer": "https://forum.mir4global.com/rank?ranktype=1"
        }

        time.sleep(10)

        while self.running:
            try:
                prune_old_tracker_records()

                server_list = get_live_mir4_server_list()
                db_path = os.path.join(get_app_dir(), TRACKER_DB)
                conn = sqlite3.connect(db_path, timeout=30.0)

                for srv in server_list:
                    if not self.running:
                        break
                    g_id = srv["group_id"]
                    w_id = srv["world_id"]
                    s_name = srv["name"]

                    for p in range(1, 51):
                        if not self.running:
                            break
                        url = f"https://forum.mir4global.com/rank?ranktype=1000&worldgroupid={g_id}&worldid={w_id}&page={p}"
                        try:
                            req = urllib.request.Request(url, headers=headers)
                            with urllib.request.urlopen(req, timeout=10) as resp:
                                if resp.status == 200:
                                    content = resp.read().decode('utf-8', errors='ignore')
                                    records = parse_mir4_table_rows(content, target_server=s_name)
                                    for r in records:
                                        save_character_record(r, conn)
                                    conn.commit()
                        except Exception:
                            pass
                        time.sleep(0.3)

                conn.close()
            except Exception:
                pass

            for _ in range(432):
                if not self.running:
                    break
                time.sleep(100)

# ==================== LUỒNG TRUY VẤN TÌM KIẾM THEO TÊN (LIVE FALLBACK) ====================

class Mir4QueryWorker(QThread):
    finished = pyqtSignal(str, bool)

    def __init__(self, char_name):
        super().__init__()
        self.char_name = char_name.strip()

    def fetch_url(self, url):
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Referer": "https://forum.mir4global.com/rank?ranktype=1"
        }
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=5) as resp:
                if resp.status == 200:
                    return resp.read().decode('utf-8', errors='ignore')
        except Exception:
            pass
        return ""

    def run(self):
        try:
            found_data = None
            clean_search = self.char_name.lower()
            db_path = os.path.join(get_app_dir(), TRACKER_DB)

            # BƯỚC 1: Tìm kiếm trong SQLite nội bộ
            try:
                conn = sqlite3.connect(db_path, timeout=5.0)
                c = conn.cursor()
                c.execute("""
                    SELECT current_server, current_clan, rank, power, level, last_updated 
                    FROM characters 
                    WHERE LOWER(char_name) = ? AND current_server != 'Chưa xác định'
                """, (clean_search,))
                row = c.fetchone()
                conn.close()

                if row:
                    found_data = {
                        "name": self.char_name,
                        "server": row[0],
                        "clan": row[1],
                        "rank": row[2],
                        "power": row[3],
                        "level": row[4],
                        "last_updated": row[5]
                    }
            except Exception:
                pass

            # BƯỚC 2: Quét trực tiếp trên web nếu SQLite chưa kịp lưu
            if not found_data:
                server_list = get_live_mir4_server_list()
                for srv in server_list:
                    g_id = srv["group_id"]
                    w_id = srv["world_id"]
                    s_name = srv["name"]

                    for p in range(1, 51):
                        url = f"https://forum.mir4global.com/rank?ranktype=1000&worldgroupid={g_id}&worldid={w_id}&page={p}"
                        html_code = self.fetch_url(url)
                        if not html_code:
                            continue

                        if clean_search in html_code.lower():
                            rows = parse_mir4_table_rows(html_code, target_server=s_name)
                            for r in rows:
                                save_character_record(r)
                                if r["name"].strip().lower() == clean_search:
                                    found_data = r
                                    break
                        if found_data:
                            break

                    if found_data:
                        break

            # BƯỚC 3: Quét XDRACO nếu đã seal NFT
            if not found_data:
                nft_url = f"https://webapi.mir4global.com/nft/character/search?characterName={urllib.parse.quote(self.char_name)}"
                headers = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                    "Accept": "application/json, text/plain, */*",
                    "Origin": "https://www.xdraco.com",
                    "Referer": "https://www.xdraco.com/"
                }
                try:
                    req = urllib.request.Request(nft_url, headers=headers)
                    with urllib.request.urlopen(req, timeout=5) as resp:
                        if resp.status == 200:
                            raw_json = json.loads(resp.read().decode('utf-8'))
                            if raw_json and "data" in raw_json and raw_json["data"]:
                                d = raw_json["data"]
                                found_data = {
                                    "rank": "Nhân vật NFT (Đã Seal)",
                                    "name": self.char_name,
                                    "server": d.get("worldName", "ASIA"),
                                    "clan": d.get("clanName", "Không có Clan"),
                                    "power": f"{d.get('powerScore', 0):,}",
                                    "level": f"Lv.{d.get('level', 0)}"
                                }
                                save_character_record(found_data)
                except Exception:
                    pass

            # BƯỚC 4: Lấy danh sách lịch sử THAY ĐỔI
            servers = []
            clans = []
            try:
                conn = sqlite3.connect(db_path, timeout=5.0)
                c = conn.cursor()
                
                c.execute("SELECT server_name, timestamp FROM server_history WHERE LOWER(char_name) = ? ORDER BY id DESC", (clean_search,))
                raw_srv = c.fetchall()
                for s_name, t_stamp in raw_srv:
                    if s_name and s_name != "Chưa xác định":
                        if not servers or servers[-1][0].lower() != s_name.lower():
                            servers.append((s_name, t_stamp))

                c.execute("SELECT clan_name, timestamp FROM clan_history WHERE LOWER(char_name) = ? ORDER BY id DESC", (clean_search,))
                raw_cln = c.fetchall()
                for c_name, t_stamp in raw_cln:
                    if c_name and c_name != "Không có Clan":
                        if not clans or clans[-1][0].lower() != c_name.lower():
                            clans.append((c_name, t_stamp))

                conn.close()
            except Exception:
                pass

            success = False
            if found_data:
                success = True
                level = found_data.get("level", "Theo bảng Lực chiến")
                power = found_data.get("power", "N/A")
                current_server = found_data.get("server", "Chưa xác định")
                current_clan = found_data.get("clan", "Không có Clan")
                ranking_rank = found_data.get("rank", "Chưa xếp hạng")
            else:
                level = "Chưa lọt Top 1000 Máy Chủ"
                power = "Chưa lọt Top 1000 Máy Chủ"
                current_server = "Chưa xác định"
                current_clan = "Không có Clan"
                ranking_rank = "Ngoài phạm vi bảng xếp hạng"

            out = []
            out.append("======================================================================")
            out.append(f" TÊN NHÂN VẬT       : {self.char_name}")
            out.append(f" THỨ HẠNG TRÊN RANK : {ranking_rank}")
            out.append(f" MÁY CHỦ HIỆN TẠI   : {current_server}")
            out.append(f" CLAN HIỆN TẠI      : {current_clan}")
            out.append(f" LỰC CHIẾN (PS)     : {power}")
            out.append(f" CẤP ĐỘ (LEVEL)     : {level}")
            out.append("======================================================================")
            
            out.append("\n[+] LỊCH SỬ MÁY CHỦ (SERVER HISTORY):")
            out.append(f"  • Máy chủ hiện tại   : {current_server}")
            prev_server_line = "Chưa ghi nhận thay đổi máy chủ"
            for s_name, t_stamp in servers:
                if s_name.strip().lower() != current_server.strip().lower() and s_name != "Chưa xác định":
                    prev_server_line = f"{s_name:<18} - {t_stamp}"
                    break
            out.append(f"  • Máy chủ trước đây  : {prev_server_line}")

            out.append("\n[+] LỊCH SỬ CLAN (CLAN HISTORY):")
            out.append(f"  • Clan hiện tại      : {current_clan}")
            prev_clan_line = "Chưa ghi nhận thay đổi Clan"
            for c_name, t_stamp in clans:
                if c_name.strip().lower() != current_clan.strip().lower() and c_name != "Không có Clan":
                    prev_clan_line = f"{c_name:<18} - {t_stamp}"
                    break
            out.append(f"  • Clan trước đây     : {prev_clan_line}")

            out.append("\n======================================================================")
            self.finished.emit("\n".join(out), success)

        except Exception as e:
            err_msg = (
                "======================================================================\n"
                f" LỖI TRUY VẤN: Không thể kết nối hoặc bóc tách dữ liệu ({str(e)})\n"
                " Vui lòng kiểm tra lại đường truyền mạng và thử lại sau.\n"
                "======================================================================"
            )
            self.finished.emit(err_msg, False)

# ==================== GIAO DIỆN CHÍNH (MASTER HUB) ====================

class MasterAppWindow(QMainWindow):
    def __init__(self, username):
        super().__init__()
        self.username = username.strip().lower()
        self.signals = MasterServerSignals()
        self.signals.client_updated.connect(self.update_client_row)
        self.signals.client_disconnected.connect(self.mark_client_offline)

        self.worker = None

        self.server = MasterServer(self.username, self.signals)
        self.server.start()

        self.ts = EmbeddedTailscale()
        self.ts.start_and_connect()
        self.lan_ip, self.vpn_ip, self.wan_ip = get_machine_ips(self.ts)
        self.init_ui()

        self.crawler_thread = Mir4FullCrawlerThread()
        self.crawler_thread.start()

    def init_ui(self):
        self.setWindowTitle(f"Mir4 Master Hub - [Tài khoản: {self.username}]")
        self.resize(1240, 800)
        self.setStyleSheet("background-color: #121214; color: #ffffff;")

        if os.path.exists("app_icon.ico"):
            self.setWindowIcon(QIcon("app_icon.ico"))

        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        layout = QVBoxLayout(main_widget)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        # BANNER QUẢNG CÁO DÙNG CHUNG
        ad_banner = QFrame()
        ad_banner.setStyleSheet("""
            background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #b8860b, stop:1 #d4af37);
            border-radius: 8px;
            padding: 8px;
        """)
        ad_layout = QVBoxLayout(ad_banner)
        ad_title = QLabel("★ HỆ THỐNG TRUY XUẤT THÔNG TIN TÀI KHOẢN MIR4 THEO TÊN (PHAMHUNG) ★")
        ad_title.setFont(QFont("Segoe UI", 12, QFont.Bold))
        ad_title.setStyleSheet("color: #000000;")
        ad_title.setAlignment(Qt.AlignCenter)
        
        ad_sub = QLabel("Hỗ trợ đồng bộ đa máy con qua LAN/Internet | Liên hệ : ZL 0392771558")
        ad_sub.setFont(QFont("Segoe UI", 9, QFont.DemiBold))
        ad_sub.setStyleSheet("color: #111111;")
        ad_sub.setAlignment(Qt.AlignCenter)

        ad_extra = QLabel("Mua QT + TINKER liên hệ : ZL : 0392771558")
        ad_extra.setFont(QFont("Segoe UI", 10, QFont.Bold))
        ad_extra.setStyleSheet("color: #8b0000; margin-top: 2px;")
        ad_extra.setAlignment(Qt.AlignCenter)
        
        ad_layout.addWidget(ad_title)
        ad_layout.addWidget(ad_sub)
        ad_layout.addWidget(ad_extra)
        layout.addWidget(ad_banner)

        self.tabs = QTabWidget()
        self.tabs.setStyleSheet("""
            QTabWidget::pane { border: 1px solid #2e2e38; background-color: #121214; }
            QTabBar::tab { background: #1a1a1e; color: #888; font-weight: bold; font-size: 13px; padding: 10px 25px; border-top-left-radius: 6px; border-top-right-radius: 6px; margin-right: 4px; }
            QTabBar::tab:selected { background: #ff6600; color: white; }
        """)

        # ----- TAB 1: QUẢN LÝ DÀN MÁY CON -----
        tab1_widget = QWidget()
        tab1_layout = QVBoxLayout(tab1_widget)

        top_bar = QHBoxLayout()
        ip_info = f"LAN: <font color='#00e676'><b>{self.lan_ip}</b></font>"
        if self.vpn_ip:
            ip_info += f" | VPN TAILSCALE: <font color='#ffeb3b'><b>{self.vpn_ip}</b></font>"
        if self.wan_ip:
            ip_info += f" | WAN: <font color='#00e5ff'><b>{self.wan_ip}</b></font>"

        user_lbl = QLabel(f"Tài khoản quản trị: <b><font color='#ffd700'>{self.username}</font></b> | IP Máy Chính ({ip_info}) | Port: <b>9999</b>")
        user_lbl.setFont(QFont("Segoe UI", 10))
        top_bar.addWidget(user_lbl)

        top_bar.addStretch()

        self.cb_autostart = QCheckBox("Khởi động cùng Windows (Bypass UAC)")
        self.cb_autostart.setChecked(is_autostart_enabled())
        self.cb_autostart.stateChanged.connect(self.on_toggle_autostart)
        self.cb_autostart.setStyleSheet("color: #00e676; font-weight: bold;")
        top_bar.addWidget(self.cb_autostart)

        tab1_layout.addLayout(top_bar)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels([
            "Tên Máy Con", "Địa Chỉ IP", "Trạng Thái", "QT", "TINKER", "Thời Gian Uptime"
        ])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.show_context_menu)
        self.table.setStyleSheet("""
            QTableWidget {
                background-color: #1c1c24;
                gridline-color: #2e2e38;
                border-radius: 6px;
                font-size: 13px;
                selection-background-color: #ff6600;
                selection-color: #ffffff;
            }
            QHeaderView::section {
                background-color: #16161b;
                padding: 6px;
                font-weight: bold;
                border: 1px solid #292933;
                color: #d4af37;
            }
        """)
        tab1_layout.addWidget(self.table)

        ctrl_bar = QHBoxLayout()
        btn_style_green = "background-color: #2e7d32; color: #fff; font-weight: bold; padding: 8px 12px; border-radius: 4px;"
        btn_style_red = "background-color: #c62828; color: #fff; font-weight: bold; padding: 8px 12px; border-radius: 4px;"
        btn_style_blue = "background-color: #1565c0; color: #fff; font-weight: bold; padding: 8px 12px; border-radius: 4px;"
        btn_style_gray = "background-color: #424242; color: #fff; font-weight: bold; padding: 8px 12px; border-radius: 4px;"
        btn_style_orange = "background-color: #e65100; color: #fff; font-weight: bold; padding: 8px 14px; border-radius: 4px;"

        btn_select_all = QPushButton("Chọn Tất Cả")
        btn_select_all.setStyleSheet(btn_style_gray)
        btn_select_all.clicked.connect(self.table.selectAll)
        ctrl_bar.addWidget(btn_select_all)

        btn_qt_on = QPushButton("▶ Bật QT")
        btn_qt_on.setStyleSheet(btn_style_green)
        btn_qt_on.clicked.connect(lambda: self.execute_on_selected("START_QT"))
        ctrl_bar.addWidget(btn_qt_on)

        btn_qt_off = QPushButton("■ Tắt QT")
        btn_qt_off.setStyleSheet(btn_style_red)
        btn_qt_off.clicked.connect(lambda: self.execute_on_selected("STOP_QT"))
        ctrl_bar.addWidget(btn_qt_off)

        btn_tk_on = QPushButton("▶ Bật TINKER")
        btn_tk_on.setStyleSheet(btn_style_green)
        btn_tk_on.clicked.connect(lambda: self.execute_on_selected("START_TINKER"))
        ctrl_bar.addWidget(btn_tk_on)

        btn_tk_off = QPushButton("■ Tắt TINKER")
        btn_tk_off.setStyleSheet(btn_style_red)
        btn_tk_off.clicked.connect(lambda: self.execute_on_selected("STOP_TINKER"))
        ctrl_bar.addWidget(btn_tk_off)

        btn_update = QPushButton("🚀 Update MIR4")
        btn_update.setStyleSheet(btn_style_orange)
        btn_update.clicked.connect(lambda: self.execute_on_selected("UPDATE_MIR4"))
        ctrl_bar.addWidget(btn_update)

        btn_reboot = QPushButton("⚡ Reboot Máy Con")
        btn_reboot.setStyleSheet(btn_style_blue)
        btn_reboot.clicked.connect(lambda: self.execute_on_selected("REBOOT"))
        ctrl_bar.addWidget(btn_reboot)

        tab1_layout.addLayout(ctrl_bar)
        self.tabs.addTab(tab1_widget, "🖥️ QUẢN LÝ DÀN MÁY CON")

        # ----- TAB 2: TRUY XUẤT THÔNG TIN ACCOUNT MIR4 -----
        tab2_widget = QWidget()
        tab2_layout = QVBoxLayout(tab2_widget)
        tab2_layout.setContentsMargins(15, 15, 15, 15)
        tab2_layout.setSpacing(12)

        search_box = QHBoxLayout()
        lbl_search = QLabel("Tên nhân vật MIR4:")
        lbl_search.setFont(QFont("Segoe UI", 11, QFont.Bold))
        lbl_search.setStyleSheet("color: #00e5ff;")
        search_box.addWidget(lbl_search)

        self.txt_char_search = QLineEdit()
        self.txt_char_search.setFixedHeight(38)
        self.txt_char_search.setPlaceholderText("Nhập tên nhân vật (ví dụ: MOEMATSUKA1)...")
        self.txt_char_search.setStyleSheet("""
            QLineEdit {
                padding: 6px 12px; 
                border-radius: 4px; 
                background: #24242c; 
                color: #ffffff; 
                font-size: 13px; 
                border: 1px solid #444;
            }
        """)
        self.txt_char_search.returnPressed.connect(self.on_search_char_clicked)

        self.history_names = get_searched_char_names()
        self.completer_model = QStringListModel(self.history_names)
        self.completer = QCompleter(self.completer_model, self)
        self.completer.setCaseSensitivity(Qt.CaseInsensitive)
        self.completer.setFilterMode(Qt.MatchContains)
        self.txt_char_search.setCompleter(self.completer)
        search_box.addWidget(self.txt_char_search)

        self.btn_search_char = QPushButton("🔍 TÌM KIẾM")
        self.btn_search_char.setFixedHeight(38)
        self.btn_search_char.setStyleSheet("background-color: #ff6600; color: #fff; font-weight: bold; padding: 0 25px; border-radius: 4px;")
        self.btn_search_char.clicked.connect(self.on_search_char_clicked)
        search_box.addWidget(self.btn_search_char)

        tab2_layout.addLayout(search_box)

        lbl_res_header = QLabel("THÔNG TIN CHI TIẾT TỪ TRANG CHỦ & RANKING MIR4:")
        lbl_res_header.setFont(QFont("Segoe UI", 10, QFont.Bold))
        lbl_res_header.setStyleSheet("color: #d4af37;")
        tab2_layout.addWidget(lbl_res_header)

        self.txt_char_details = QTextEdit()
        self.txt_char_details.setReadOnly(True)
        self.txt_char_details.setFont(QFont("Consolas", 10))
        self.txt_char_details.setStyleSheet("background-color: #1a1a22; color: #00e676; border: 1px solid #333; border-radius: 6px; padding: 10px;")
        tab2_layout.addWidget(self.txt_char_details)

        self.tabs.addTab(tab2_widget, "🔍 TRUY XUẤT THÔNG TIN ACCOUNT MIR4")
        layout.addWidget(self.tabs)

    def on_search_char_clicked(self):
        name = self.txt_char_search.text().strip()
        if not name:
            QMessageBox.warning(self, "Chưa nhập tên", "Vui lòng nhập tên nhân vật cần tra cứu!")
            return

        self.btn_search_char.setEnabled(False)
        self.btn_search_char.setText("Đang tìm...")
        self.txt_char_details.setText(f"[*] Đang truy xuất thông tin nhân vật [{name}]...")

        if self.worker is not None and self.worker.isRunning():
            self.worker.terminate()
            self.worker.wait()

        self.worker = Mir4QueryWorker(name)
        self.worker.finished.connect(self.on_search_finished)
        self.worker.start()

    def on_search_finished(self, result_text, success_found):
        self.txt_char_details.setText(result_text)
        self.btn_search_char.setEnabled(True)
        self.btn_search_char.setText("🔍 TÌM KIẾM")

        name = self.txt_char_search.text().strip()
        if name and name not in self.history_names:
            self.history_names.insert(0, name)
            self.completer_model.setStringList(self.history_names)

    def show_context_menu(self, pos):
        selected_rows = self.table.selectionModel().selectedRows()
        if not selected_rows:
            return

        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu { background-color: #24242c; color: #ffffff; border: 1px solid #444; font-weight: bold; }
            QMenu::item:selected { background-color: #c62828; color: #ffffff; }
        """)

        action_update = QAction(f"🚀 UPDATE MIR4 (15 PHÚT) CHO {len(selected_rows)} MÁY", self)
        action_update.triggered.connect(lambda: self.execute_on_selected("UPDATE_MIR4"))
        menu.addAction(action_update)

        action_delete = QAction(f"🗑️ XÓA {len(selected_rows)} MÁY ĐÃ CHỌN", self)
        action_delete.triggered.connect(self.delete_selected_rows)
        menu.addAction(action_delete)

        menu.exec_(self.table.viewport().mapToGlobal(pos))

    def delete_selected_rows(self):
        selected_rows = self.table.selectionModel().selectedRows()
        if not selected_rows:
            return
        reply = QMessageBox.question(
            self, 'Xác nhận xóa',
            f"Bạn có chắc muốn xóa {len(selected_rows)} máy con khỏi bảng hiển thị?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            rows = sorted([index.row() for index in selected_rows], reverse=True)
            for r in rows:
                self.table.removeRow(r)

    def find_row_by_node_name(self, node_name):
        for r in range(self.table.rowCount()):
            item = self.table.item(r, 0)
            if item and item.text().strip().lower() == node_name.strip().lower():
                return r
        return -1

    def update_client_row(self, data):
        node_name = data["node_name"]
        row = self.find_row_by_node_name(node_name)
        if row == -1:
            row = self.table.rowCount()
            self.table.insertRow(row)

        self.table.setItem(row, 0, QTableWidgetItem(node_name))
        self.table.setItem(row, 1, QTableWidgetItem(data["ip"]))
        
        status_item = QTableWidgetItem(data["status"])
        status_item.setForeground(QColor("#00e676"))
        status_item.setFont(QFont("Segoe UI", 9, QFont.Bold))
        self.table.setItem(row, 2, status_item)

        qt_status = data["qt_status"]
        qt_item = QTableWidgetItem(qt_status)
        if qt_status == "ON":
            qt_item.setForeground(QColor("#00e676"))
        elif "CHỜ ĐĂNG NHẬP" in qt_status:
            qt_item.setForeground(QColor("#ffb300"))
        elif "UPDATING" in qt_status:
            qt_item.setForeground(QColor("#00e5ff"))
        else:
            qt_item.setForeground(QColor("#ff5252"))
        qt_item.setFont(QFont("Segoe UI", 9, QFont.Bold))
        self.table.setItem(row, 3, qt_item)

        tinker_status = data["tinker_status"]
        tinker_item = QTableWidgetItem(tinker_status)
        if tinker_status == "ON":
            tinker_item.setForeground(QColor("#00e676"))
        elif "CHỜ ĐĂNG NHẬP" in tinker_status:
            tinker_item.setForeground(QColor("#ffb300"))
        elif "UPDATING" in tinker_status:
            tinker_item.setForeground(QColor("#00e5ff"))
        else:
            tinker_item.setForeground(QColor("#ff5252"))
        tinker_item.setFont(QFont("Segoe UI", 9, QFont.Bold))
        self.table.setItem(row, 4, tinker_item)

        self.table.setItem(row, 5, QTableWidgetItem(data["uptime"]))

    def mark_client_offline(self, node_name):
        row = self.find_row_by_node_name(node_name)
        if row != -1:
            status_item = QTableWidgetItem("OFFLINE")
            status_item.setForeground(QColor("#ff5252"))
            status_item.setFont(QFont("Segoe UI", 9, QFont.Bold))
            self.table.setItem(row, 2, status_item)

    def get_selected_node_names(self):
        selected_indexes = self.table.selectionModel().selectedRows()
        node_names = []
        for index in selected_indexes:
            item = self.table.item(index.row(), 0)
            if item:
                node_names.append(item.text().strip())
        return node_names

    def execute_on_selected(self, command_str):
        selected_nodes = self.get_selected_node_names()
        if not selected_nodes:
            QMessageBox.warning(self, "Chưa chọn máy", "Vui lòng chọn ít nhất 1 máy con!")
            return

        if command_str == "REBOOT":
            reply = QMessageBox.question(
                self, 'Xác nhận Reboot',
                f"Bạn có chắc muốn Reboot {len(selected_nodes)} máy đã chọn?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No
            )
            if reply != QMessageBox.Yes:
                return

        if command_str == "UPDATE_MIR4":
            reply = QMessageBox.question(
                self, 'Xác nhận Cập nhật MIR4',
                f"Kích hoạt tự động Cập nhật MIR4 cho {len(selected_nodes)} máy đã chọn?\n\n"
                f"- Hệ thống máy con sẽ lập tức tắt QT, TINKER và ngừng tự bật lại.\n"
                f"- Sau 15 phút cập nhật game xong, máy con sẽ tự khôi phục auto QT & TINKER.",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No
            )
            if reply != QMessageBox.Yes:
                return

        success_count = 0
        for name in selected_nodes:
            if self.server.send_command(name, command_str):
                success_count += 1

        QMessageBox.information(self, "Thông báo", f"Đã gửi lệnh [{command_str}] tới {success_count}/{len(selected_nodes)} máy đang Online.")

    def on_toggle_autostart(self, state):
        enable = state == Qt.Checked
        if set_autostart(enable):
            status = "đã bật" if enable else "đã tắt"
            QMessageBox.information(self, "Thông báo", f"Tự khởi động cùng Windows (Bypass UAC) {status} thành công!")
        else:
            QMessageBox.warning(self, "Lỗi", "Không thể cập nhật cấu hình khởi động!")

class LoginWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.pref_path = os.path.join(get_app_dir(), PREF_FILE)
        self.init_ui()
        self.load_remembered_credentials()

    def init_ui(self):
        self.setWindowTitle("Đăng Nhập Quản Trị Mir4")
        self.resize(360, 360)
        self.setStyleSheet("background-color: #121214; color: #ffffff; font-family: 'Segoe UI';")

        if os.path.exists("app_icon.ico"):
            self.setWindowIcon(QIcon("app_icon.ico"))

        layout = QVBoxLayout(self)

        title = QLabel("HỆ THỐNG QUẢN TRỊ MIR4")
        title.setFont(QFont("Segoe UI", 12, QFont.Bold))
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("color: #d4af37; margin-bottom: 10px;")
        layout.addWidget(title)

        self.txt_user = QLineEdit()
        self.txt_user.setPlaceholderText("Tài khoản máy chính")
        self.txt_user.setStyleSheet("padding: 8px; border-radius: 4px; background: #24242c; color: #fff;")
        layout.addWidget(self.txt_user)

        self.txt_pass = QLineEdit()
        self.txt_pass.setPlaceholderText("Mật khẩu")
        self.txt_pass.setEchoMode(QLineEdit.Password)
        self.txt_pass.setStyleSheet("padding: 8px; border-radius: 4px; background: #24242c; color: #fff;")
        layout.addWidget(self.txt_pass)

        self.chk_remember = QCheckBox("Ghi nhớ tài khoản cho lần sau")
        self.chk_remember.setStyleSheet("color: #00e5ff; font-weight: bold; margin-top: 4px;")
        layout.addWidget(self.chk_remember)

        self.btn_login = QPushButton("Đăng Nhập")
        self.btn_login.setStyleSheet("background-color: #ff6600; color: #fff; font-weight: bold; padding: 10px; border-radius: 4px; margin-top: 6px;")
        self.btn_login.clicked.connect(self.handle_login)
        layout.addWidget(self.btn_login)

        self.btn_register = QPushButton("Tạo Tài Khoản Mới")
        self.btn_register.setStyleSheet("background-color: #2b2b36; color: #fff; padding: 8px; border-radius: 4px;")
        self.btn_register.clicked.connect(self.handle_register)
        layout.addWidget(self.btn_register)

    def load_remembered_credentials(self):
        if os.path.exists(self.pref_path):
            try:
                with open(self.pref_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if data.get("remember", False):
                        self.txt_user.setText(data.get("username", ""))
                        self.txt_pass.setText(data.get("password", ""))
                        self.chk_remember.setChecked(True)
            except Exception:
                pass

    def save_credentials(self, username, password, remember):
        try:
            payload = {
                "remember": bool(remember),
                "username": username if remember else "",
                "password": password if remember else ""
            }
            with open(self.pref_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=4)
        except Exception:
            pass

    def handle_login(self):
        u = self.txt_user.text().strip().lower()
        p = self.txt_pass.text().strip()
        if authenticate_user(u, p):
            self.save_credentials(u, p, self.chk_remember.isChecked())
            self.master_win = MasterAppWindow(u)
            self.master_win.show()
            self.close()
        else:
            QMessageBox.critical(self, "Thất bại", "Sai tài khoản hoặc mật khẩu!")

    def handle_register(self):
        u = self.txt_user.text().strip().lower()
        p = self.txt_pass.text().strip()
        ok, msg = register_user(u, p)
        if ok:
            QMessageBox.information(self, "Thành công", msg)
        else:
            QMessageBox.warning(self, "Lỗi", msg)

if __name__ == "__main__":
    setup_windows_firewall(SERVER_PORT)
    init_db()
    init_tracker_db()
    app = QApplication(sys.argv)
    login = LoginWindow()
    login.show()
    sys.exit(app.exec_())
