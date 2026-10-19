import sys
import os
import re
import json
import subprocess
import winreg
import ctypes
from ctypes import wintypes
import psutil
import socket
import threading
import time
import pyautogui

from embedded_tailscale import EmbeddedTailscale

def run_as_admin():
    try:
        if ctypes.windll.shell32.IsUserAnAdmin():
            return True
        exe = sys.executable
        if getattr(sys, 'frozen', False):
            ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, "", None, 1)
        else:
            script = os.path.abspath(__file__)
            ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, f'"{script}"', None, 1)
        sys.exit(0)
    except Exception:
        return False

run_as_admin()

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0.5

import win32gui
import win32ui
import win32con
import win32process

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, 
    QHBoxLayout, QGridLayout, QPushButton, QLabel, QLineEdit, 
    QFileDialog, QCheckBox, QFrame, QMessageBox,
    QTabWidget, QScrollArea, QComboBox, QSlider, QSizePolicy
)
from PyQt5.QtCore import Qt, QTimer, QRect, QPoint, pyqtSignal, QObject
from PyQt5.QtGui import QImage, QPixmap, QPainter, QIcon

def get_app_dir():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

def resource_path(relative_path):
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = get_app_dir()
    return os.path.join(base_path, relative_path)

CONFIG_FILE = os.path.join(get_app_dir(), "boot_config.json")
TASK_SCHEDULER_NAME = "AUTOGAME_Client_Node_AutoStart"

def get_current_exe_path():
    if getattr(sys, 'frozen', False):
        return sys.executable
    return os.path.abspath(__file__)

def get_uptime_str():
    try:
        millis = ctypes.windll.kernel32.GetTickCount64()
        seconds = int(millis / 1000)
        hours, remainder = divmod(seconds, 3600)
        minutes, sec = divmod(remainder, 60)
        return f"{hours}h {minutes}m {sec}s"
    except Exception:
        return "N/A"

def get_pids_by_path(target_path):
    if not target_path or not os.path.exists(target_path):
        return []
    target_name = os.path.basename(target_path).lower()
    target_abs = os.path.abspath(target_path).lower()
    pids = []
    my_pid = os.getpid()
    try:
        for proc in psutil.process_iter(['pid', 'name', 'exe']):
            try:
                if proc.info['pid'] == my_pid:
                    continue
                p_exe = proc.info.get('exe')
                if p_exe and os.path.abspath(p_exe).lower() == target_abs:
                    pids.append(proc.info['pid'])
                    continue
                p_name = proc.info.get('name')
                if p_name and p_name.lower() == target_name:
                    pids.append(proc.info['pid'])
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    except Exception:
        pass
    return pids

def is_process_running_by_path(target_path):
    return len(get_pids_by_path(target_path)) > 0

def get_visible_window_count_by_pids(pids):
    if not pids:
        return 0
    pids_set = set(pids)
    valid_hwnds = []

    def enum_cb(hwnd, extra):
        try:
            if win32gui.IsWindow(hwnd) and win32gui.IsWindowVisible(hwnd):
                _, win_pid = win32process.GetWindowThreadProcessId(hwnd)
                if win_pid in pids_set:
                    rect = win32gui.GetWindowRect(hwnd)
                    w = rect[2] - rect[0]
                    h = rect[3] - rect[1]
                    if w > 100 and h > 60:
                        valid_hwnds.append(hwnd)
        except Exception:
            pass
        return True

    try:
        win32gui.EnumWindows(enum_cb, None)
    except Exception:
        pass
    return len(valid_hwnds)

def get_tool_status_by_path(target_path):
    pids = get_pids_by_path(target_path)
    if not pids:
        return "OFF"
    
    count = get_visible_window_count_by_pids(pids)
    if count >= 2:
        return "ĐÃ CHẠY-CHỜ ĐĂNG NHẬP"
    elif count == 1:
        return "ON"
    return "ON"

def kill_process_by_path(target_path):
    if not target_path:
        return
    target_name = os.path.basename(target_path)
    try:
        subprocess.run(f'taskkill /F /IM "{target_name}" /T', shell=True, capture_output=True)
    except Exception:
        pass

def kill_all_mir_game_and_launcher():
    """Kill toàn bộ tiến trình Game và Launcher MIR4, bảo vệ tiến trình hiện tại"""
    my_pid = os.getpid()
    keywords = ["mir mobile", "mirmobile", "mir4g", "mir4client", "mir4launcher", "mir4", "mir"]
    for proc in psutil.process_iter(["pid", "name"]):
        try:
            if proc.info["pid"] == my_pid:
                continue
            pname = proc.info["name"].lower() if proc.info["name"] else ""
            for kw in keywords:
                if kw in pname:
                    proc.kill()
                    break
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

def kill_tailscale_processes():
    """Tắt toàn bộ VPN Tailscale ngầm khi đóng app"""
    try:
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE
        subprocess.run("taskkill /F /IM tailscaled.exe /IM tailscale.exe", shell=True, startupinfo=startupinfo)
    except Exception:
        pass

def set_task_scheduler_autostart(enable=True):
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = subprocess.SW_HIDE

    if enable:
        exe_path = get_current_exe_path()
        cmd = f'schtasks /create /tn "{TASK_SCHEDULER_NAME}" /tr "\"{exe_path}\"" /sc onlogon /rl highest /f'
        res = subprocess.run(cmd, shell=True, startupinfo=startupinfo)
        return res.returncode == 0
    else:
        cmd = f'schtasks /delete /tn "{TASK_SCHEDULER_NAME}" /f'
        res = subprocess.run(cmd, shell=True, startupinfo=startupinfo)
        return res.returncode == 0

def is_task_scheduler_autostart_enabled():
    try:
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE
        chk_cmd = f'schtasks /query /tn "{TASK_SCHEDULER_NAME}"'
        res = subprocess.run(chk_cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, startupinfo=startupinfo)
        return res.returncode == 0
    except Exception:
        return False

class ClientSignals(QObject):
    remote_command_received = pyqtSignal(str)
    connection_status_changed = pyqtSignal(str, str)
    update_progress_changed = pyqtSignal(str)

