import os
import sys
import subprocess
import time
import shutil

# Auth Key chính thức
DEFAULT_AUTH_KEY = "tskey-auth-kW2XejLBFX11CNTRL-fc2htAM1fCFixWAK76pPDFP5ivhQ7Rkx"

def get_bundle_dir():
    """Lấy đường dẫn thư mục giải nén tạm thời khi chạy file PyInstaller --onefile"""
    if getattr(sys, 'frozen', False):
        return getattr(sys, '_MEIPASS', os.path.dirname(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))

class EmbeddedTailscale:
    def __init__(self, auth_key=DEFAULT_AUTH_KEY):
        self.auth_key = auth_key
        self.target_dir = os.path.join(os.environ.get("PROGRAMDATA", "C:\\ProgramData"), "Mir4Tailscale")
        self.daemon_exe = os.path.join(self.target_dir, "tailscaled.exe")
        self.cli_exe = os.path.join(self.target_dir, "tailscale.exe")
        self.wintun_dll = os.path.join(self.target_dir, "wintun.dll")
        self.state_file = os.path.join(self.target_dir, "tailscaled.state")

    def extract_binaries(self):
        """Trích xuất 3 file nhúng từ bên trong file EXE ra ProgramData nếu chưa có"""
        os.makedirs(self.target_dir, exist_ok=True)
        src_bin = os.path.join(get_bundle_dir(), "tailscale_bin")

        for fname in ["tailscaled.exe", "tailscale.exe", "wintun.dll"]:
            dst = os.path.join(self.target_dir, fname)
            src = os.path.join(src_bin, fname)
            if not os.path.exists(dst) and os.path.exists(src):
                try:
                    shutil.copy2(src, dst)
                except Exception:
                    pass

    def start_and_connect(self):
        """Khởi động daemon và tự động kết nối vào mạng ảo VPN"""
        self.extract_binaries()
        if not os.path.exists(self.daemon_exe) or not os.path.exists(self.cli_exe):
            return False

        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE

        # Kiểm tra nếu tiến trình daemon chưa chạy thì bật lên
        chk = subprocess.run('tasklist /FI "IMAGENAME eq tailscaled.exe"', shell=True, capture_output=True, text=True)
        if "tailscaled.exe" not in chk.stdout:
            cmd_daemon = f'"{self.daemon_exe}" -state="{self.state_file}"'
            subprocess.Popen(cmd_daemon, shell=True, startupinfo=startupinfo)
            time.sleep(2)

        # Đăng ký tự động qua Auth Key
        cmd_up = f'"{self.cli_exe}" up --authkey="{self.auth_key}" --unattended --reset'
        subprocess.run(cmd_up, shell=True, startupinfo=startupinfo)
        return True

    def get_my_ip(self):
        """Lấy IP ảo dải 100.x.y.z của máy này"""
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE

        cmd = f'"{self.cli_exe}" ip -4'
        for _ in range(12):
            try:
                res = subprocess.run(cmd, shell=True, capture_output=True, text=True, startupinfo=startupinfo, timeout=3)
                ip = res.stdout.strip()
                if ip.startswith("100."):
                    return ip
            except Exception:
                pass
            time.sleep(0.5)
        return ""
