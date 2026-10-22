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
from concurrent.futures import ThreadPoolExecutor, as_completed

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

def get_app_dir():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

def kill_tailscale_processes():
    try:
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE
        subprocess.run(
            "taskkill /F /IM tailscaled.exe /IM tailscale.exe",
            shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, startupinfo=startupinfo
        )
    except Exception:
        pass

def setup_windows_firewall(port=9999):
    """
    Chạy ngầm mở toàn bộ cổng TCP/UDP 9999, cổng Tailscale VPN (41641, 3478),
    ICMP Ping và cho phép tiến trình tailscaled.exe trên mọi loại mạng (Public, Private, Domain).
    """
    try:
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE

        rules = [
            f'netsh advfirewall firewall add rule name="AUTOGAME_Master_Socket_Server" dir=in action=allow protocol=TCP localport={port} profile=any',
            f'netsh advfirewall firewall add rule name="AUTOGAME_Master_Socket_Server_OUT" dir=out action=allow protocol=TCP localport={port} profile=any',
            f'netsh advfirewall firewall add rule name="Mir4_Master_Socket_Server" dir=in action=allow protocol=TCP localport={port} profile=any',
            f'netsh advfirewall firewall add rule name="AUTOGAME_Tailscale_UDP" dir=in action=allow protocol=UDP localport=41641 profile=any',
            f'netsh advfirewall firewall add rule name="AUTOGAME_Tailscale_Subnet" dir=in action=allow remoteip=100.64.0.0/10 profile=any',
            f'netsh advfirewall firewall add rule name="AUTOGAME_ICMP_Ping" dir=in action=allow protocol=icmpv4:8,any profile=any'
        ]

        ts_prog_dir = os.path.join(os.environ.get("PROGRAMDATA", "C:\\ProgramData"), "Mir4Tailscale")
        for bin_name in ("tailscaled.exe", "tailscale.exe"):
            bin_path = os.path.join(ts_prog_dir, bin_name)
            if os.path.exists(bin_path):
                rules.append(f'netsh advfirewall firewall add rule name="AUTOGAME_VPN_{bin_name}" dir=in action=allow program="{bin_path}" profile=any')

        chk_cmd = 'netsh advfirewall firewall show rule name="AUTOGAME_Tailscale_Subnet"'
        res = subprocess.run(chk_cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, startupinfo=startupinfo)
        if res.returncode != 0:
            for cmd in rules:
                subprocess.run(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, startupinfo=startupinfo)
        else:
            subprocess.run(rules[0], shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, startupinfo=startupinfo)
    except Exception:
        pass

def enable_socket_keepalive(sock):
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        if hasattr(socket, "SIO_KEEPALIVE_VALS"):
            sock.ioctl(socket.SIO_KEEPALIVE_VALS, (1, 15000, 5000))
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
        try:
            vpn_ip = ts_client.get_my_ip()
        except Exception:
            pass

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
APP_NAME = "AUTOGAME_MasterManager"
TASK_NAME = "AUTOGAME_MasterManager_AutoStart"
PREF_FILE = "login_pref.json"
TRACKER_DB = "mir4_tracker_history.db"

# Bộ nhớ đệm (RAM Cache) chứa danh sách tên gợi ý để không bao giờ gây đơ UI
CACHED_CHAR_NAMES = []
CACHE_LOCK = threading.Lock()

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
    c.execute("PRAGMA synchronous=NORMAL;")
    c.execute("PRAGMA busy_timeout=30000;")
    c.execute('''CREATE TABLE IF NOT EXISTS characters (
                    char_name TEXT PRIMARY KEY,
                    current_server TEXT,
                    current_clan TEXT,
                    rank TEXT,
                    power TEXT,
                    level TEXT,
                    last_updated TEXT,
                    char_class TEXT DEFAULT 'Theo bảng xếp hạng'
                )''')
    try:
        c.execute("ALTER TABLE characters ADD COLUMN char_class TEXT DEFAULT 'Theo bảng xếp hạng'")
    except Exception:
        pass

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
    c.execute('''CREATE TABLE IF NOT EXISTS name_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    char_name TEXT,
                    old_name TEXT,
                    new_name TEXT,
                    timestamp TEXT
                )''')
    c.execute("CREATE INDEX IF NOT EXISTS idx_char_name ON characters(char_name);")
    c.execute("CREATE INDEX IF NOT EXISTS idx_char_clan_hist ON clan_history(char_name);")
    c.execute("CREATE INDEX IF NOT EXISTS idx_char_srv_hist ON server_history(char_name);")
    c.execute("CREATE INDEX IF NOT EXISTS idx_char_name_hist ON name_history(char_name);")
    conn.commit()
    conn.close()