class ClientNetworkThread(threading.Thread):
    def __init__(self, main_app, signals):
        super().__init__(daemon=True)
        self.main_app = main_app
        self.signals = signals
        self.running = True

    def run(self):
        while self.running:
            cfg = self.main_app.config
            is_active = cfg.get("is_connected", False)
            master_host = cfg.get("master_host", "").strip()
            master_port = int(cfg.get("master_port", 9999))
            master_user = cfg.get("master_user", "").strip().lower()
            node_name = cfg.get("node_name", socket.gethostname()).strip()

            if not is_active or not master_host or not master_user:
                self.signals.connection_status_changed.emit("Chưa liên kết. Vui lòng bấm [LƯU VÀ KẾT NỐI]", "#888888")
                time.sleep(2)
                continue

            sock = None
            try:
                self.signals.connection_status_changed.emit(f"Đang kết nối tới {master_host}:{master_port}...", "#00e5ff")
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(5.0)
                sock.connect((master_host, master_port))
                sock.settimeout(None)

                self.signals.connection_status_changed.emit(f"🟢 ĐÃ LIÊN KẾT: {master_user}", "#00e676")

                def receive_worker(s):
                    buf = ""
                    while self.running:
                        try:
                            data = s.recv(1024).decode('utf-8')
                            if not data:
                                break
                            buf += data
                            while "\n" in buf:
                                line, buf = buf.split("\n", 1)
                                if line.strip():
                                    cmd_obj = json.loads(line)
                                    cmd = cmd_obj.get("command", "")
                                    if cmd:
                                        self.signals.remote_command_received.emit(cmd)
                        except Exception:
                            break

                rcv_thread = threading.Thread(target=receive_worker, args=(sock,), daemon=True)
                rcv_thread.start()

                while self.running and rcv_thread.is_alive():
                    if not self.main_app.config.get("is_connected", False):
                        break
                    
                    if self.main_app.is_updating_mir4:
                        qt_status = "UPDATING"
                        tinker_status = "UPDATING"
                    else:
                        qt_status = get_tool_status_by_path(self.main_app.config.get("qt_path", ""))
                        tinker_status = get_tool_status_by_path(self.main_app.config.get("tinker_path", ""))

                    payload = {
                        "master_user": master_user,
                        "node_name": node_name,
                        "qt": qt_status,
                        "tinker": tinker_status,
                        "uptime": get_uptime_str()
                    }
                    try:
                        sock.sendall((json.dumps(payload) + "\n").encode('utf-8'))
                    except Exception:
                        break
                    time.sleep(3)

            except Exception:
                self.signals.connection_status_changed.emit(f"🔴 MẤT KẾT NỐI TỚI {master_host} (Thử lại sau 5s)", "#ff5252")
            finally:
                if sock:
                    try:
                        sock.close()
                    except Exception:
                        pass
                time.sleep(5)

def capture_window_pixmap(hwnd, target_w, target_h):
    try:
        if not win32gui.IsWindow(hwnd):
            return None

        cl_left, cl_top, cl_right, cl_bot = win32gui.GetClientRect(hwnd)
        w = cl_right - cl_left
        h = cl_bot - cl_top

        if w <= 0 or h <= 0:
            rect = win32gui.GetWindowRect(hwnd)
            w = rect[2] - rect[0]
            h = rect[3] - rect[1]

        if w <= 0 or h <= 0:
            return None

        hwndDC = win32gui.GetWindowDC(hwnd)
        mfcDC = win32ui.CreateDCFromHandle(hwndDC)
        saveDC = mfcDC.CreateCompatibleDC()

        saveBitMap = win32ui.CreateBitmap()
        saveBitMap.CreateCompatibleBitmap(mfcDC, w, h)
        saveDC.SelectObject(saveBitMap)

        res = ctypes.windll.user32.PrintWindow(hwnd, saveDC.GetSafeHdc(), 2)
        if res == 0:
            res = ctypes.windll.user32.PrintWindow(hwnd, saveDC.GetSafeHdc(), 0)
            if res == 0:
                saveDC.BitBlt((0, 0), (w, h), mfcDC, (0, 0), win32con.SRCCOPY)

        bmpinfo = saveBitMap.GetInfo()
        bmpstr = saveBitMap.GetBitmapBits(True)

        img = QImage(bmpstr, bmpinfo['bmWidth'], bmpinfo['bmHeight'], QImage.Format_ARGB32)

        win32gui.DeleteObject(saveBitMap.GetHandle())
        saveDC.DeleteDC()
        mfcDC.DeleteDC()
        win32gui.ReleaseDC(hwnd, hwndDC)

        pixmap = QPixmap.fromImage(img)
        return pixmap.scaled(target_w, target_h, Qt.KeepAspectRatio, Qt.FastTransformation)
    except Exception:
        return None

class ThumbnailCard(QFrame):
    def __init__(self, hwnd, title, index, parent=None):
        super().__init__(parent)
        self.hwnd = hwnd
        self.game_title = title
        self.index = index
        self.target_w = 320
        self.target_h = 180
        self.current_pixmap = None

        self.setFrameShape(QFrame.StyledPanel)
        self.setStyleSheet("ThumbnailCard { background-color: #000000; border: 2px solid #38383e; border-radius: 4px; } ThumbnailCard:hover { border: 2px solid #ff7b00; }")

    def set_card_size(self, width, height):
        self.target_w = width
        self.target_h = height
        self.setFixedSize(self.target_w + 4, self.target_h + 4)
        self.update()

    def refresh_screen(self):
        pix = capture_window_pixmap(self.hwnd, self.target_w, self.target_h)
        if pix and not pix.isNull():
            self.current_pixmap = pix
            self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.current_pixmap and not self.current_pixmap.isNull():
            painter = QPainter(self)
            x = (self.width() - self.current_pixmap.width()) // 2
            y = (self.height() - self.current_pixmap.height()) // 2
            painter.drawPixmap(x, y, self.current_pixmap)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.activate_window()

    def activate_window(self):
        try:
            if win32gui.IsWindow(self.hwnd):
                win32gui.ShowWindow(self.hwnd, win32con.SW_RESTORE)
                user32 = ctypes.windll.user32
                current_thread = ctypes.windll.kernel32.GetCurrentThreadId()
                window_thread = user32.GetWindowThreadProcessId(self.hwnd, None)
                user32.AttachThreadInput(current_thread, window_thread, True)
                win32gui.SetForegroundWindow(self.hwnd)
                win32gui.BringWindowToTop(self.hwnd)
                user32.AttachThreadInput(current_thread, window_thread, False)
        except Exception:
            try:
                win32gui.SetForegroundWindow(self.hwnd)
            except Exception:
                pass

class MasterApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("AutoGame Client Node - Auto Launcher & Wall Manager [PHAMHUNG]")
        self.resize(1340, 920)
        self.setStyleSheet(
            "QMainWindow { background-color: #121214; }"
            "QWidget { color: #ececed; font-family: Segoe UI, sans-serif; }"
            "QTabWidget::pane { border: 1px solid #2e2e38; background-color: #121214; }"
            "QTabBar::tab { background: #1a1a1e; color: #888; font-weight: bold; font-size: 13px; padding: 10px 25px; border-top-left-radius: 6px; border-top-right-radius: 6px; margin-right: 4px; }"
            "QTabBar::tab:selected { background: #ff6600; color: white; }"
            "QMessageBox { background-color: #1e1e24; }"
            "QMessageBox QLabel { color: #ffffff; font-size: 13px; font-weight: bold; }"
            "QMessageBox QPushButton { background-color: #ff6600; color: white; font-weight: bold; min-width: 80px; height: 28px; border-radius: 4px; }"
            "QMessageBox QPushButton:hover { background-color: #ff8533; }"
        )

        if os.path.exists("app_icon.ico"):
            self.setWindowIcon(QIcon("app_icon.ico"))

        self.config = {
            "qt_path": "",
            "tinker_path": "",
            "launcher_path": "",
            "auto_start_apps": True,
            "auto_restart_crash": True,
            "delay_seconds": 2,
            "master_host": "",
            "master_port": 9999,
            "master_user": "",
            "node_name": socket.gethostname(),
            "is_connected": False
        }
        self.load_config()

        self.is_updating_mir4 = False

        self.ts = EmbeddedTailscale()
        threading.Thread(target=self.ts.start_and_connect, daemon=True).start()

        self.current_cols = 4
        self.cards = {}
        self.auto_scale = True
        self.fixed_w = 320
        self.fixed_h = 180

        self.init_ui()

        self.net_signals = ClientSignals()
        self.net_signals.connection_status_changed.connect(self.on_net_status_change)
        self.net_signals.remote_command_received.connect(self.on_remote_command)
        self.net_signals.update_progress_changed.connect(self.on_update_progress)
        self.net_thread = ClientNetworkThread(self, self.net_signals)
        self.net_thread.start()

        self.render_timer = QTimer(self)
        self.render_timer.timeout.connect(self.update_frames)
        self.render_timer.start(33)

        self.scan_timer = QTimer(self)
        self.scan_timer.timeout.connect(self.scan_windows)
        self.scan_timer.start(3000)

        self.watchdog_timer = QTimer(self)
        self.watchdog_timer.timeout.connect(self.check_and_recover_apps)
        self.watchdog_timer.start(4000)

        QTimer.singleShot(200, self.check_app_status_ui)
        QTimer.singleShot(500, self.scan_windows)

        if self.config.get("auto_start_apps", True):
            delay_ms = self.config.get("delay_seconds", 2) * 1000
            self.status_boot_label.setText(f"Tự động chạy QT sau {self.config.get('delay_seconds', 2)}s...")
            QTimer.singleShot(delay_ms, self.auto_start_both_silent)

    def closeEvent(self, event):
        """Tự động chạy CMD Taskkill quyền Admin khi đóng App"""
        kill_tailscale_processes()
        event.accept()

    def init_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(8)

        self.tabs = QTabWidget()

        # TAB 1: QUẢN LÝ AUTO
        tab_boot = QWidget()
        tab_boot_layout = QVBoxLayout(tab_boot)
        tab_boot_layout.setContentsMargins(20, 15, 20, 15)
        tab_boot_layout.setSpacing(12)

        # KHUNG KẾT NỐI MÁY CHÍNH
        net_box = QFrame()
        net_box.setStyleSheet("background-color: #17171d; border: 2px solid #ff6600; border-radius: 8px;")
        net_box_layout = QVBoxLayout(net_box)
        net_box_layout.setContentsMargins(15, 12, 15, 12)
        net_box_layout.setSpacing(10)

        h_net_title = QHBoxLayout()
        lbl_net = QLabel("📡 KẾT NỐI MÁY CHÍNH:")
        lbl_net.setStyleSheet("border: none; font-weight: bold; color: #ff944d; font-size: 13px;")
        h_net_title.addWidget(lbl_net)

        self.lbl_net_status = QLabel("⚪ Đang khởi động mạng ảo VPN ngầm...")
        self.lbl_net_status.setStyleSheet("border: none; font-weight: bold; font-size: 12px; color: #888888;")
        h_net_title.addWidget(self.lbl_net_status)
        h_net_title.addStretch()
        net_box_layout.addLayout(h_net_title)

        h_net_inputs = QHBoxLayout()
        h_net_inputs.setSpacing(10)

        h_net_inputs.addWidget(QLabel("IP Máy Chính (VPN):"))
        self.txt_master_host = QLineEdit(self.config.get("master_host", ""))
        self.txt_master_host.setPlaceholderText("VD: 100.x.y.z")
        self.txt_master_host.setStyleSheet("background-color: #242429; color: #ffffff; border: 1px solid #555; border-radius: 4px; padding: 6px;")
        h_net_inputs.addWidget(self.txt_master_host)

        h_net_inputs.addWidget(QLabel("Tài Khoản Quản Trị:"))
        self.txt_master_user = QLineEdit(self.config.get("master_user", ""))
        self.txt_master_user.setPlaceholderText("VD: admin")
        self.txt_master_user.setStyleSheet("background-color: #242429; color: #00e676; font-weight: bold; border: 1px solid #555; border-radius: 4px; padding: 6px;")
        h_net_inputs.addWidget(self.txt_master_user)

        h_net_inputs.addWidget(QLabel("Tên Máy Con:"))
        self.txt_node_name = QLineEdit(self.config.get("node_name", socket.gethostname()))
        self.txt_node_name.setPlaceholderText("VD: PC01")
        self.txt_node_name.setStyleSheet("background-color: #242429; color: #00e5ff; font-weight: bold; border: 1px solid #555; border-radius: 4px; padding: 6px;")
        h_net_inputs.addWidget(self.txt_node_name)

        self.btn_save_connect = QPushButton("💾 LƯU VÀ KẾT NỐI")
        self.btn_save_connect.setFixedHeight(34)
        self.btn_save_connect.setStyleSheet("QPushButton { background-color: #2e7d32; color: #ffffff; font-weight: bold; padding: 0 16px; border-radius: 4px; } QPushButton:hover { background-color: #388e3c; }")
        self.btn_save_connect.clicked.connect(self.on_save_and_connect_clicked)
        h_net_inputs.addWidget(self.btn_save_connect)

        net_box_layout.addLayout(h_net_inputs)
        tab_boot_layout.addWidget(net_box)

        # KHUNG QUẢN LÝ ĐƯỜNG DẪN QT, TINKER VÀ LAUNCHER
        boot_box = QFrame()
        boot_box.setStyleSheet("background-color: #1a1a1e; border: 1px solid #2e2e38; border-radius: 8px;")
        boot_box_layout = QVBoxLayout(boot_box)
        boot_box_layout.setContentsMargins(20, 15, 20, 15)
        boot_box_layout.setSpacing(12)

        # 1. QT
        h_qt_title = QHBoxLayout()
        lbl_qt = QLabel("1. Phần mềm QT:")
        lbl_qt.setStyleSheet("border: none; font-weight: bold; color: #00e5ff; font-size: 14px;")
        h_qt_title.addWidget(lbl_qt)
        self.status_qt_badge = QLabel("⚪ Đang kiểm tra...")
        self.status_qt_badge.setStyleSheet("border: none; font-size: 11px; font-weight: bold; color: #888;")
        h_qt_title.addWidget(self.status_qt_badge)
        h_qt_title.addStretch()
        boot_box_layout.addLayout(h_qt_title)

        h_qt = QHBoxLayout()
        self.txt_qt = QLineEdit(self.config.get("qt_path", ""))
        self.txt_qt.setPlaceholderText("Đường dẫn file QT (.exe)...")
        self.txt_qt.setStyleSheet("background-color: #242429; color: white; border: 1px solid #444; border-radius: 4px; padding: 6px;")
        self.txt_qt.textChanged.connect(self.save_settings)
        h_qt.addWidget(self.txt_qt)

        btn_browse_qt = QPushButton("Chọn file...")
        btn_browse_qt.setFixedHeight(32)
        btn_browse_qt.setStyleSheet("background-color: #383842; color: white; border-radius: 4px; padding: 0 12px;")
        btn_browse_qt.clicked.connect(lambda: self.browse_file(self.txt_qt))
        h_qt.addWidget(btn_browse_qt)

        btn_run_qt = QPushButton("Chạy QT")
        btn_run_qt.setFixedHeight(32)
        btn_run_qt.setStyleSheet("background-color: #007acc; color: white; font-weight: bold; border-radius: 4px; padding: 0 15px;")
        btn_run_qt.clicked.connect(lambda: self.on_manual_launch_single(self.txt_qt.text().strip(), "QT"))
        h_qt.addWidget(btn_run_qt)
        boot_box_layout.addLayout(h_qt)

        # 2. TINKER
        h_tinker_title = QHBoxLayout()
        lbl_tinker = QLabel("2. Phần mềm TINKER (Bật sau QT 30s):")
        lbl_tinker.setStyleSheet("border: none; font-weight: bold; color: #00e676; font-size: 14px;")
        h_tinker_title.addWidget(lbl_tinker)
        self.status_tinker_badge = QLabel("⚪ Đang kiểm tra...")
        self.status_tinker_badge.setStyleSheet("border: none; font-size: 11px; font-weight: bold; color: #888;")
        h_tinker_title.addWidget(self.status_tinker_badge)
        h_tinker_title.addStretch()
        boot_box_layout.addLayout(h_tinker_title)

        h_tinker = QHBoxLayout()
        self.txt_tinker = QLineEdit(self.config.get("tinker_path", ""))
        self.txt_tinker.setPlaceholderText("Đường dẫn file TINKER (.exe)...")
        self.txt_tinker.setStyleSheet("background-color: #242429; color: white; border-radius: 4px; border: 1px solid #444; padding: 6px;")
        self.txt_tinker.textChanged.connect(self.save_settings)
        h_tinker.addWidget(self.txt_tinker)

        btn_browse_tinker = QPushButton("Chọn file...")
        btn_browse_tinker.setFixedHeight(32)
        btn_browse_tinker.setStyleSheet("background-color: #383842; color: white; border-radius: 4px; padding: 0 12px;")
        btn_browse_tinker.clicked.connect(lambda: self.browse_file(self.txt_tinker))
        h_tinker.addWidget(btn_browse_tinker)

        btn_run_tinker = QPushButton("Chạy Tinker")
        btn_run_tinker.setFixedHeight(32)
        btn_run_tinker.setStyleSheet("background-color: #2e7d32; color: white; font-weight: bold; border-radius: 4px; padding: 0 15px;")
        btn_run_tinker.clicked.connect(lambda: self.on_manual_launch_single(self.txt_tinker.text().strip(), "TINKER"))
        h_tinker.addWidget(btn_run_tinker)
        boot_box_layout.addLayout(h_tinker)

        # 3. LAUNCHER MIR4 (CHỈ NHẬN LỆNH TỪ MÁY CHÍNH)
        h_launcher_title = QHBoxLayout()
        lbl_launcher = QLabel("3. Launcher MIR4 (Chỉ nhận lệnh cập nhật từ Máy Chính):")
        lbl_launcher.setStyleSheet("border: none; font-weight: bold; color: #ffd700; font-size: 14px;")
        h_launcher_title.addWidget(lbl_launcher)
        self.status_update_badge = QLabel("⚪ Chờ lệnh từ máy chính...")
        self.status_update_badge.setStyleSheet("border: none; font-size: 11px; font-weight: bold; color: #888;")
        h_launcher_title.addWidget(self.status_update_badge)
        h_launcher_title.addStretch()
        boot_box_layout.addLayout(h_launcher_title)

        h_launcher = QHBoxLayout()
        self.txt_launcher = QLineEdit(self.config.get("launcher_path", ""))
        self.txt_launcher.setPlaceholderText("Đường dẫn file Mir4Launcher.exe...")
        self.txt_launcher.setStyleSheet("background-color: #242429; color: white; border-radius: 4px; border: 1px solid #444; padding: 6px;")
        self.txt_launcher.textChanged.connect(self.save_settings)
        h_launcher.addWidget(self.txt_launcher)

        btn_browse_launcher = QPushButton("Chọn file...")
        btn_browse_launcher.setFixedHeight(32)
        btn_browse_launcher.setStyleSheet("background-color: #383842; color: white; border-radius: 4px; padding: 0 12px;")
        btn_browse_launcher.clicked.connect(lambda: self.browse_file(self.txt_launcher))
        h_launcher.addWidget(btn_browse_launcher)
        boot_box_layout.addLayout(h_launcher)

        tab_boot_layout.addWidget(boot_box)

        # Checkbox tùy chọn
        opts_layout = QVBoxLayout()
        opts_layout.setSpacing(8)

        h_opts1 = QHBoxLayout()
        self.chk_windows_startup = QCheckBox("Tự khởi động toàn bộ Hub cùng Windows (Bypass UAC)")
        self.chk_windows_startup.setChecked(is_task_scheduler_autostart_enabled())
        self.chk_windows_startup.setStyleSheet("color: #00e676; font-weight: bold; font-size: 13px;")
        self.chk_windows_startup.stateChanged.connect(self.toggle_startup)
        h_opts1.addWidget(self.chk_windows_startup)

        self.chk_auto_launch_tools = QCheckBox("Tự bật 2 Tool khi mở App (QT trước 30s)")
        self.chk_auto_launch_tools.setChecked(self.config.get("auto_start_apps", True))
        self.chk_auto_launch_tools.setStyleSheet("color: #ffffff; font-weight: bold; font-size: 13px;")
        self.chk_auto_launch_tools.stateChanged.connect(self.save_settings)
        h_opts1.addWidget(self.chk_auto_launch_tools)
        opts_layout.addLayout(h_opts1)

        self.chk_auto_restart = QCheckBox("🛡️ Giám sát & Tự động bật lại khi QT hoặc TINKER bị Tắt / Crash")
        self.chk_auto_restart.setChecked(self.config.get("auto_restart_crash", True))
        self.chk_auto_restart.setStyleSheet("color: #ffaa00; font-weight: bold; font-size: 13px;")
        self.chk_auto_restart.stateChanged.connect(self.save_settings)
        opts_layout.addWidget(self.chk_auto_restart)

        tab_boot_layout.addLayout(opts_layout)

        self.btn_launch_both = QPushButton("🚀 KHỞI CHẠY CẢ 2 PHẦN MỀM (QT TRƯỚC 30S)")
        self.btn_launch_both.setFixedHeight(45)
        self.btn_launch_both.setStyleSheet("QPushButton { background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #ff6600, stop:1 #ff8533); color: white; font-size: 14px; font-weight: bold; border-radius: 6px; } QPushButton:hover { background: #ff944d; }")
        self.btn_launch_both.clicked.connect(self.on_manual_launch_both)
        tab_boot_layout.addWidget(self.btn_launch_both)

        self.status_boot_label = QLabel("Hệ thống giám sát đang chạy ngầm.")
        self.status_boot_label.setAlignment(Qt.AlignCenter)
        self.status_boot_label.setStyleSheet("color: #00e676; font-size: 13px; font-weight: bold;")
        tab_boot_layout.addWidget(self.status_boot_label)

        tab_boot_layout.addStretch()
        self.tabs.addTab(tab_boot, "⚙️ QUẢN LÝ AUTO (QT & TINKER)")

        # TAB 2: MIR4 WALL
        tab_wall = QWidget()
        tab_wall_layout = QVBoxLayout(tab_wall)
        tab_wall_layout.setContentsMargins(5, 5, 5, 5)
        tab_wall_layout.setSpacing(8)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(12)

        self.refresh_btn = QPushButton("Làm mới ngay (F5)")
        self.refresh_btn.setFixedHeight(32)
        self.refresh_btn.setStyleSheet("QPushButton { background-color: #ff6600; color: white; font-weight: bold; padding: 0 16px; border-radius: 4px; } QPushButton:hover { background-color: #ff8533; }")
        self.refresh_btn.clicked.connect(self.scan_windows)
        toolbar.addWidget(self.refresh_btn)

        toolbar.addWidget(QLabel("Độ phân giải:"))
        self.res_combo = QComboBox()
        self.res_combo.setFixedHeight(30)
        self.res_combo.setStyleSheet("background-color: #242429; color: white; border-radius: 4px; padding: 2px 8px;")
        self.res_options = {
            "Tự động (Co giãn theo App)": None,
            "Rất nhỏ (240x135)": (240, 135),
            "Tiêu chuẩn (320x180)": (320, 180),
            "Trung bình (400x225)": (400, 225),
            "Lớn (480x270)": (480, 270),
            "Gốc chuẩn (640x360)": (640, 360)
        }
        for label in self.res_options.keys():
            self.res_combo.addItem(label)
        self.res_combo.setCurrentText("Tự động (Co giãn theo App)")
        self.res_combo.currentIndexChanged.connect(self.on_resolution_change)
        toolbar.addWidget(self.res_combo)

        toolbar.addWidget(QLabel("Số cột:"))
        self.col_combo = QComboBox()
        self.col_combo.setFixedHeight(30)
        self.col_combo.setStyleSheet("background-color: #242429; color: white; border-radius: 4px; padding: 2px 8px;")
        for c in range(1, 9):
            self.col_combo.addItem(f"{c} Cột", c)
        self.col_combo.setCurrentIndex(3)
        self.col_combo.currentIndexChanged.connect(self.on_columns_change)
        toolbar.addWidget(self.col_combo)

        toolbar.addWidget(QLabel("Tốc độ FPS:"))
        self.fps_slider = QSlider(Qt.Horizontal)
        self.fps_slider.setRange(0, 60)
        self.fps_slider.setValue(30)
        self.fps_slider.setFixedWidth(160)
        self.fps_slider.setStyleSheet("QSlider::groove:horizontal { height: 6px; background: #2b2b36; border-radius: 3px; } QSlider::sub-page:horizontal { background: #ff6600; border-radius: 3px; } QSlider::handle:horizontal { background: #ffffff; border: 2px solid #ff6600; width: 14px; margin-top: -5px; margin-bottom: -5px; border-radius: 7px; }")
        self.fps_slider.valueChanged.connect(self.on_fps_slider_changed)
        toolbar.addWidget(self.fps_slider)

        self.fps_value_label = QLabel("30 FPS")
        self.fps_value_label.setStyleSheet("color: #ffaa00; font-weight: bold; min-width: 80px;")
        toolbar.addWidget(self.fps_value_label)

        toolbar.addStretch()

        self.status_wall_label = QLabel("Đang chờ quét...")
        self.status_wall_label.setStyleSheet("color: #00e676; font-weight: bold;")
        toolbar.addWidget(self.status_wall_label)

        tab_wall_layout.addLayout(toolbar)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setStyleSheet("QScrollArea { border: 1px solid #292930; background-color: #121214; }")

        self.scroll_widget = QWidget()
        self.grid_layout = QGridLayout(self.scroll_widget)
        self.grid_layout.setContentsMargins(6, 6, 6, 6)
        self.grid_layout.setSpacing(8)
        self.grid_layout.setAlignment(Qt.AlignTop | Qt.AlignLeft)

        self.scroll_area.setWidget(self.scroll_widget)
        tab_wall_layout.addWidget(self.scroll_area, 1)

        self.tabs.addTab(tab_wall, "🖥️ BỨC TƯỜNG GAME (MIR4 WALL)")
        main_layout.addWidget(self.tabs, 1)

        # BANNER BẢN QUYỀN
        self.ad_banner = QFrame()
        self.ad_banner.setFixedHeight(82)
        self.ad_banner.setStyleSheet("QFrame { background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #18090a, stop:0.5 #200f12, stop:1 #18090a); border: 2px solid #ff2a2a; border-radius: 8px; }")
        ad_layout = QHBoxLayout(self.ad_banner)
        ad_layout.setContentsMargins(20, 5, 20, 5)

        contact_layout = QVBoxLayout()
        contact_layout.setSpacing(2)
        contact_layout.setAlignment(Qt.AlignVCenter)

        lbl_design = QLabel("Design by : <span style='color: #00e5ff; font-weight: bold;'>PHAMHUNG</span>")
        lbl_design.setStyleSheet("border: none; background: transparent; font-size: 13px; font-weight: bold; color: #ff5252;")
        contact_layout.addWidget(lbl_design)

        lbl_zalo = QLabel("ZALO : <span style='color: #ffe600; font-weight: bold;'>0392771558</span>")
        lbl_zalo.setStyleSheet("border: none; background: transparent; font-size: 13px; font-weight: bold; color: #ff5252;")
        contact_layout.addWidget(lbl_zalo)

        lbl_fb = QLabel("FB : <span style='color: #ffffff; font-weight: bold;'>Phạm Văn Hưng</span>")
        lbl_fb.setStyleSheet("border: none; background: transparent; font-size: 13px; font-weight: bold; color: #ff5252;")
        contact_layout.addWidget(lbl_fb)

        lbl_extra_ad = QLabel("Mua QT + TINKER liên hệ : <span style='color: #00e676; font-weight: bold;'>ZL : 0392771558</span>")
        lbl_extra_ad.setStyleSheet("border: none; background: transparent; font-size: 12px; font-weight: bold; color: #ffea00;")
        contact_layout.addWidget(lbl_extra_ad)

        ad_layout.addLayout(contact_layout)
        ad_layout.addStretch()

        gold_badge = QLabel("BUY / SELL GOLD MIR4")
        gold_badge.setFixedHeight(46)
        gold_badge.setStyleSheet("QLabel { background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #801010, stop:1 #d32f2f); color: #ffeb3b; font-size: 16px; font-weight: 900; padding: 0 25px; border: 2px solid #ffe600; border-radius: 6px; letter-spacing: 1px; }")
        ad_layout.addWidget(gold_badge)
        main_layout.addWidget(self.ad_banner)

    def on_save_and_connect_clicked(self):
        host = self.txt_master_host.text().strip()
        user = self.txt_master_user.text().strip().lower()
        node = self.txt_node_name.text().strip()
        if not host or not user or not node:
            QMessageBox.warning(self, "Thiếu thông tin", "Vui lòng nhập IP Máy Chính, Tài khoản và Tên máy con!")
            return

        self.config["master_host"] = host
        self.config["master_user"] = user
        self.config["node_name"] = node
        self.config["is_connected"] = True
        self.save_settings()
        QMessageBox.information(self, "Đã lưu", f"Đã lưu cấu hình kết nối!\nĐang liên kết tới máy chủ {host}:9999...")

    def on_net_status_change(self, text, color):
        self.lbl_net_status.setText(text)
        self.lbl_net_status.setStyleSheet(f"border: none; font-weight: bold; font-size: 12px; color: {color};")

    def on_update_progress(self, msg):
        self.status_boot_label.setText(msg)
        self.status_update_badge.setText(msg)
        if self.is_updating_mir4:
            self.status_update_badge.setStyleSheet("border: none; font-size: 11px; font-weight: bold; color: #ff9100;")
        else:
            self.status_update_badge.setStyleSheet("border: none; font-size: 11px; font-weight: bold; color: #888;")

    def on_remote_command(self, cmd):
        qt_path = self.txt_qt.text().strip()
        tinker_path = self.txt_tinker.text().strip()

        if cmd == "START_QT":
            if not self.is_updating_mir4 and qt_path and os.path.exists(qt_path):
                self.execute_binary(qt_path)
        elif cmd == "STOP_QT":
            kill_process_by_path(qt_path)
        elif cmd == "START_TINKER":
            if not self.is_updating_mir4 and tinker_path and os.path.exists(tinker_path):
                self.execute_binary(tinker_path)
        elif cmd == "STOP_TINKER":
            kill_process_by_path(tinker_path)
        elif cmd == "REBOOT":
            subprocess.run("shutdown /r /t 2 /f", shell=True)
        elif cmd == "UPDATE_MIR4":
            threading.Thread(target=self.run_remote_update_mir4_process, daemon=True).start()

        QTimer.singleShot(1000, self.check_app_status_ui)

    def locate_screen_image(self, image_path, confidence=0.75):
        try:
            return pyautogui.locateCenterOnScreen(image_path, confidence=confidence)
        except Exception:
            return None

    def click_screen_pos(self, pos, double_click=False):
        pyautogui.moveTo(pos.x, pos.y, duration=0.2)
        time.sleep(0.3)
        if double_click:
            pyautogui.doubleClick(pos.x, pos.y)
        else:
            pyautogui.click(pos.x, pos.y)

    def find_and_click_image(self, image_path, confidence=0.75, timeout=45, double_click=False):
        name = os.path.basename(image_path)
        start_time = time.time()
        while time.time() - start_time < timeout:
            if not self.is_updating_mir4:
                return False
            pos = self.locate_screen_image(image_path, confidence=confidence)
            if pos:
                self.click_screen_pos(pos, double_click=double_click)
                return True
            time.sleep(1.5)
        return False

    def run_remote_update_mir4_process(self):
        if self.is_updating_mir4:
            return

        self.is_updating_mir4 = True
        self.net_signals.update_progress_changed.emit("Đang tắt QT & TINKER để chuẩn bị cập nhật...")

        # 1. Đóng tiến trình QT & TINKER
        kill_process_by_path(self.txt_qt.text().strip())
        kill_process_by_path(self.txt_tinker.text().strip())

        launcher_path = self.txt_launcher.text().strip()
        if not launcher_path or not os.path.exists(launcher_path):
            common_paths = [
                r"C:\Wemade\Mir4Global\Mir4Launcher\Mir4Launcher.exe",
                r"D:\Wemade\Mir4Global\Mir4Launcher\Mir4Launcher.exe",
                r"E:\Wemade\Mir4Global\Mir4Launcher\Mir4Launcher.exe"
            ]
            for cp in common_paths:
                if os.path.exists(cp):
                    launcher_path = cp
                    self.txt_launcher.setText(cp)
                    self.save_settings()
                    break

        if not launcher_path or not os.path.exists(launcher_path):
            self.net_signals.update_progress_changed.emit("LỖI: Chưa chọn đúng đường dẫn Mir4Launcher.exe!")
            self.is_updating_mir4 = False
            return

        try:
            # 2. Thu nhỏ cửa sổ về Desktop
            pyautogui.hotkey("win", "d")
            time.sleep(1.5)

            # 3. Kill toàn bộ cửa sổ Game và Launcher có chữ MIR
            self.net_signals.update_progress_changed.emit("Đang đóng toàn bộ tiến trình game & launcher MIR...")
            kill_all_mir_game_and_launcher()
            time.sleep(2)

            # 4. Mở Launcher MIR4 qua UAC Bypass
            self.net_signals.update_progress_changed.emit("Đang khởi chạy Mir4Launcher (Bypass UAC)...")
            launcher_dir = os.path.dirname(launcher_path)
            ctypes.windll.shell32.ShellExecuteW(None, "runas", launcher_path, "", launcher_dir, 1)

            # Chờ Launcher mở
            for sec in range(45, 0, -5):
                if not self.is_updating_mir4:
                    return
                self.net_signals.update_progress_changed.emit(f"Chờ Launcher khởi động hoàn tất ({sec}s)...")
                time.sleep(5)

            install_img = resource_path("install_btn.png")
            game_start_img = resource_path("game_start_btn.png")
            ingame_img = resource_path("ingame_start_btn.png")
            confirm_img = resource_path("confirm_btn.png")

            # 5. Kiểm tra Install / Game Start
            pos_game_start = self.locate_screen_image(game_start_img, confidence=0.75)
            if not pos_game_start:
                pos_install = self.locate_screen_image(install_img, confidence=0.75)
                if pos_install:
                    self.click_screen_pos(pos_install)
                    time.sleep(5)

            clicked_game = self.find_and_click_image(game_start_img, timeout=120, double_click=True)
            if not clicked_game:
                self.net_signals.update_progress_changed.emit("Cảnh báo: Không click được nút Game Start Launcher!")

            # 6. Chờ vào game
            for sec in range(60, 0, -5):
                if not self.is_updating_mir4:
                    return
                self.net_signals.update_progress_changed.emit(f"Chờ cửa sổ game xuất hiện ({sec}s)...")
                time.sleep(5)

            # 7. Click Start Game in-game
            clicked_start = False
            if os.path.exists(ingame_img):
                clicked_start = self.find_and_click_image(ingame_img, timeout=35)
            if not clicked_start:
                sw, sh = pyautogui.size()
                pyautogui.click(sw // 2, int(sh * 0.67))

            # 8. Chờ 10s hiện bảng xác nhận cập nhật
            time.sleep(10)
            clicked_confirm = False
            if os.path.exists(confirm_img):
                clicked_confirm = self.find_and_click_image(confirm_img, timeout=25)
            if not clicked_confirm:
                sw, sh = pyautogui.size()
                pyautogui.click(int(sw * 0.565), int(sh * 0.670))

            # 9. CHỜ 15 PHÚT TẢI BẢN VÁ GAME
            for remaining in range(900, 0, -5):
                if not self.is_updating_mir4:
                    return
                m, s = divmod(remaining, 60)
                self.net_signals.update_progress_changed.emit(f"Đang tải bản cập nhật MIR4 (Còn {m}p {s}s)...")
                time.sleep(5)

            # 10. Hoàn tất 15 phút: Đóng game & launcher cũ, thu nhỏ màn hình
            self.net_signals.update_progress_changed.emit("Hết 15 phút cập nhật! Đang đóng game và dọn dẹp...")
            kill_all_mir_game_and_launcher()
            time.sleep(1.5)
            pyautogui.hotkey("win", "d")

        except Exception as e:
            self.net_signals.update_progress_changed.emit(f"Lỗi update: {str(e)}")
        finally:
            self.is_updating_mir4 = False
            self.net_signals.update_progress_changed.emit("Đã hoàn tất update. Khôi phục tự động bật QT & TINKER.")
            # 11. Khôi phục tự động chạy lại QT và Tinker
            if self.config.get("auto_start_apps", True):
                QTimer.singleShot(2000, self.auto_start_both_silent)

    def on_fps_slider_changed(self, value):
        if value == 0:
            self.fps_value_label.setText("0 FPS (Tạm dừng)")
            self.render_timer.stop()
        else:
            self.fps_value_label.setText(f"{value} FPS")
            interval = max(16, int(1000 / value))
            self.render_timer.setInterval(interval)
            if not self.render_timer.isActive():
                self.render_timer.start()

    def on_resolution_change(self):
        text = self.res_combo.currentText()
        val = self.res_options[text]
        if val is None:
            self.auto_scale = True
        else:
            self.auto_scale = False
            self.fixed_w, self.fixed_h = val
        self.update_card_dimensions()

    def on_columns_change(self):
        self.current_cols = self.col_combo.currentData()
        self.relayout_cards()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.auto_scale:
            self.update_card_dimensions()

    def update_card_dimensions(self):
        if not self.cards:
            return

        if self.auto_scale:
            viewport_w = self.scroll_area.viewport().width()
            spacing = self.grid_layout.spacing()
            margins = self.grid_layout.contentsMargins().left() + self.grid_layout.contentsMargins().right()
            available_width = viewport_w - margins - ((self.current_cols - 1) * spacing) - 15
            target_w = max(160, int(available_width / self.current_cols))
            target_h = int(target_w * 360 / 640)
        else:
            target_w = self.fixed_w
            target_h = self.fixed_h

        for card in self.cards.values():
            card.set_card_size(target_w, target_h)

    def relayout_cards(self):
        sorted_cards = sorted(self.cards.values(), key=lambda card: card.index)
        for idx, card in enumerate(sorted_cards):
            r = idx // self.current_cols
            c = idx % self.current_cols
            self.grid_layout.addWidget(card, r, c)
        self.update_card_dimensions()

    def scan_windows(self):
        found_windows = []
        pattern = re.compile(r"^mir4g\[(\d+)\]", re.IGNORECASE)

        def enum_cb(hwnd, extra):
            try:
                if win32gui.IsWindowVisible(hwnd):
                    title = win32gui.GetWindowText(hwnd).strip()
                    match = pattern.match(title)
                    if match:
                        idx = int(match.group(1))
                        if 1 <= idx <= 50:
                            found_windows.append((idx, hwnd, title))
            except Exception:
                pass
            return True

        try:
            win32gui.EnumWindows(enum_cb, None)
        except Exception:
            pass

        found_windows.sort(key=lambda x: x[0])
        current_hwnds = {hwnd for _, hwnd, _ in found_windows}

        for hwnd in list(self.cards.keys()):
            if hwnd not in current_hwnds:
                card = self.cards.pop(hwnd)
                card.setParent(None)
                card.deleteLater()

        needs_relayout = False
        for idx, hwnd, title in found_windows:
            if hwnd not in self.cards:
                card = ThumbnailCard(hwnd, title, idx)
                self.cards[hwnd] = card
                needs_relayout = True

        if needs_relayout:
            self.relayout_cards()

        self.status_wall_label.setText(f"Tìm thấy: {len(self.cards)} / 50 cửa sổ")

    def update_frames(self):
        if self.tabs.currentIndex() != 1 or not self.isVisible() or self.isMinimized() or not self.cards:
            return

        scroll_y = self.scroll_area.verticalScrollBar().value()
        viewport_h = self.scroll_area.viewport().height()
        view_top = scroll_y - 50
        view_bottom = scroll_y + viewport_h + 50

        for card in self.cards.values():
            card_top = card.y()
            card_bottom = card_top + card.height()
            if card_bottom >= view_top and card_top <= view_bottom:
                card.refresh_screen()

    def show_alert(self, title, message):
        msg_box = QMessageBox(self)
        msg_box.setWindowTitle(title)
        msg_box.setText(message)
        msg_box.setIcon(QMessageBox.Information)
        msg_box.setStandardButtons(QMessageBox.Ok)
        msg_box.exec_()

    def browse_file(self, target_line_edit):
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Chọn file chương trình", "", "Executable Files (*.exe *.bat *.py);;All Files (*.*)"
        )
        if file_path:
            norm_path = os.path.normpath(file_path)
            target_line_edit.setText(norm_path)
            self.save_settings()
            self.check_app_status_ui()

    def load_config(self):
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.config.update(data)
            except Exception:
                pass

    def save_settings(self):
        try:
            self.config["qt_path"] = self.txt_qt.text().strip()
            self.config["tinker_path"] = self.txt_tinker.text().strip()
            self.config["launcher_path"] = self.txt_launcher.text().strip()
            self.config["auto_start_apps"] = self.chk_auto_launch_tools.isChecked()
            self.config["auto_restart_crash"] = self.chk_auto_restart.isChecked()
            self.config["master_host"] = self.txt_master_host.text().strip()
            self.config["master_user"] = self.txt_master_user.text().strip().lower()
            self.config["node_name"] = self.txt_node_name.text().strip()
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(self.config, f, ensure_ascii=False, indent=4)
        except Exception:
            pass

    def execute_binary(self, path):
        if not path or not os.path.exists(path):
            return False

        abs_path = os.path.abspath(path)
        app_dir = os.path.dirname(abs_path)

        try:
            ret = ctypes.windll.shell32.ShellExecuteW(None, "runas", abs_path, None, app_dir, 1)
            if ret > 32:
                return True
        except Exception:
            pass

        try:
            os.chdir(app_dir)
            os.startfile(abs_path)
            return True
        except Exception:
            pass

        try:
            subprocess.Popen([abs_path], cwd=app_dir, shell=False)
            return True
        except Exception:
            return False

    def on_manual_launch_single(self, path, name):
        self.save_settings()
        if not path or not os.path.exists(path):
            self.show_alert("Lỗi đường dẫn", f"Chưa cấu hình đường dẫn cho {name} hoặc file không tồn tại!")
            return

        if is_process_running_by_path(path):
            self.show_alert("Thông báo", f"{name} đã được khởi động")
            return

        if self.execute_binary(path):
            self.status_boot_label.setText(f"Khởi chạy {name} thành công.")
            self.show_alert("Thông báo", f"Khởi chạy {name} thành công")
            QTimer.singleShot(1500, self.check_app_status_ui)
        else:
            self.show_alert("Lỗi", f"Không thể khởi động {name}. Vui lòng thử lại!")

    def on_manual_launch_both(self):
        self.save_settings()
        qt_path = self.txt_qt.text().strip()
        tinker_path = self.txt_tinker.text().strip()

        if not qt_path and not tinker_path:
            self.show_alert("Thông báo", "Vui lòng chọn đường dẫn cho cả 2 phần mềm trước!")
            return

        results = []

        if qt_path and os.path.exists(qt_path):
            if is_process_running_by_path(qt_path):
                results.append("QT đã được khởi động từ trước.")
            else:
                if self.execute_binary(qt_path):
                    results.append("Khởi chạy QT thành công.")
                else:
                    results.append("Lỗi khởi chạy QT.")
        else:
            results.append("Đường dẫn QT chưa hợp lệ.")

        self.btn_launch_both.setEnabled(False)
        self.status_boot_label.setText("Đã kích hoạt QT. Đang chờ 30 giây để bật TINKER...")

        def run_second_app():
            self.btn_launch_both.setEnabled(True)
            if tinker_path and os.path.exists(tinker_path):
                if is_process_running_by_path(tinker_path):
                    tinker_msg = "TINKER đã được khởi động từ trước."
                else:
                    if self.execute_binary(tinker_path):
                        tinker_msg = "Khởi chạy TINKER thành công sau 30s!"
                    else:
                        tinker_msg = "Lỗi khởi chạy TINKER."
            else:
                tinker_msg = "Đường dẫn TINKER chưa hợp lệ."

            self.status_boot_label.setText(tinker_msg)
            self.show_alert("Thông báo hệ thống", f"Tiến trình hoàn tất:\n{tinker_msg}")
            QTimer.singleShot(1500, self.check_app_status_ui)

        current_status = "\n".join(results)
        self.show_alert(
            "Tiến trình bắt đầu", 
            f"{current_status}\n\n-> Hệ thống sẽ tự động bật TINKER sau đúng 30 giây nữa!"
        )
        QTimer.singleShot(30000, run_second_app)

    def auto_start_both_silent(self):
        if self.is_updating_mir4:
            return

        qt_path = self.txt_qt.text().strip()
        tinker_path = self.txt_tinker.text().strip()

        if qt_path and os.path.exists(qt_path) and not is_process_running_by_path(qt_path):
            self.execute_binary(qt_path)
            self.status_boot_label.setText("Đã bật QT. Đang đếm lùi 30 giây để bật TINKER...")
        else:
            self.status_boot_label.setText("QT đã hoạt động. Đang chờ 30 giây để kiểm tra TINKER...")

        def start_tinker():
            if not self.is_updating_mir4 and tinker_path and os.path.exists(tinker_path) and not is_process_running_by_path(tinker_path):
                self.execute_binary(tinker_path)
                self.status_boot_label.setText("Đã tự động khởi chạy TINKER sau 30 giây!")
            self.check_app_status_ui()

        QTimer.singleShot(30000, start_tinker)

    def check_app_status_ui(self):
        if self.is_updating_mir4:
            self.status_qt_badge.setText("🟠 ĐANG CẬP NHẬT GAME...")
            self.status_qt_badge.setStyleSheet("border: none; font-size: 11px; font-weight: bold; color: #ff9100;")
            self.status_tinker_badge.setText("🟠 ĐANG CẬP NHẬT GAME...")
            self.status_tinker_badge.setStyleSheet("border: none; font-size: 11px; font-weight: bold; color: #ff9100;")
            return "UPDATING", "UPDATING"

        qt_status = get_tool_status_by_path(self.txt_qt.text().strip())
        if qt_status == "ON":
            self.status_qt_badge.setText("🟢 ĐANG CHẠY (ONLINE)")
            self.status_qt_badge.setStyleSheet("border: none; font-size: 11px; font-weight: bold; color: #00e676;")
        elif qt_status == "ĐÃ CHẠY-CHỜ ĐĂNG NHẬP":
            self.status_qt_badge.setText("🟠 ĐÃ CHẠY-CHỜ ĐĂNG NHẬP")
            self.status_qt_badge.setStyleSheet("border: none; font-size: 11px; font-weight: bold; color: #ffb300;")
        else:
            self.status_qt_badge.setText("🔴 ĐÃ DỪNG (OFFLINE)")
            self.status_qt_badge.setStyleSheet("border: none; font-size: 11px; font-weight: bold; color: #ff5252;")

        tinker_status = get_tool_status_by_path(self.txt_tinker.text().strip())
        if tinker_status == "ON":
            self.status_tinker_badge.setText("🟢 ĐANG CHẠY (ONLINE)")
            self.status_tinker_badge.setStyleSheet("border: none; font-size: 11px; font-weight: bold; color: #00e676;")
        elif tinker_status == "ĐÃ CHẠY-CHỜ ĐĂNG NHẬP":
            self.status_tinker_badge.setText("🟠 ĐÃ CHẠY-CHỜ ĐĂNG NHẬP")
            self.status_tinker_badge.setStyleSheet("border: none; font-size: 11px; font-weight: bold; color: #ffb300;")
        else:
            self.status_tinker_badge.setText("🔴 ĐÃ DỪNG (OFFLINE)")
            self.status_tinker_badge.setStyleSheet("border: none; font-size: 11px; font-weight: bold; color: #ff5252;")

        return qt_status, tinker_status

    def check_and_recover_apps(self):
        if self.is_updating_mir4:
            return

        qt_path = self.txt_qt.text().strip()
        tinker_path = self.txt_tinker.text().strip()

        qt_status, tinker_status = self.check_app_status_ui()

        if not self.chk_auto_restart.isChecked():
            return

        if qt_path and os.path.exists(qt_path) and qt_status == "OFF":
            self.status_boot_label.setText("Phát hiện QT bị tắt! Đang tự động bật lại...")
            self.execute_binary(qt_path)

        if tinker_path and os.path.exists(tinker_path) and tinker_status == "OFF":
            self.status_boot_label.setText("Phát hiện TINKER bị tắt! Đang tự động bật lại...")
            self.execute_binary(tinker_path)

    def toggle_startup(self, state):
        enable = (state == Qt.Checked)
        if set_task_scheduler_autostart(enable):
            status = "kích hoạt" if enable else "tắt"
            self.status_boot_label.setText(f"Đã {status} tự khởi động Hub cùng Windows (Bypass UAC).")
            old_bat = os.path.join(os.environ.get("APPDATA", ""), r"Microsoft\Windows\Start Menu\Programs\Startup\AutoGame_Hub.bat")
            if os.path.exists(old_bat):
                try:
                    os.remove(old_bat)
                except Exception:
                    pass
        else:
            self.status_boot_label.setText("Lỗi cấu hình khởi động Task Scheduler!")

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MasterApp()
    window.show()
    sys.exit(app.exec_())
