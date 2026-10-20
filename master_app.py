import sys
import os
import socket
import threading
import json
import sqlite3
import hashlib
import subprocess
import ctypes
import re
import html
import time
import random
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QTableWidget, QTableWidgetItem,
    QHeaderView, QMessageBox, QFrame, QCheckBox, QAbstractItemView,
    QMenu, QAction, QTabWidget, QTextEdit, QCompleter
)
from PyQt5.QtCore import Qt, pyqtSignal, QObject, QThread, QStringListModel
from PyQt5.QtGui import QFont, QColor, QIcon

from embedded_tailscale import EmbeddedTailscale

# ==================== CẤU HÌNH HỆ THỐNG & KEY BẢN QUYỀN ====================
SERVER_PORT = 9999
USER_DB_NAME = "master_users.db"
TRACKER_DB = "mir4_tracker_history.db"
LOGIN_PREF_FILE = "login_pref.json"
LICENSE_FILE = "license.lic"
MAX_HISTORY_ENTRIES = 5  # Hiển thị tối đa 5 lần thay đổi Server / Clan

# KEY DÙNG CHUNG DUY NHẤT (Bạn có thể đổi chuỗi này tùy ý)
MASTER_LICENSE_KEY = "PHAMHUNG-MIR4-2026"

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36 Edg/123.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
]

def get_random_headers():
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://forum.mir4global.com/rank?ranktype=1"
    }

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

def get_app_dir():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

def get_machine_ips(ts_obj=None):
    lan_ip = "127.0.0.1"
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        lan_ip = s.getsockname()[0]
        s.close()
    except Exception:
        pass

    vpn_ip = ""
    if ts_obj:
        try:
            vpn_ip = ts_obj.get_my_ip()
        except Exception:
            pass

    wan_ip = ""
    try:
        req = urllib.request.Request("https://api.ipify.org", headers={'User-Agent': 'Mozilla/5.0'})
        wan_ip = urllib.request.urlopen(req, timeout=3).read().decode('utf8').strip()
    except Exception:
        pass
    return lan_ip, vpn_ip, wan_ip