def prune_old_tracker_records(conn=None):
    """Lưu trữ dữ liệu 180 ngày."""
    should_close = False
    try:
        if conn is None:
            db_path = os.path.join(get_app_dir(), TRACKER_DB)
            conn = sqlite3.connect(db_path, timeout=20.0)
            should_close = True

        c = conn.cursor()
        c.execute("DELETE FROM clan_history WHERE timestamp < datetime('now', '-180 days')")
        c.execute("DELETE FROM server_history WHERE timestamp < datetime('now', '-180 days')")
        c.execute("DELETE FROM name_history WHERE timestamp < datetime('now', '-180 days')")
        c.execute("DELETE FROM characters WHERE last_updated < datetime('now', '-180 days')")
        conn.commit()
    except Exception:
        pass
    finally:
        if should_close and conn:
            conn.close()

def load_char_names_from_db():
    """Đọc danh sách tên nhân vật từ DB (chỉ gọi ở luồng ngầm hoặc khi khởi tạo)."""
    global CACHED_CHAR_NAMES
    try:
        db_path = os.path.join(get_app_dir(), TRACKER_DB)
        conn = sqlite3.connect(db_path, timeout=10.0)
        c = conn.cursor()
        c.execute("SELECT char_name FROM characters ORDER BY last_updated DESC LIMIT 15000")
        rows = c.fetchall()
        conn.close()
        names = [r[0] for r in rows if r[0]]
        with CACHE_LOCK:
            CACHED_CHAR_NAMES = names
        return names
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

# ==================== TÍN HIỆU ĐỒNG BỘ LUỒNG NGẦM & UI ====================

class MasterServerSignals(QObject):
    client_updated = pyqtSignal(dict)
    client_disconnected = pyqtSignal(str)
    ip_info_updated = pyqtSignal(str, str, str)
    completer_updated = pyqtSignal(list)

# ==================== SOCKET SERVER QUẢN LÝ MÁY CON ====================

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
        enable_socket_keepalive(server)
        server.bind(('0.0.0.0', SERVER_PORT))
        server.listen(200)

        while self.running:
            try:
                conn, addr = server.accept()
                enable_socket_keepalive(conn)
                threading.Thread(target=self.handle_client, args=(conn, addr), daemon=True).start()
            except Exception:
                break

    def handle_client(self, conn, addr):
        client_id = None
        buffer = ""
        try:
            while self.running:
                data = conn.recv(4096).decode('utf-8', errors='ignore')
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

                    qt_val = payload.get("qt", payload.get("qt_status", False))
                    qt_status = qt_val if isinstance(qt_val, str) else ("ON" if qt_val else "OFF")

                    tinker_val = payload.get("tinker", payload.get("tinker_status", False))
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
                if client_id in self.active_sockets and self.active_sockets[client_id] is conn:
                    del self.active_sockets[client_id]
            try:
                conn.close()
            except Exception:
                pass
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

# ==================== LUỒNG NGẦM DUY TRÌ VPN TAILSCALE & TƯỜNG LỬA ====================

class NetworkWatchdogThread(threading.Thread):
    """
    Chạy ngầm 100% ở Background Thread để khởi tạo VPN, mở Firewall và
    tự động bật lại tailscaled.exe nếu bị tắt ngầm mà KHÔNG làm đơ giao diện.
    """
    def __init__(self, signals):
        super().__init__(daemon=True)
        self.signals = signals
        self.running = True
        self.ts = EmbeddedTailscale()

    def is_tailscaled_running(self):
        try:
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = subprocess.SW_HIDE
            chk = subprocess.run(
                'tasklist /FI "IMAGENAME eq tailscaled.exe"',
                shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, startupinfo=startupinfo
            )
            return "tailscaled.exe" in chk.stdout.lower()
        except Exception:
            return False

    def run(self):
        # 1. Mở cổng Firewall và kết nối VPN ngay khi vào màn hình chính
        setup_windows_firewall(SERVER_PORT)
        self.ts.start_and_connect()
        setup_windows_firewall(SERVER_PORT)

        lan_ip, vpn_ip, wan_ip = get_machine_ips(self.ts)
        self.signals.ip_info_updated.emit(lan_ip, vpn_ip, wan_ip)

        # 2. Vòng lặp kiểm tra mỗi 20 giây ở luồng ngầm: nếu tailscaled bị tắt thì tự bật lại ngay
        while self.running:
            for _ in range(20):
                if not self.running:
                    return
                time.sleep(1)

            try:
                if not self.is_tailscaled_running():
                    self.ts.start_and_connect()
                    setup_windows_firewall(SERVER_PORT)

                curr_vpn = self.ts.get_my_ip()
                if not curr_vpn:
                    self.ts.start_and_connect()
                    curr_vpn = self.ts.get_my_ip()

                if curr_vpn and curr_vpn != vpn_ip:
                    vpn_ip = curr_vpn
                    self.signals.ip_info_updated.emit(lan_ip, vpn_ip, wan_ip)
            except Exception:
                pass

# ==================== MODULE BÓC TÁCH MÁY CHỦ & DỮ LIỆU MIR4 ====================

MIR4_CLASS_MAP = {
    "1": "Chiến Binh (Warrior)",
    "2": "Pháp Sư (Sorcerer)",
    "3": "Đạo Sĩ (Taoist)",
    "4": "Cung Thủ (Arbalist)",
    "5": "Võ Sĩ (Lancer)",
    "6": "Hắc Đạo (Darkist)",
    "warrior": "Chiến Binh (Warrior)",
    "sorcerer": "Pháp Sư (Sorcerer)",
    "taoist": "Đạo Sĩ (Taoist)",
    "arbalist": "Cung Thủ (Arbalist)",
    "lancer": "Võ Sĩ (Lancer)",
    "darkist": "Hắc Đạo (Darkist)"
}

def extract_class_from_tr(tr_html):
    try:
        m = re.search(r'icon_class_([a-zA-Z0-9_]+)', tr_html, re.IGNORECASE)
        if m:
            key = m.group(1).lower()
            for k, v in MIR4_CLASS_MAP.items():
                if k in key:
                    return v
        m_alt = re.search(r'<img[^>]+alt=["\']([^"\']+)["\']', tr_html, re.IGNORECASE)
        if m_alt:
            alt_txt = m_alt.group(1).strip().lower()
            if alt_txt in MIR4_CLASS_MAP:
                return MIR4_CLASS_MAP[alt_txt]
            elif alt_txt:
                return m_alt.group(1).strip()
    except Exception:
        pass
    return "Theo bảng xếp hạng"

def get_live_mir4_server_list():
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8"
    }
    extracted_servers = []
    seen_worlds = set()
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
                    if clean_name and clean_name.lower() != "all" and int(w_id) not in seen_worlds:
                        seen_worlds.add(int(w_id))
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
            if not td_texts:
                continue

            char_class = extract_class_from_tr(tr)
            td_cleaned = []
            for td in td_texts:
                clean_td = re.sub(r'<[^>]+>', ' ', td)
                clean_td = html.unescape(clean_td).strip()
                clean_td = ' '.join(clean_td.split())
                td_cleaned.append(clean_td)

            if len(td_cleaned) >= 5:
                rank_raw = td_cleaned[0].replace("NEW", "").strip()
                rank_num_match = re.match(r'^([0-9]+)', rank_raw)
                if rank_num_match:
                    rank_raw = rank_num_match.group(1)
                name = td_cleaned[1]
                server = td_cleaned[2] if td_cleaned[2] else target_server
                clan = td_cleaned[3] if td_cleaned[3] and td_cleaned[3] != "-" else "Không có Clan"
                power = td_cleaned[4]
                results.append({
                    "rank": rank_raw,
                    "name": name,
                    "server": server,
                    "clan": clan,
                    "power": power,
                    "level": "Theo bảng Lực chiến",
                    "char_class": char_class
                })
            elif len(td_cleaned) == 4:
                rank_raw = td_cleaned[0].replace("NEW", "").strip()
                rank_num_match = re.match(r'^([0-9]+)', rank_raw)
                if rank_num_match:
                    rank_raw = rank_num_match.group(1)
                name = td_cleaned[1]
                clan = td_cleaned[2] if td_cleaned[2] and td_cleaned[2] != "-" else "Không có Clan"
                power = td_cleaned[3]
                results.append({
                    "rank": rank_raw,
                    "name": name,
                    "server": target_server,
                    "clan": clan,
                    "power": power,
                    "level": "Theo bảng Lực chiến",
                    "char_class": char_class
                })
    except Exception:
        pass
    return results