# ==================== KIỂM TRA BẢN QUYỀN KEY ====================
def is_app_activated():
    lic_path = os.path.join(get_app_dir(), LICENSE_FILE)
    if not os.path.exists(lic_path):
        return False
    try:
        with open(lic_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data.get("key", "").strip() == MASTER_LICENSE_KEY
    except Exception:
        return False

def save_activation_key(user_key):
    lic_path = os.path.join(get_app_dir(), LICENSE_FILE)
    try:
        with open(lic_path, "w", encoding="utf-8") as f:
            json.dump({"key": user_key.strip()}, f)
    except Exception:
        pass

# ==================== DATABASE QUẢN LÝ TÀI KHOẢN MASTER ====================
def init_user_db():
    db_path = os.path.join(get_app_dir(), USER_DB_NAME)
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

def hash_pw(password):
    return hashlib.sha256(password.encode('utf-8')).hexdigest()

def authenticate_user(username, password):
    db_path = os.path.join(get_app_dir(), USER_DB_NAME)
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("SELECT password_hash FROM users WHERE username = ?", (username.lower(),))
    row = c.fetchone()
    conn.close()
    return bool(row and row[0] == hash_pw(password))

def register_user(username, password):
    if not username or not password:
        return False, "Tài khoản và mật khẩu không được rỗng!"
    db_path = os.path.join(get_app_dir(), USER_DB_NAME)
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    try:
        c.execute("INSERT INTO users VALUES (?, ?)", (username.lower(), hash_pw(password)))
        conn.commit()
        return True, "Đăng ký thành công!"
    except sqlite3.IntegrityError:
        return False, "Tài khoản đã tồn tại!"
    finally:
        conn.close()

# ==================== DATABASE THEO DÕI NHÂN VẬT & LỊCH SỬ ====================
def init_tracker_db():
    db_path = os.path.join(get_app_dir(), TRACKER_DB)
    conn = sqlite3.connect(db_path, timeout=30.0)
    c = conn.cursor()
    c.execute("PRAGMA journal_mode=WAL;")
    c.execute("PRAGMA busy_timeout = 30000;")
    c.execute('''
        CREATE TABLE IF NOT EXISTS characters (
            char_name TEXT PRIMARY KEY,
            current_server TEXT,
            current_clan TEXT,
            rank TEXT,
            power TEXT,
            level TEXT,
            last_updated TEXT
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS clan_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            char_name TEXT,
            clan_name TEXT,
            timestamp TEXT
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS server_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            char_name TEXT,
            server_name TEXT,
            timestamp TEXT
        )
    ''')
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
            conn = sqlite3.connect(db_path, timeout=20.0)
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
            return

        server = data_obj.get("server", "Chưa xác định")
        clan = data_obj.get("clan", "Không có Clan")
        rank = str(data_obj.get("rank", "N/A"))
        power = str(data_obj.get("power", "N/A"))
        level = str(data_obj.get("level", "Theo bảng Lực chiến"))

        c.execute("SELECT current_server, current_clan FROM characters WHERE LOWER(char_name) = LOWER(?)", (c_name,))
        row = c.fetchone()

        if not row:
            c.execute("INSERT OR REPLACE INTO characters VALUES (?, ?, ?, ?, ?, ?, ?)",
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
    except Exception:
        pass
    finally:
        if should_close and conn:
            conn.close()

def get_character_history(char_name):
    clean_name = char_name.strip().lower()
    db_path = os.path.join(get_app_dir(), TRACKER_DB)
    conn = sqlite3.connect(db_path, timeout=15.0)
    c = conn.cursor()

    c.execute("""
        SELECT char_name, current_server, current_clan, rank, power, level, last_updated 
        FROM characters 
        WHERE LOWER(char_name) = ?
    """, (clean_name,))
    char_row = c.fetchone()

    if not char_row:
        conn.close()
        return None

    # Lấy lịch sử máy chủ (tối đa 5 lần)
    c.execute("SELECT server_name, timestamp FROM server_history WHERE LOWER(char_name) = ? ORDER BY id DESC", (clean_name,))
    raw_servers = c.fetchall()
    servers = []
    for s_name, t_stamp in raw_servers:
        if s_name and s_name != "Chưa xác định":
            if not servers or servers[-1][0].lower() != s_name.lower():
                servers.append((s_name, t_stamp))
        if len(servers) >= MAX_HISTORY_ENTRIES:
            break

    # Lấy lịch sử Clan (tối đa 5 lần)
    c.execute("SELECT clan_name, timestamp FROM clan_history WHERE LOWER(char_name) = ? ORDER BY id DESC", (clean_name,))
    raw_clans = c.fetchall()
    clans = []
    for c_name, t_stamp in raw_clans:
        if c_name and c_name != "Không có Clan":
            if not clans or clans[-1][0].lower() != c_name.lower():
                clans.append((c_name, t_stamp))
        if len(clans) >= MAX_HISTORY_ENTRIES:
            break

    conn.close()
    return {
        "name": char_row[0],
        "server": char_row[1],
        "clan": char_row[2],
        "rank": char_row[3],
        "power": char_row[4],
        "level": char_row[5],
        "last_updated": char_row[6],
        "servers": servers,
        "clans": clans
    }

def get_searched_char_names():
    try:
        db_path = os.path.join(get_app_dir(), TRACKER_DB)
        conn = sqlite3.connect(db_path, timeout=5.0)
        c = conn.cursor()
        c.execute("SELECT char_name FROM characters ORDER BY last_updated DESC LIMIT 1000")
        rows = c.fetchall()
        conn.close()
        return [r[0] for r in rows if r[0]]
    except Exception:
        return []

# ==================== BÓC TÁCH WEB MIR4 & DỰ PHÒNG TOÀN DIỆN ====================
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

def get_live_mir4_server_list():
    extracted_servers = []
    urls = [
        "https://forum.mir4global.com/rank?ranktype=1",
        "https://forum.mir4global.com/rank?ranktype=1000"
    ]
    for test_url in urls:
        try:
            req = urllib.request.Request(test_url, headers=get_random_headers())
            with urllib.request.urlopen(req, timeout=8) as resp:
                if resp.status == 200:
                    raw_html = resp.read().decode('utf-8', errors='ignore')
                    matches = re.findall(r'worldgroupid=([0-9]+)&worldid=([0-9]+)[^>]*>([^<]+)</a>', raw_html, re.IGNORECASE)
                    for g_id, w_id, s_name in matches:
                        clean_name = s_name.strip()
                        if clean_name and clean_name.lower() != "all":
                            extracted_servers.append({
                                "group_id": int(g_id),
                                "world_id": int(w_id),
                                "name": clean_name
                            })
                    if extracted_servers:
                        break
        except Exception:
            pass

    if not extracted_servers:
        extracted_servers = [
            {"group_id": 1, "world_id": 811, "name": "ASIA011"}, {"group_id": 1, "world_id": 812, "name": "ASIA012"},
            {"group_id": 1, "world_id": 821, "name": "ASIA021"}, {"group_id": 1, "world_id": 822, "name": "ASIA022"},
            {"group_id": 1, "world_id": 823, "name": "ASIA023"}, {"group_id": 1, "world_id": 824, "name": "ASIA024"},
            {"group_id": 1, "world_id": 831, "name": "ASIA031"}, {"group_id": 1, "world_id": 832, "name": "ASIA032"},
            {"group_id": 1, "world_id": 833, "name": "ASIA033"}, {"group_id": 1, "world_id": 834, "name": "ASIA034"},
            {"group_id": 1, "world_id": 841, "name": "ASIA041"}, {"group_id": 1, "world_id": 842, "name": "ASIA042"},
            {"group_id": 2, "world_id": 921, "name": "ASIA21"}, {"group_id": 2, "world_id": 922, "name": "ASIA22"},
            {"group_id": 2, "world_id": 923, "name": "ASIA23"}, {"group_id": 2, "world_id": 931, "name": "ASIA31"},
            {"group_id": 2, "world_id": 932, "name": "ASIA32"}, {"group_id": 2, "world_id": 933, "name": "ASIA33"},
            {"group_id": 3, "world_id": 941, "name": "ASIA321"}, {"group_id": 3, "world_id": 942, "name": "ASIA322"},
            {"group_id": 3, "world_id": 943, "name": "ASIA323"}, {"group_id": 3, "world_id": 951, "name": "ASIA331"},
            {"group_id": 3, "world_id": 952, "name": "ASIA332"}, {"group_id": 3, "world_id": 953, "name": "ASIA333"},
            {"group_id": 3, "world_id": 954, "name": "ASIA334"},
            {"group_id": 11, "world_id": 411, "name": "INMENA011"}, {"group_id": 11, "world_id": 412, "name": "INMENA012"},
            {"group_id": 21, "world_id": 711, "name": "EU011"}, {"group_id": 21, "world_id": 712, "name": "EU012"},
            {"group_id": 31, "world_id": 511, "name": "SA011"}, {"group_id": 31, "world_id": 512, "name": "SA012"},
            {"group_id": 41, "world_id": 611, "name": "NA011"}, {"group_id": 41, "world_id": 612, "name": "NA012"}
        ]
    return extracted_servers

# ==================== LUỒNG CÀO DỮ LIỆU ĐỊNH KỲ 12H ====================
class Mir4FullCrawlerThread(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.running = True

    def run(self):
        time.sleep(10)

        while self.running:
            try:
                prune_old_tracker_records()
                server_list = get_live_mir4_server_list()
                db_path = os.path.join(get_app_dir(), TRACKER_DB)

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
                            req = urllib.request.Request(url, headers=get_random_headers())
                            with urllib.request.urlopen(req, timeout=10) as resp:
                                if resp.status == 200:
                                    content = resp.read().decode('utf-8', errors='ignore')
                                    records = parse_mir4_table_rows(content, target_server=s_name)
                                    if records:
                                        conn = sqlite3.connect(db_path, timeout=30.0)
                                        for r in records:
                                            save_character_record(r, conn)
                                        conn.commit()
                                        conn.close()
                        except urllib.error.HTTPError as http_err:
                            if http_err.code in (429, 403):
                                time.sleep(15)
                        except Exception:
                            pass

                        time.sleep(random.uniform(0.6, 1.0))
            except Exception:
                pass

            for _ in range(432):
                if not self.running:
                    break
                time.sleep(100)

# ==================== LUỒNG TÌM KIẾM NHANH (TIMEOUT 60S & LIVE SCAN) ====================
class Mir4QueryWorker(QThread):
    finished = pyqtSignal(str, bool)

    def __init__(self, char_name):
        super().__init__()
        self.char_name = char_name.strip()

    def fetch_url(self, url):
        try:
            req = urllib.request.Request(url, headers=get_random_headers())
            with urllib.request.urlopen(req, timeout=6) as resp:
                if resp.status == 200:
                    return resp.read().decode('utf-8', errors='ignore')
        except Exception:
            pass
        return ""

    def run(self):
        start_time = time.time()
        try:
            clean_search = self.char_name.lower()
            info = get_character_history(self.char_name)

            if not info or info.get("server") == "Chưa xác định":
                server_list = get_live_mir4_server_list()
                found = False
                for srv in server_list:
                    if found or (time.time() - start_time > 55):
                        break
                    g_id = srv["group_id"]
                    w_id = srv["world_id"]
                    s_name = srv["name"]

                    for p in range(1, 51):
                        if (time.time() - start_time > 55):
                            break
                        url = f"https://forum.mir4global.com/rank?ranktype=1000&worldgroupid={g_id}&worldid={w_id}&page={p}"
                        html_code = self.fetch_url(url)
                        if not html_code:
                            continue

                        if clean_search in html_code.lower():
                            rows = parse_mir4_table_rows(html_code, target_server=s_name)
                            for r in rows:
                                save_character_record(r)
                                if r["name"].strip().lower() == clean_search:
                                    found = True
                                    break
                        if found:
                            break
                        time.sleep(0.3)

                info = get_character_history(self.char_name)

            if not info:
                self.finished.emit(f"Không tìm thấy nhân vật [{self.char_name}] trên hệ thống và bảng xếp hạng MIR4!", False)
                return

            current_server = info.get("server", "Chưa xác định")
            current_clan = info.get("clan", "Không có Clan")
            ranking_rank = info.get("rank", "Chưa xếp hạng")
            power = info.get("power", "N/A")
            level = info.get("level", "Theo bảng Lực chiến")
            servers = info.get("servers", [])
            clans = info.get("clans", [])

            out = []
            out.append("======================================================================")
            out.append(f" TÊN NHÂN VẬT       : {self.char_name}")
            out.append(f" THỨ HẠNG TRÊN RANK : {ranking_rank}")
            out.append(f" MÁY CHỦ HIỆN TẠI   : {current_server}")
            out.append(f" CLAN HIỆN TẠI      : {current_clan}")
            out.append(f" LỰC CHIẾN (PS)     : {power}")
            out.append(f" CẤP ĐỘ (LEVEL)     : {level}")
            out.append("======================================================================")

            out.append("\n[+] LỊCH SỬ MÁY CHỦ (SERVER HISTORY - TỐI ĐA 5 LẦN THAY ĐỔI):")
            out.append(f"  • Máy chủ hiện tại   : {current_server}")
            past_servers = [s for s in servers if s[0].strip().lower() != current_server.strip().lower() and s[0] != "Chưa xác định"]
            if past_servers:
                for idx, (s_name, t_stamp) in enumerate(past_servers[:5], 1):
                    out.append(f"  • Thay đổi lần {idx:<5}: {s_name:<18} (Ghi nhận: {t_stamp})")
            else:
                out.append("  • Lịch sử trước đây  : Chưa ghi nhận thay đổi máy chủ")

            out.append("\n[+] LỊCH SỬ CLAN (CLAN HISTORY - TỐI ĐA 5 LẦN THAY ĐỔI):")
            out.append(f"  • Clan hiện tại      : {current_clan}")
            past_clans = [c for c in clans if c[0].strip().lower() != current_clan.strip().lower() and c[0] != "Không có Clan"]
            if past_clans:
                for idx, (c_name, t_stamp) in enumerate(past_clans[:5], 1):
                    out.append(f"  • Thay đổi lần {idx:<5}: {c_name:<20} (Ghi nhận: {t_stamp})")
            else:
                out.append("  • Lịch sử trước đây  : Chưa ghi nhận thay đổi Clan")

            out.append("\n======================================================================")
            self.finished.emit("\n".join(out), True)
        except Exception as e:
            self.finished.emit(f"LỖI TRUY VẤN: {str(e)}", False)

# ==================== SOCKET SERVER QUẢN LÝ MÁY CON ====================
class MasterServerSignals(QObject):
    client_updated = pyqtSignal(dict)
    client_disconnected = pyqtSignal(str)

class MasterServer(threading.Thread):
    def __init__(self, username, signals, port=SERVER_PORT):
        super().__init__(daemon=True)
        self.username = username.lower()
        self.signals = signals
        self.port = port
        self.clients = {}
        self.running = True

    def run(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("0.0.0.0", self.port))
        srv.listen(100)

        while self.running:
            try:
                conn, addr = srv.accept()
                threading.Thread(target=self.handle_client, args=(conn, addr), daemon=True).start()
            except Exception:
                break

    def handle_client(self, conn, addr):
        node_id = None
        try:
            while self.running:
                data = conn.recv(8192)
                if not data:
                    break
                msgs = data.decode('utf-8', errors='ignore').strip().split('\n')
                for msg in msgs:
                    if not msg.strip():
                        continue
                    payload = json.loads(msg)
                    if payload.get("master_user", "").strip().lower() != self.username:
                        conn.close()
                        return

                    node_id = payload.get("node_name", addr[0])
                    self.clients[node_id] = conn

                    node_info = {
                        "node_name": node_id,
                        "ip": addr[0],
                        "qt_status": payload.get("qt_status", "OFF"),
                        "tinker_status": payload.get("tinker_status", "OFF"),
                        "uptime": payload.get("uptime", "N/A"),
                        "status": "ONLINE"
                    }
                    self.signals.client_updated.emit(node_info)
        except Exception:
            pass
        finally:
            if node_id and node_id in self.clients:
                del self.clients[node_id]
                self.signals.client_disconnected.emit(node_id)
            conn.close()

    def send_command(self, node_id, command_str):
        if node_id in self.clients:
            try:
                self.clients[node_id].sendall((command_str + "\n").encode('utf-8'))
                return True
            except Exception:
                return False
        return False

# ==================== CỬA SỔ CHÍNH (MASTER APP HUB) ====================
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

        self.lan_ip = "127.0.0.1"
        self.vpn_ip = "Đang kết nối..."
        self.wan_ip = ""
        self.init_ui()

        threading.Thread(target=self._async_init_network, daemon=True).start()
        self.crawler_thread = Mir4FullCrawlerThread()
        self.crawler_thread.start()

    def _async_init_network(self):
        try:
            self.ts = EmbeddedTailscale()
            self.ts.start_and_connect()
            self.lan_ip, self.vpn_ip, self.wan_ip = get_machine_ips(self.ts)

            ip_info = f"LAN: <font color='#00e676'><b>{self.lan_ip}</b></font>"
            if self.vpn_ip and self.vpn_ip != "Đang kết nối...":
                ip_info += f" | VPN TAILSCALE: <font color='#ffeb3b'><b>{self.vpn_ip}</b></font>"
            if self.wan_ip:
                ip_info += f" | WAN: <font color='#00e5ff'><b>{self.wan_ip}</b></font>"

            self.user_lbl.setText(f"Tài khoản quản trị: <b><font color='#ffd700'>{self.username}</font></b> | IP Máy Chính ({ip_info}) | Port: <b>9999</b>")
        except Exception:
            pass

    def init_ui(self):
        self.setWindowTitle(f"Mir4 Master Hub - [Tài khoản: {self.username}]")
        self.resize(1180, 780)
        self.setStyleSheet("background-color: #121214; color: #ffffff;")
        if os.path.exists("app_icon.ico"):
            self.setWindowIcon(QIcon("app_icon.ico"))

        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        layout = QVBoxLayout(main_widget)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        # BANNER QUẢNG CÁO
        ad_banner = QFrame()
        ad_banner.setStyleSheet("background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #b8860b, stop:1 #d4af37); border-radius: 8px; padding: 8px;")
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

        # TAB 1: QUẢN LÝ DÀN MÁY CON
        tab1_widget = QWidget()
        tab1_layout = QVBoxLayout(tab1_widget)

        top_bar = QHBoxLayout()
        ip_info = f"LAN: <font color='#00e676'><b>{self.lan_ip}</b></font> | VPN TAILSCALE: <font color='#ffeb3b'><b>{self.vpn_ip}</b></font>"
        self.user_lbl = QLabel(f"Tài khoản quản trị: <b><font color='#ffd700'>{self.username}</font></b> | IP Máy Chính ({ip_info}) | Port: <b>9999</b>")
        self.user_lbl.setFont(QFont("Segoe UI", 10))
        top_bar.addWidget(self.user_lbl)
        top_bar.addStretch()

        btn_refresh = QPushButton("Làm Mới Bảng")
        btn_refresh.setStyleSheet("background-color: #2b2b36; color: white; font-weight: bold; padding: 6px 12px; border-radius: 4px;")
        btn_refresh.clicked.connect(self.reload_names_completer)
        top_bar.addWidget(btn_refresh)
        tab1_layout.addLayout(top_bar)

        self.table = QTableWidget()
        self.table.setColumnCount(6)
        self.table.setHorizontalHeaderLabels(["Tên Máy Con", "Địa Chỉ IP", "Trạng Thái QT", "Trạng Thái TINKER", "Thời Gian Chạy", "Kết Nối"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.show_context_menu)
        self.table.setStyleSheet("""
            QTableWidget { background-color: #18181c; gridline-color: #2e2e38; color: #ffffff; }
            QHeaderView::section { background-color: #222228; color: #ff9933; font-weight: bold; padding: 6px; border: 1px solid #2e2e38; }
            QTableWidget::item:selected { background-color: #ff6600; color: white; }
        """)
        tab1_layout.addWidget(self.table)

        btn_bar = QHBoxLayout()
        btn_update = QPushButton("🚀 Update MIR4")
        btn_update.setStyleSheet("background-color: #007acc; color: white; font-weight: bold; padding: 8px 16px; border-radius: 4px;")
        btn_update.clicked.connect(lambda: self.send_command_to_selected("UPDATE_MIR4"))
        btn_bar.addWidget(btn_update)

        btn_reboot = QPushButton("🔄 Khởi Động Lại Máy Đã Chọn")
        btn_reboot.setStyleSheet("background-color: #d32f2f; color: white; font-weight: bold; padding: 8px 16px; border-radius: 4px;")
        btn_reboot.clicked.connect(lambda: self.send_command_to_selected("REBOOT"))
        btn_bar.addWidget(btn_reboot)
        btn_bar.addStretch()
        tab1_layout.addLayout(btn_bar)

        self.tabs.addTab(tab1_widget, "QUẢN LÝ DÀN MÁY CON")

        # TAB 2: TRUY XUẤT THÔNG TIN ACCOUNT MIR4
        tab2_widget = QWidget()
        tab2_layout = QVBoxLayout(tab2_widget)

        search_bar = QHBoxLayout()
        lbl_search = QLabel("TÊN NHÂN VẬT:")
        lbl_search.setFont(QFont("Segoe UI", 10, QFont.Bold))
        search_bar.addWidget(lbl_search)

        self.txt_char = QLineEdit()
        self.txt_char.setPlaceholderText("Gõ tên nhân vật cần tra cứu...")
        self.txt_char.setStyleSheet("background-color: #1e1e24; color: #ffffff; border: 1px solid #3e3e4a; padding: 8px; border-radius: 4px; font-size: 13px;")
        self.completer = QCompleter([])
        self.completer.setCaseSensitivity(Qt.CaseInsensitive)
        self.txt_char.setCompleter(self.completer)
        self.txt_char.returnPressed.connect(self.start_character_search)
        search_bar.addWidget(self.txt_char)

        self.btn_search = QPushButton("🔍 TÌM KIẾM")
        self.btn_search.setStyleSheet("background-color: #ff6600; color: white; font-weight: bold; padding: 8px 20px; border-radius: 4px; font-size: 13px;")
        self.btn_search.clicked.connect(self.start_character_search)
        search_bar.addWidget(self.btn_search)
        tab2_layout.addLayout(search_bar)

        self.txt_result = QTextEdit()
        self.txt_result.setReadOnly(True)
        self.txt_result.setFont(QFont("Consolas", 10))
        self.txt_result.setStyleSheet("background-color: #16161a; color: #00ffcc; border: 1px solid #2e2e38; border-radius: 6px; padding: 10px;")
        tab2_layout.addWidget(self.txt_result)

        self.tabs.addTab(tab2_widget, "TRUY XUẤT THÔNG TIN ACCOUNT MIR4")
        layout.addWidget(self.tabs)
        self.reload_names_completer()

    def reload_names_completer(self):
        names = get_searched_char_names()
        model = QStringListModel(names)
        self.completer.setModel(model)

    def show_context_menu(self, pos):
        selected_rows = self.table.selectionModel().selectedRows()
        if not selected_rows:
            return
        menu = QMenu(self)
        menu.setStyleSheet("QMenu { background-color: #24242c; color: #ffffff; border: 1px solid #444; font-weight: bold; } QMenu::item:selected { background-color: #c62828; color: #ffffff; }")

        action_update = QAction("🚀 UPDATE MIR4 (15 PHÚT)", self)
        action_update.triggered.connect(lambda: self.send_command_to_selected("UPDATE_MIR4"))
        menu.addAction(action_update)

        action_delete = QAction(f"🗑️ XÓA {len(selected_rows)} MÁY ĐÃ CHỌN", self)
        action_delete.triggered.connect(self.delete_selected_rows)
        menu.addAction(action_delete)
        menu.exec_(self.table.viewport().mapToGlobal(pos))

    def delete_selected_rows(self):
        selected_rows = self.table.selectionModel().selectedRows()
        for index in sorted(selected_rows, reverse=True):
            self.table.removeRow(index.row())

    def send_command_to_selected(self, cmd):
        selected_rows = self.table.selectionModel().selectedRows()
        if not selected_rows:
            QMessageBox.information(self, "Thông báo", "Vui lòng chọn ít nhất một máy con trong bảng!")
            return
        count = 0
        for index in selected_rows:
            node_name = self.table.item(index.row(), 0).text()
            if self.server.send_command(node_name, cmd):
                count += 1
        QMessageBox.information(self, "Thực thi", f"Đã gửi lệnh [{cmd}] tới {count} máy đang kết nối.")

    def update_client_row(self, data):
        node = data["node_name"]
        for row in range(self.table.rowCount()):
            if self.table.item(row, 0) and self.table.item(row, 0).text() == node:
                self.table.setItem(row, 1, QTableWidgetItem(data["ip"]))
                self.table.setItem(row, 2, QTableWidgetItem(data["qt_status"]))
                self.table.setItem(row, 3, QTableWidgetItem(data["tinker_status"]))
                self.table.setItem(row, 4, QTableWidgetItem(data["uptime"]))
                st_item = QTableWidgetItem("ONLINE")
                st_item.setForeground(QColor("#00e676"))
                self.table.setItem(row, 5, st_item)
                return

        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setItem(row, 0, QTableWidgetItem(node))
        self.table.setItem(row, 1, QTableWidgetItem(data["ip"]))
        self.table.setItem(row, 2, QTableWidgetItem(data["qt_status"]))
        self.table.setItem(row, 3, QTableWidgetItem(data["tinker_status"]))
        self.table.setItem(row, 4, QTableWidgetItem(data["uptime"]))
        st_item = QTableWidgetItem("ONLINE")
        st_item.setForeground(QColor("#00e676"))
        self.table.setItem(row, 5, st_item)

    def mark_client_offline(self, node):
        for row in range(self.table.rowCount()):
            if self.table.item(row, 0) and self.table.item(row, 0).text() == node:
                st_item = QTableWidgetItem("OFFLINE")
                st_item.setForeground(QColor("#ff5252"))
                self.table.setItem(row, 5, st_item)
                break

    def start_character_search(self):
        char_name = self.txt_char.text().strip()
        if not char_name:
            QMessageBox.warning(self, "Lỗi", "Vui lòng nhập tên nhân vật cần tra cứu!")
            return
        self.btn_search.setEnabled(False)
        self.btn_search.setText("Đang tra cứu...")
        self.txt_result.setText(f"[*] Đang truy xuất thông tin nhân vật [{char_name}] trên hệ thống và bảng xếp hạng MIR4...\nVui lòng chờ trong giây lát...")

        self.worker = Mir4QueryWorker(char_name)
        self.worker.finished.connect(self.on_search_finished)
        self.worker.start()

    def on_search_finished(self, result_text, success):
        self.txt_result.setText(result_text)
        self.btn_search.setEnabled(True)
        self.btn_search.setText("🔍 TÌM KIẾM")
        self.reload_names_completer()

    def closeEvent(self, event):
        try:
            self.crawler_thread.running = False
            self.server.running = False
            subprocess.run("taskkill /F /IM tailscaled.exe /IM tailscale.exe", shell=True, capture_output=True)
        except Exception:
            pass
        event.accept()

# ==================== GIAO DIỆN NHẬP KEY BẢN QUYỀN (THƯƠNG HIỆU) ====================
class LicenseActivationWindow(QWidget):
    def __init__(self, on_success_callback):
        super().__init__()
        self.on_success_callback = on_success_callback
        self.setWindowTitle("Xác Thực Bản Quyền Phần Mềm - PHAMHUNG")
        self.resize(440, 260)
        self.setStyleSheet("background-color: #1a1a1e; color: #ffffff;")
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 25, 30, 25)
        layout.setSpacing(12)

        title = QLabel("★ MIR4 TOOL - PHAMHUNG ★")
        title.setFont(QFont("Segoe UI", 13, QFont.Bold))
        title.setStyleSheet("color: #ff6600;")
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)

        desc = QLabel("Phần mềm quản lý máy trạm & tra cứu MIR4.\nVui lòng nhập mã Key để mở khóa sử dụng:")
        desc.setFont(QFont("Segoe UI", 9))
        desc.setStyleSheet("color: #cccccc;")
        desc.setAlignment(Qt.AlignCenter)
        layout.addWidget(desc)

        self.txt_key = QLineEdit()
        self.txt_key.setPlaceholderText("Nhập mã key (Ví dụ: PHAMHUNG-MIR4-2026)...")
        self.txt_key.setStyleSheet("background-color: #24242c; color: #fff; padding: 10px; border: 1px solid #444; border-radius: 4px; font-size: 13px;")
        layout.addWidget(self.txt_key)

        self.btn_activate = QPushButton("KÍCH HOẠT SỬ DỤNG")
        self.btn_activate.setStyleSheet("background-color: #ff6600; color: #fff; font-weight: bold; padding: 10px; border-radius: 4px; font-size: 13px;")
        self.btn_activate.clicked.connect(self.handle_activation)
        layout.addWidget(self.btn_activate)

        footer = QLabel("Liên hệ nhận Key: Zalo 0392771558")
        footer.setFont(QFont("Segoe UI", 9, QFont.Bold))
        footer.setStyleSheet("color: #ffd700; margin-top: 5px;")
        footer.setAlignment(Qt.AlignCenter)
        layout.addWidget(footer)

    def handle_activation(self):
        val = self.txt_key.text().strip()
        if val == MASTER_LICENSE_KEY:
            save_activation_key(val)
            QMessageBox.information(self, "Thành công", "Kích hoạt phần mềm thành công!\nChào mừng bạn đến với hệ thống PHAMHUNG.")
            self.close()
            self.on_success_callback()
        else:
            QMessageBox.critical(self, "Lỗi", "Mã Key không chính xác!\nVui lòng liên hệ Zalo: 0392771558 để nhận Key.")

# ==================== GIAO DIỆN ĐĂNG NHẬP MASTER ====================
class LoginWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Đăng Nhập Quản Trị - Mir4 Hub")
        self.resize(380, 320)
        self.setStyleSheet("background-color: #1a1a1e; color: #ffffff;")
        self.init_ui()
        self.load_remembered_account()

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 25, 30, 25)
        layout.setSpacing(12)

        title = QLabel("MIR4 MASTER LOGIN")
        title.setFont(QFont("Segoe UI", 14, QFont.Bold))
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("color: #ff6600; margin-bottom: 10px;")
        layout.addWidget(title)

        self.txt_user = QLineEdit()
        self.txt_user.setPlaceholderText("Tên tài khoản quản trị...")
        self.txt_user.setStyleSheet("background-color: #24242c; color: #fff; padding: 8px; border: 1px solid #333; border-radius: 4px;")
        layout.addWidget(self.txt_user)

        self.txt_pass = QLineEdit()
        self.txt_pass.setPlaceholderText("Mật khẩu...")
        self.txt_pass.setEchoMode(QLineEdit.Password)
        self.txt_pass.setStyleSheet("background-color: #24242c; color: #fff; padding: 8px; border: 1px solid #333; border-radius: 4px;")
        layout.addWidget(self.txt_pass)

        self.chk_remember = QCheckBox("Ghi nhớ tài khoản đăng nhập")
        self.chk_remember.setStyleSheet("color: #bbb; font-size: 11px;")
        self.chk_remember.setChecked(True)
        layout.addWidget(self.chk_remember)

        self.btn_login = QPushButton("Đăng Nhập")
        self.btn_login.setStyleSheet("background-color: #ff6600; color: #fff; font-weight: bold; padding: 10px; border-radius: 4px;")
        self.btn_login.clicked.connect(self.handle_login)
        layout.addWidget(self.btn_login)

        self.btn_register = QPushButton("Tạo Tài Khoản Mới")
        self.btn_register.setStyleSheet("background-color: #2b2b36; color: #fff; padding: 8px; border-radius: 4px;")
        self.btn_register.clicked.connect(self.handle_register)
        layout.addWidget(self.btn_register)

    def load_remembered_account(self):
        pref_path = os.path.join(get_app_dir(), LOGIN_PREF_FILE)
        if os.path.exists(pref_path):
            try:
                with open(pref_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.txt_user.setText(data.get("username", ""))
                    self.txt_pass.setText(data.get("password", ""))
                    self.chk_remember.setChecked(True)
            except Exception:
                pass

    def save_remembered_account(self, u, p):
        pref_path = os.path.join(get_app_dir(), LOGIN_PREF_FILE)
        if self.chk_remember.isChecked():
            try:
                with open(pref_path, "w", encoding="utf-8") as f:
                    json.dump({"username": u, "password": p}, f)
            except Exception:
                pass
        else:
            if os.path.exists(pref_path):
                try:
                    os.remove(pref_path)
                except Exception:
                    pass

    def handle_login(self):
        u = self.txt_user.text().strip().lower()
        p = self.txt_pass.text().strip()
        if authenticate_user(u, p):
            self.save_remembered_account(u, p)
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

# ==================== ĐIỂM KHỞI CHẠY ====================
def show_main_login():
    global login_window
    login_window = LoginWindow()
    login_window.show()

if __name__ == "__main__":
    setup_windows_firewall(SERVER_PORT)
    init_user_db()
    init_tracker_db()
    app = QApplication(sys.argv)

    # Kiểm tra key bản quyền trước khi vào màn hình login
    if is_app_activated():
        show_main_login()
    else:
        act_win = LicenseActivationWindow(on_success_callback=show_main_login)
        act_win.show()

    sys.exit(app.exec_())