def save_character_record(data_obj, conn=None):
    should_close = False
    try:
        if conn is None:
            db_path = os.path.join(get_app_dir(), TRACKER_DB)
            conn = sqlite3.connect(db_path, timeout=20.0)
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
        char_class = str(data_obj.get("char_class", "Theo bảng xếp hạng")).strip()

        c.execute("SELECT char_name, current_server, current_clan FROM characters WHERE LOWER(char_name) = LOWER(?)", (c_name,))
        row = c.fetchone()

        if not row:
            # Đối chiếu phát hiện đổi tên nhân vật
            if server != "Chưa xác định" and power != "N/A":
                c.execute("""
                    SELECT char_name FROM characters
                    WHERE LOWER(current_server) = LOWER(?)
                      AND LOWER(current_clan) = LOWER(?)
                      AND power = ?
                      AND rank = ?
                      AND LOWER(char_name) != LOWER(?)
                    ORDER BY last_updated DESC LIMIT 1
                """, (server, clan, power, rank, c_name))
                rename_candidate = c.fetchone()
                if rename_candidate:
                    old_char_name = rename_candidate[0]
                    c.execute("SELECT old_name, new_name FROM name_history WHERE LOWER(char_name)=LOWER(?) ORDER BY id DESC LIMIT 1", (c_name,))
                    last_rn = c.fetchone()
                    if not last_rn or last_rn[1].lower() != c_name.lower():
                        c.execute("INSERT INTO name_history (char_name, old_name, new_name, timestamp) VALUES (?, ?, ?, ?)",
                                  (c_name, old_char_name, c_name, now_str))
                        c.execute("INSERT INTO name_history (char_name, old_name, new_name, timestamp) VALUES (?, ?, ?, ?)",
                                  (old_char_name, old_char_name, c_name, now_str))

            c.execute("""
                INSERT INTO characters (char_name, current_server, current_clan, rank, power, level, last_updated, char_class)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (c_name, server, clan, rank, power, level, now_str, char_class))

            if server and server != "Chưa xác định":
                c.execute("INSERT INTO server_history (char_name, server_name, timestamp) VALUES (?, ?, ?)",
                          (c_name, server, now_str))
            if clan:
                c.execute("INSERT INTO clan_history (char_name, clan_name, timestamp) VALUES (?, ?, ?)",
                          (c_name, clan, now_str))
        else:
            stored_name, old_server, old_clan = row
            if stored_name != c_name:
                c.execute("SELECT old_name, new_name FROM name_history WHERE LOWER(char_name)=LOWER(?) ORDER BY id DESC LIMIT 1", (c_name,))
                last_rn = c.fetchone()
                if not last_rn or last_rn[1] != c_name:
                    c.execute("INSERT INTO name_history (char_name, old_name, new_name, timestamp) VALUES (?, ?, ?, ?)",
                              (c_name, stored_name, c_name, now_str))

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
                SET char_name=?, current_server=?, current_clan=?, rank=?, power=?, level=?, last_updated=?, char_class=?
                WHERE LOWER(char_name)=LOWER(?)
            """, (c_name, server, clan, rank, power, level, now_str, char_class, c_name))

        if should_close:
            conn.commit()
            conn.close()
    except Exception:
        if should_close and conn:
            conn.close()

# ==================== LUỒNG CÀO DỮ LIỆU MỖI 3 GIỜ (ÊM, KHÔNG ĐƠ MÁY) ====================

class Mir4FullCrawlerThread(threading.Thread):
    def __init__(self, signals=None):
        super().__init__(daemon=True)
        self.running = True
        self.signals = signals

    def run(self):
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Referer": "https://forum.mir4global.com/rank?ranktype=1"
        }

        # Nạp danh sách tên ban đầu ở luồng ngầm để UI không phải đọc DB
        initial_names = load_char_names_from_db()
        if self.signals and initial_names:
            self.signals.completer_updated.emit(initial_names)

        time.sleep(8)

        while self.running:
            try:
                prune_old_tracker_records()
                server_list = get_live_mir4_server_list()
                db_path = os.path.join(get_app_dir(), TRACKER_DB)

                for srv_idx, srv in enumerate(server_list):
                    if not self.running:
                        break
                    g_id = srv["group_id"]
                    w_id = srv["world_id"]
                    s_name = srv["name"]

                    server_records = []
                    for p in range(1, 51):
                        if not self.running:
                            break
                        url = f"https://forum.mir4global.com/rank?ranktype=1000&worldgroupid={g_id}&worldid={w_id}&page={p}"
                        try:
                            req = urllib.request.Request(url, headers=headers)
                            with urllib.request.urlopen(req, timeout=8) as resp:
                                if resp.status == 200:
                                    content = resp.read().decode('utf-8', errors='ignore')
                                    rows = parse_mir4_table_rows(content, target_server=s_name)
                                    if rows:
                                        server_records.extend(rows)
                        except Exception:
                            pass
                        # Nhường CPU cho giao diện chính luôn mượt 60 FPS
                        time.sleep(0.25)

                    # Ghi gọn 1 lần (Batch Transaction) sau khi quét xong 1 máy chủ
                    if server_records:
                        try:
                            conn = sqlite3.connect(db_path, timeout=30.0)
                            for r in server_records:
                                save_character_record(r, conn)
                            conn.commit()
                            conn.close()
                        except Exception:
                            pass

                    # Cập nhật danh sách gợi ý sau mỗi 5 máy chủ
                    if (srv_idx + 1) % 5 == 0 and self.signals:
                        updated_names = load_char_names_from_db()
                        self.signals.completer_updated.emit(updated_names)

                if self.signals:
                    self.signals.completer_updated.emit(load_char_names_from_db())

            except Exception:
                pass

            # Chu kỳ lặp lại sau mỗi 3 giờ (108 * 100 giây = 10.800 giây = 3 giờ)
            for _ in range(108):
                if not self.running:
                    break
                time.sleep(100)

# ==================== LUỒNG TRUY VẤN TÌM KIẾM THEO TÊN (LIVE FALLBACK TỪ TRANG CHỦ) ====================

class Mir4QueryWorker(QThread):
    finished = pyqtSignal(str, bool)

    def __init__(self, char_name):
        super().__init__()
        self.char_name = char_name.strip()
        self.search_timeout = 60

    def fetch_url(self, url, timeout=5):
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Referer": "https://forum.mir4global.com/rank?ranktype=1"
        }
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if resp.status == 200:
                    return resp.read().decode('utf-8', errors='ignore')
        except Exception:
            pass
        return ""

    def search_server_direct(self, srv, encoded_name, clean_search):
        g_id = srv["group_id"]
        w_id = srv["world_id"]
        s_name = srv["name"]
        for r_type in (1, 1000):
            url = f"https://forum.mir4global.com/rank?ranktype={r_type}&worldgroupid={g_id}&worldid={w_id}&search={encoded_name}"
            html_code = self.fetch_url(url, timeout=5)
            if html_code and clean_search in html_code.lower():
                rows = parse_mir4_table_rows(html_code, target_server=s_name)
                for r in rows:
                    save_character_record(r)
                    if r["name"].strip().lower() == clean_search:
                        return r
        return None

    def query_xdraco_nft(self):
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
                        if isinstance(d, list) and len(d) > 0:
                            d = d[0]
                        if isinstance(d, dict):
                            cls_code = str(d.get("class", ""))
                            char_cls = MIR4_CLASS_MAP.get(cls_code, d.get("className", "Nhân vật NFT"))
                            return {
                                "rank": "Nhân vật NFT (XDRACO)",
                                "name": d.get("characterName", self.char_name),
                                "server": d.get("worldName", "ASIA"),
                                "clan": d.get("clanName") or "Không có Clan",
                                "power": f"{int(d.get('powerScore', 0)):,}" if str(d.get('powerScore', '')).isdigit() else str(d.get('powerScore', 'N/A')),
                                "level": f"Lv.{d.get('level', 'N/A')}",
                                "char_class": char_cls
                            }
        except Exception:
            pass
        return None

    def run(self):
        start_time = time.time()
        try:
            found_data = None
            clean_search = self.char_name.lower()
            encoded_name = urllib.parse.quote(self.char_name)
            db_path = os.path.join(get_app_dir(), TRACKER_DB)
            data_source_label = "Dữ liệu đã lưu trên App (Cập nhật định kỳ 3h)"

            # BƯỚC 1: Tìm trong SQLite nội bộ
            try:
                conn = sqlite3.connect(db_path, timeout=5.0)
                c = conn.cursor()
                c.execute("""
                    SELECT char_name, current_server, current_clan, rank, power, level, last_updated, char_class
                    FROM characters 
                    WHERE LOWER(char_name) = ? AND current_server != 'Chưa xác định'
                """, (clean_search,))
                row = c.fetchone()

                if not row:
                    c.execute("""
                        SELECT new_name FROM name_history
                        WHERE LOWER(old_name) = ?
                        ORDER BY id DESC LIMIT 1
                    """, (clean_search,))
                    rn_row = c.fetchone()
                    if rn_row and rn_row[0]:
                        c.execute("""
                            SELECT char_name, current_server, current_clan, rank, power, level, last_updated, char_class
                            FROM characters 
                            WHERE LOWER(char_name) = LOWER(?) AND current_server != 'Chưa xác định'
                        """, (rn_row[0],))
                        row = c.fetchone()

                conn.close()

                if row:
                    found_data = {
                        "name": row[0],
                        "server": row[1],
                        "clan": row[2],
                        "rank": row[3],
                        "power": row[4],
                        "level": row[5],
                        "last_updated": row[6],
                        "char_class": row[7] if len(row) > 7 and row[7] else "Theo bảng xếp hạng"
                    }
            except Exception:
                pass

            # BƯỚC 2: Nếu chưa có trong CSDL App -> Lấy trực tiếp từ trang chủ MIR4
            if not found_data:
                data_source_label = "Trực tiếp từ Trang Chủ MIR4 (Live Homepage)"
                server_list = get_live_mir4_server_list()
                with ThreadPoolExecutor(max_workers=8) as executor:
                    futures = [
                        executor.submit(self.search_server_direct, srv, encoded_name, clean_search)
                        for srv in server_list
                    ]
                    for fut in as_completed(futures):
                        try:
                            res = fut.result()
                            if res:
                                found_data = res
                                break
                        except Exception:
                            pass

            # BƯỚC 3: Kiểm tra bổ sung qua API XDRACO / Trang chủ
            nft_data = self.query_xdraco_nft()
            if nft_data:
                if not found_data:
                    found_data = nft_data
                    data_source_label = "Trực tiếp từ Trang Chủ MIR4 / XDRACO"
                    save_character_record(found_data)
                else:
                    if found_data.get("level") in ("N/A", "Theo bảng Lực chiến") and nft_data.get("level"):
                        found_data["level"] = nft_data["level"]
                    if found_data.get("char_class") in ("N/A", "Theo bảng xếp hạng", "Chưa xác định") and nft_data.get("char_class"):
                        found_data["char_class"] = nft_data["char_class"]
                    save_character_record(found_data)

            # BƯỚC 4: Quét các trang bảng xếp hạng nếu chưa thấy
            if not found_data:
                server_list = get_live_mir4_server_list()
                for srv in server_list:
                    if time.time() - start_time >= self.search_timeout:
                        break
                    g_id = srv["group_id"]
                    w_id = srv["world_id"]
                    s_name = srv["name"]

                    for p in range(1, 51):
                        if time.time() - start_time >= self.search_timeout:
                            break
                        url = f"https://forum.mir4global.com/rank?ranktype=1000&worldgroupid={g_id}&worldid={w_id}&page={p}"
                        html_code = self.fetch_url(url, timeout=4)
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

            # BƯỚC 5: Truy xuất lịch sử Máy chủ, Clan, Đổi tên (Tối đa 5 lần kèm thời gian)
            target_lookup = (found_data.get("name", self.char_name) if found_data else self.char_name).lower()
            servers = []
            clans = []
            names_hist = []
            try:
                conn = sqlite3.connect(db_path, timeout=5.0)
                c = conn.cursor()
                
                c.execute("SELECT server_name, timestamp FROM server_history WHERE LOWER(char_name) = ? ORDER BY id DESC", (target_lookup,))
                for s_name, t_stamp in c.fetchall():
                    if s_name and s_name != "Chưa xác định":
                        if not servers or servers[-1][0].lower() != s_name.lower():
                            servers.append((s_name, t_stamp))

                c.execute("SELECT clan_name, timestamp FROM clan_history WHERE LOWER(char_name) = ? ORDER BY id DESC", (target_lookup,))
                for c_name, t_stamp in c.fetchall():
                    if c_name and c_name != "Không có Clan":
                        if not clans or clans[-1][0].lower() != c_name.lower():
                            clans.append((c_name, t_stamp))

                c.execute("""
                    SELECT old_name, new_name, timestamp FROM name_history
                    WHERE LOWER(char_name) = ? OR LOWER(old_name) = ? OR LOWER(new_name) = ?
                    ORDER BY id DESC
                """, (target_lookup, clean_search, target_lookup))
                seen_nm_pairs = set()
                for o_nm, n_nm, t_stamp in c.fetchall():
                    pair_key = (o_nm.lower(), n_nm.lower(), t_stamp)
                    if pair_key not in seen_nm_pairs and o_nm.lower() != n_nm.lower():
                        seen_nm_pairs.add(pair_key)
                        names_hist.append((o_nm, n_nm, t_stamp))

                conn.close()
            except Exception:
                pass

            is_timeout = (time.time() - start_time >= self.search_timeout) and not found_data

            success = False
            if found_data:
                success = True
                display_name = found_data.get("name", self.char_name)
                level = found_data.get("level", "Theo bảng Lực chiến")
                power = found_data.get("power", "N/A")
                current_server = found_data.get("server", "Chưa xác định")
                current_clan = found_data.get("clan", "Không có Clan")
                ranking_rank = found_data.get("rank", "Chưa xếp hạng")
                char_class = found_data.get("char_class", "Theo bảng xếp hạng")
                last_updated = found_data.get("last_updated", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            elif is_timeout:
                display_name = self.char_name
                level = "Hết thời gian tìm kiếm (Quá 1 phút)"
                power = "Chưa tìm thấy dữ liệu tương thích"
                current_server = "Chưa xác định"
                current_clan = "Không có Clan"
                ranking_rank = "Đã dừng quét tự động sau 60 giây"
                char_class = "Chưa xác định"
                last_updated = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            else:
                display_name = self.char_name
                level = "Chưa lọt Top 1000 Máy Chủ"
                power = "Chưa lọt Top 1000 Máy Chủ"
                current_server = "Chưa xác định"
                current_clan = "Không có Clan"
                ranking_rank = "Ngoài phạm vi bảng xếp hạng"
                char_class = "Chưa xác định"
                last_updated = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            if names_hist:
                latest_o, latest_n, latest_t = names_hist[0]
                rename_summary = f"{latest_o} ➔ {latest_n} ({latest_t})"
            else:
                rename_summary = "Chưa ghi nhận đổi tên"

            out = []
            out.append("======================================================================")
            out.append(f" TÊN NHÂN VẬT       : {display_name}")
            out.append(f" LỊCH SỬ ĐỔI TÊN    : {rename_summary}")
            out.append(f" PHÁI (CLASS)       : {char_class}")
            out.append(f" THỨ HẠNG TRÊN RANK : {ranking_rank}")
            out.append(f" MÁY CHỦ HIỆN TẠI   : {current_server}")
            out.append(f" CLAN HIỆN TẠI      : {current_clan}")
            out.append(f" LỰC CHIẾN (PS)     : {power}")
            out.append(f" CẤP ĐỘ (LEVEL)     : {level}")
            out.append(f" NGUỒN DỮ LIỆU      : {data_source_label}")
            out.append(f" CẬP NHẬT LÚC       : {last_updated}")
            out.append("======================================================================")
            
            out.append("\n[+] LỊCH SỬ MÁY CHỦ (SERVER HISTORY - TỐI ĐA 5 LẦN THAY ĐỔI):")
            out.append(f"  • Máy chủ hiện tại   : {current_server}")
            prev_servers = [
                (s_name, t_stamp) for s_name, t_stamp in servers
                if s_name.strip().lower() != current_server.strip().lower() and s_name != "Chưa xác định"
            ][:5]
            if prev_servers:
                for idx, (s_name, t_stamp) in enumerate(prev_servers, 1):
                    out.append(f"  • Lần đổi {idx:<2}         : {s_name:<18} (Thời gian: {t_stamp})")
            else:
                out.append("  • Máy chủ trước đây  : Chưa ghi nhận thay đổi máy chủ")

            out.append("\n[+] LỊCH SỬ CLAN (CLAN HISTORY - TỐI ĐA 5 LẦN THAY ĐỔI):")
            out.append(f"  • Clan hiện tại      : {current_clan}")
            prev_clans = [
                (c_name, t_stamp) for c_name, t_stamp in clans
                if c_name.strip().lower() != current_clan.strip().lower() and c_name != "Không có Clan"
            ][:5]
            if prev_clans:
                for idx, (c_name, t_stamp) in enumerate(prev_clans, 1):
                    out.append(f"  • Lần đổi {idx:<2}         : {c_name:<18} (Thời gian: {t_stamp})")
            else:
                out.append("  • Clan trước đây     : Chưa ghi nhận thay đổi Clan")

            out.append("\n[+] LỊCH SỬ ĐỔI TÊN (NAME HISTORY - TỐI ĐA 5 LẦN THAY ĐỔI):")
            if names_hist:
                for idx, (o_name, n_name, t_stamp) in enumerate(names_hist[:5], 1):
                    out.append(f"  • Lần đổi {idx:<2}         : {o_name} ➔ {n_name} (Thời gian: {t_stamp})")
            else:
                out.append("  • Chưa ghi nhận lịch sử đổi tên")

            if is_timeout:
                out.append("\n[!] THÔNG BÁO: Đã hết thời gian tìm kiếm tối đa (1 phút).")
                out.append("    Hệ thống đã dừng lại để bạn có thể tra cứu tài khoản khác.")

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
        self.signals.ip_info_updated.connect(self.on_ip_info_updated)
        self.signals.completer_updated.connect(self.on_completer_updated)

        self.worker = None
        self.lan_ip = "Đang lấy IP..."
        self.vpn_ip = "Đang kết nối VPN..."
        self.wan_ip = ""

        # 1. Khởi chạy Socket Server
        self.server = MasterServer(self.username, self.signals)
        self.server.start()

        # 2. Hiển thị giao diện ngay lập tức (0.1s), KHÔNG chạy lệnh chờ trên luồng UI
        self.init_ui()

        # 3. Khởi chạy luồng ngầm quản lý VPN Tailscale & Firewall
        self.net_watchdog = NetworkWatchdogThread(self.signals)
        self.net_watchdog.start()

        # 4. Khởi chạy luồng ngầm cào dữ liệu MIR4 định kỳ 3 giờ
        self.crawler_thread = Mir4FullCrawlerThread(self.signals)
        self.crawler_thread.start()

    def on_ip_info_updated(self, lan_ip, vpn_ip, wan_ip):
        self.lan_ip = lan_ip
        self.vpn_ip = vpn_ip
        self.wan_ip = wan_ip
        ip_info = f"LAN: <font color='#00e676'><b>{self.lan_ip}</b></font>"
        if self.vpn_ip:
            ip_info += f" | VPN TAILSCALE: <font color='#ffeb3b'><b>{self.vpn_ip}</b></font>"
        if self.wan_ip:
            ip_info += f" | WAN: <font color='#00e5ff'><b>{self.wan_ip}</b></font>"
        self.user_lbl.setText(f"Tài khoản quản trị: <b><font color='#ffd700'>{self.username}</font></b> | IP Máy Chính ({ip_info}) | Port: <b>9999</b>")

    def on_completer_updated(self, names_list):
        try:
            self.history_names = list(names_list)
            self.completer_model.setStringList(self.history_names)
        except Exception:
            pass

    def closeEvent(self, event):
        self.net_watchdog.running = False
        self.crawler_thread.running = False
        kill_tailscale_processes()
        event.accept()

    def init_ui(self):
        self.setWindowTitle(f"AutoGame Master Hub - [Tài khoản: {self.username}]")
        self.resize(1240, 800)
        self.setStyleSheet("background-color: #121214; color: #ffffff;")

        if os.path.exists("app_icon.ico"):
            self.setWindowIcon(QIcon("app_icon.ico"))

        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        layout = QVBoxLayout(main_widget)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        ad_banner = QFrame()
        ad_banner.setStyleSheet("""
            background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #b8860b, stop:1 #d4af37);
            border-radius: 8px;
            padding: 8px;
        """)
        ad_layout = QVBoxLayout(ad_banner)
        ad_title = QLabel("★ HỆ THỐNG TRUY XUẤT THÔNG TIN TÀI KHOẢN THEO TÊN (PHAMHUNG) ★")
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
        ip_info = f"LAN: <font color='#00e676'><b>{self.lan_ip}</b></font> | VPN TAILSCALE: <font color='#ffeb3b'><b>{self.vpn_ip}</b></font>"

        self.user_lbl = QLabel(f"Tài khoản quản trị: <b><font color='#ffd700'>{self.username}</font></b> | IP Máy Chính ({ip_info}) | Port: <b>9999</b>")
        self.user_lbl.setFont(QFont("Segoe UI", 10))
        top_bar.addWidget(self.user_lbl)

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
        self.txt_char_search.setPlaceholderText("Nhập tên nhân vật (tự động gợi ý tên đã cào hoặc tìm trực tiếp từ trang chủ)...")
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

        self.history_names = []
        self.completer_model = QStringListModel(self.history_names)
        self.completer = QCompleter(self.completer_model, self)
        self.completer.setCaseSensitivity(Qt.CaseInsensitive)
        self.completer.setFilterMode(Qt.MatchContains)
        self.completer.setMaxVisibleItems(15)
        self.completer.popup().setStyleSheet("""
            QListView {
                background-color: #24242c;
                color: #00e676;
                border: 1px solid #ff6600;
                font-size: 13px;
                font-weight: bold;
                selection-background-color: #ff6600;
                selection-color: #ffffff;
            }
        """)
        self.txt_char_search.setCompleter(self.completer)
        search_box.addWidget(self.txt_char_search)

        self.btn_search_char = QPushButton("🔍 TÌM KIẾM")
        self.btn_search_char.setFixedHeight(38)
        self.btn_search_char.setStyleSheet("background-color: #ff6600; color: #fff; font-weight: bold; padding: 0 25px; border-radius: 4px;")
        self.btn_search_char.clicked.connect(self.on_search_char_clicked)
        search_box.addWidget(self.btn_search_char)

        tab2_layout.addLayout(search_box)

        lbl_res_header = QLabel("THÔNG TIN CHI TIẾT TỪ TRANG CHỦ & RANKING MIR4 (LƯU TRỮ 180 NGÀY - CẬP NHẬT MỖI 3 GIỜ):")
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
        self.txt_char_details.setText(
            f"[*] Đang truy xuất thông tin nhân vật [{name}]...\n"
            f"    - Kiểm tra dữ liệu đã cào trong CSDL (Lưu trữ 180 ngày).\n"
            f"    - Nếu chưa có trong App, hệ thống sẽ lấy trực tiếp từ trang chủ MIR4."
        )

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
        if name and all(name.lower() != x.lower() for x in self.history_names):
            self.history_names.insert(0, name)
            self.completer_model.setStringList(self.history_names)

    def show_context_menu(self, pos):
        selected_rows = self.table.selectionModel().selectedRows()
        if not selected_rows:
            return

        menu = QMenu(self)
        menu.setStyleSheet("QMenu { background-color: #24242c; color: #ffffff; border: 1px solid #444; font-weight: bold; } QMenu::item:selected { background-color: #c62828; color: #ffffff; }")

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
                f"- Hệ thống máy con sẽ đóng toàn bộ game, launcher cũ, tắt QT & TINKER.\n"
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
        self.is_logged_in = False  # Cờ bảo vệ: Không tắt VPN khi chuyển sang MasterAppWindow
        self.pref_path = os.path.join(get_app_dir(), PREF_FILE)
        self.init_ui()
        self.load_remembered_credentials()

    def closeEvent(self, event):
        # Chỉ tắt Tailscale nếu người dùng bấm X thoát ứng dụng tại màn hình đăng nhập
        if not self.is_logged_in:
            kill_tailscale_processes()
        event.accept()

    def init_ui(self):
        self.setWindowTitle("Đăng Nhập Quản Trị Hệ Thống")
        self.resize(360, 360)
        self.setStyleSheet("background-color: #121214; color: #ffffff; font-family: 'Segoe UI';")

        if os.path.exists("app_icon.ico"):
            self.setWindowIcon(QIcon("app_icon.ico"))

        layout = QVBoxLayout(self)

        title = QLabel("HỆ THỐNG QUẢN TRỊ MÁY CHỦ")
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
            self.is_logged_in = True
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
    init_db()
    init_tracker_db()
    app = QApplication(sys.argv)
    login = LoginWindow()
    login.show()
    sys.exit(app.exec_())
