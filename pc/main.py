"""NoticeFloat PC 客户端。

托盘常驻 + Tkinter 主窗口 + WebSocket 长连接。与 Android 端共用后端 API/WS。
"""
from __future__ import annotations

import json
import os
import queue
import sqlite3
import subprocess
import sys
import threading
import time
import tkinter as tk
import uuid
from datetime import datetime, timedelta
from tkinter import messagebox, simpledialog, ttk
from typing import Any, Callable

import requests
from PIL import Image, ImageDraw
import pystray
import websocket  # websocket-client
# v0.8.14：Windows 系统提示音（消息到达时鸣响，避免用户漏看托盘）
try:
    import winsound  # type: ignore
except Exception:
    winsound = None  # type: ignore

# ============================================================
# 配置与存储
# ============================================================

APP_NAME = "勇冠三军提醒器"
APP_VERSION = "0.8.15.2"
APP_VERSION_CODE = 17  # v0.8.15 用于自升级比较
DATA_DIR = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "NoticeFloat")


def _resource_dir() -> str:
    """PyInstaller onefile 打包后 assets 位于 sys._MEIPASS；开发态用脚本同目录。"""
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return base
    return os.path.dirname(os.path.abspath(__file__))


HELP_TEXT = """📖 勇冠三军提醒器 · 使用说明

🖱 PC 端
• Ctrl + 鼠标滚轮 = 全局缩放字体
• 待办 tab 双击行 = 单条已读；顶部「全部已读」= 一键清空
• 发布 tab 选「所有人（广播）」= 发给所有装了本 App 的设备
• 「🔴 强弹窗」= 接收方必须点确认才能关闭
• 托盘图标：红色勇字 = 有新提醒；单击最小化/双击显示

📱 手机端悬浮球
• 点击 = 打开待办面板
• 长按 = 打开发布页并自动开始语音识别
• 拖动 = 移动位置，松手吸左/右边
• 圆球中央显示待办关键字；多条时右上角红角标显示数字

⏰ 提醒时间
• 点「📅 选择日期时间」= 日期+时分选择器
• 手机休眠也会响铃（USE_EXACT_ALARM）

👥 群 / 管理员
• 4 位群号（1万种）方便记忆；「加入群」直接输群号
• 8 位大写字母数字口令码 = 兑换成为管理员（由超管在「🛡️ 管理员管理」tab 生成）
• 普通用户只能给自己设提醒；管理员/超管可发群/广播
"""
os.makedirs(DATA_DIR, exist_ok=True)
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")
# v0.8.14：判断本次启动是否首次运行（未创建过 config）—— 首运主动弹窗，避免用户看不到托盘以为exe没启动
IS_FIRST_RUN = not os.path.exists(CONFIG_PATH)
DB_PATH = os.path.join(DATA_DIR, "notice.db")

# 内置默认后端 URL（cpolar tunnel，24h 变一次；用户可在 config.json 覆盖）
DEFAULT_SERVER_URL = "https://78a8a5e6.r6.cpolar.cn"

# v0.8.15 引导 URL：cpolar 免费版每天变 URL 太麻烦，改用 GitHub raw 存当前 URL。
# 启动时先请求这个固定地址拿到最新 cpolar URL；请求失败则用 config 里的旧值。
# 更新 cpolar URL 只需 push server_url.txt，客户端下次启动自动同步。
BOOTSTRAP_URL = "https://raw.githubusercontent.com/DKaisa/noticefloat/main/server_url.txt"
BOOTSTRAP_URL_JSDELIVR = "https://cdn.jsdelivr.net/gh/DKaisa/noticefloat@main/server_url.txt"


def load_config() -> dict:
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
                if not (cfg.get("server_url") or "").strip():
                    cfg["server_url"] = DEFAULT_SERVER_URL
                    save_config(cfg)
                return cfg
        except Exception:
            pass
    cfg = {
        "server_url": DEFAULT_SERVER_URL,
        "nickname": os.environ.get("USERNAME", "PC 用户"),
        "device_id": f"pc-{uuid.uuid4().hex[:16]}",
    }
    save_config(cfg)
    return cfg


def save_config(cfg: dict) -> None:
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def _bootstrap_server_url(cfg: dict) -> None:
    """v0.8.15 启动时从 GitHub raw 拉取最新的 cpolar URL 并覆盖 config。
    先试 jsdelivr（国内 CDN 更快），失败再试 raw.githubusercontent.com。
    失败则保持原 config 不变；成功且 URL 有变化才写回磁盘。
    v0.8.15.1 加域名白名单：只接受 *.cpolar.cn / *.cpolar.io / localhost /
      127.0.0.1，防止 server_url.txt 被篡改劫持客户端到恶意后端。"""
    import requests as _req
    from urllib.parse import urlparse as _up
    ALLOWED_SUFFIX = (".cpolar.cn", ".cpolar.io", ".cpolar.top")
    ALLOWED_HOST = ("localhost", "127.0.0.1")
    for url in (BOOTSTRAP_URL_JSDELIVR, BOOTSTRAP_URL):
        try:
            r = _req.get(url, timeout=5)
            if r.status_code != 200:
                continue
            new_url = (r.text or "").strip()
            if not new_url or "\n" in new_url:
                new_url = new_url.splitlines()[0].strip() if new_url else ""
            if not new_url.startswith(("http://", "https://")):
                continue
            # 域名白名单校验
            try:
                host = (_up(new_url).hostname or "").lower()
            except Exception:
                continue
            if not (host in ALLOWED_HOST or any(host.endswith(s) for s in ALLOWED_SUFFIX)):
                print(f"[bootstrap] rejected untrusted host: {host}")
                continue
            if new_url != (cfg.get("server_url") or "").strip():
                print(f"[bootstrap] server_url updated: {cfg.get('server_url')} -> {new_url}")
                cfg["server_url"] = new_url
                try:
                    save_config(cfg)
                except Exception:
                    pass
            return
        except Exception:
            continue


CONFIG = load_config()
# 启动时静默尝试从 GitHub raw 拉一次最新 URL（不阻塞：超时 5s，失败保持原值）
try:
    _bootstrap_server_url(CONFIG)
except Exception:
    pass


# ============================================================
# 本地 SQLite（缓存待办）
# ============================================================

def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _db() as c:
        c.execute(
            """CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                server_id INTEGER,
                title TEXT NOT NULL,
                content TEXT DEFAULT '',
                remind_at INTEGER DEFAULT 0,
                created_at INTEGER NOT NULL,
                status INTEGER DEFAULT 0,
                publisher TEXT DEFAULT '我',
                source TEXT DEFAULT 'local',
                group_code TEXT,
                urgent INTEGER DEFAULT 0
            )"""
        )
        c.execute("CREATE INDEX IF NOT EXISTS idx_server ON tasks(server_id)")


def add_local_task(title: str, content: str, remind_at: int, urgent: bool) -> int:
    with _db() as c:
        cur = c.execute(
            "INSERT INTO tasks(title,content,remind_at,created_at,urgent) VALUES(?,?,?,?,?)",
            (title, content, remind_at, int(time.time() * 1000), int(urgent)),
        )
        return cur.lastrowid


def upsert_group_task(server_id: int, title: str, content: str, remind_at: int,
                      publisher: str, group_code: str, urgent: bool) -> int:
    with _db() as c:
        row = c.execute("SELECT id,status FROM tasks WHERE server_id=?", (server_id,)).fetchone()
        if row:
            return row["id"]
        cur = c.execute(
            """INSERT INTO tasks(server_id,title,content,remind_at,created_at,status,publisher,source,group_code,urgent)
               VALUES(?,?,?,?,?,0,?,?,?,?)""",
            (server_id, title, content, remind_at, int(time.time() * 1000),
             publisher, "group", group_code, int(urgent)),
        )
        return cur.lastrowid


def list_pending() -> list[sqlite3.Row]:
    with _db() as c:
        return list(c.execute(
            "SELECT * FROM tasks WHERE status=0 ORDER BY (remind_at=0), remind_at ASC, id DESC"
        ))


def list_done(limit: int = 200) -> list[sqlite3.Row]:
    with _db() as c:
        return list(c.execute(
            "SELECT * FROM tasks WHERE status=1 ORDER BY id DESC LIMIT ?", (limit,)
        ))


def delete_all_done() -> int:
    with _db() as c:
        cur = c.execute("DELETE FROM tasks WHERE status=1")
        return cur.rowcount


def mark_done(task_id: int) -> sqlite3.Row | None:
    with _db() as c:
        row = c.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row:
            c.execute("UPDATE tasks SET status=1 WHERE id=?", (task_id,))
        return row


def mark_done_by_server(server_id: int) -> None:
    with _db() as c:
        c.execute("UPDATE tasks SET status=1 WHERE server_id=?", (server_id,))


def delete_task(task_id: int) -> None:
    with _db() as c:
        c.execute("DELETE FROM tasks WHERE id=?", (task_id,))


init_db()


# ============================================================
# 预置周期性提醒（本地 scheduler，纯 PC 侧，不占后端）
# ============================================================
# 每条：(名字, 描述文案, weekday 集合[Mon=0..Sun=6], 时, 分, 弹出内容)
SCHEDULED_REMINDERS: list[tuple[str, str, set[int], int, int, str]] = [
    ("竞技场", "每晚 21:50", {0, 1, 2, 3, 4, 5, 6}, 21, 50, "🏆 竞技场提醒：该打竞技场了"),
    ("盐场",   "每周六 19:50", {5},                   19, 50, "🧂 盐场提醒：周六盐场开始"),
    ("蟠桃/躺平", "每周日 19:50", {6},                19, 50, "🍑 周日蟠桃 / 躺平吹逼时间"),
    ("赛车",   "周一~周三 08:00", {0, 1, 2},         8,  0,  "🏎 早间赛车提醒"),
    # 咸鱼神杯早晚竞猜提醒已迁移至后端 recurring_tasks（周期公告 tab 可见），此处不再重复
]

_scheduler_triggered_today: set[str] = set()
_scheduler_date_key: str = ""
_scheduler_on_trigger: Callable[[], None] | None = None


def _scheduler_loop() -> None:
    global _scheduler_triggered_today, _scheduler_date_key
    while True:
        try:
            now = datetime.now()
            today = now.strftime("%Y-%m-%d")
            if today != _scheduler_date_key:
                _scheduler_triggered_today = set()
                _scheduler_date_key = today
            wd = now.weekday()
            for name, desc, weekdays, hh, mm, msg in SCHEDULED_REMINDERS:
                if wd not in weekdays:
                    continue
                if now.hour != hh or now.minute != mm:
                    continue
                key = f"{today}|{name}"
                if key in _scheduler_triggered_today:
                    continue
                _scheduler_triggered_today.add(key)
                add_local_task(msg, desc, int(now.timestamp() * 1000), False)
                if _scheduler_on_trigger:
                    try:
                        _scheduler_on_trigger()
                    except Exception:
                        pass
        except Exception:
            pass
        time.sleep(30)


def start_scheduler(on_trigger: Callable[[], None] | None = None) -> None:
    """启动预置提醒后台线程。on_trigger 每次到点触发后被调用（用于 UI 刷新）。"""
    global _scheduler_on_trigger
    _scheduler_on_trigger = on_trigger
    threading.Thread(target=_scheduler_loop, daemon=True).start()


# ============================================================
# REST API 封装
# ============================================================

class Api:
    def __init__(self, cfg: dict) -> None:
        self.cfg = cfg

    @property
    def base(self) -> str:
        return (self.cfg.get("server_url") or "").rstrip("/")

    def _url(self, path: str) -> str:
        return f"{self.base}{path}"

    def health(self) -> bool:
        try:
            r = requests.get(self._url("/api/health"), timeout=3)
            return r.ok
        except Exception:
            return False

    def create_group(self, name: str) -> dict:
        r = requests.post(self._url("/api/groups"), json={
            "group_name": name,
            "device_id": self.cfg["device_id"],
            "nickname": self.cfg["nickname"],
        }, timeout=6)
        r.raise_for_status()
        return r.json()

    def join_group(self, code: str) -> dict:
        r = requests.post(self._url(f"/api/groups/{code}/join"), json={
            "device_id": self.cfg["device_id"],
            "nickname": self.cfg["nickname"],
        }, timeout=6)
        r.raise_for_status()
        return r.json()

    def my_groups(self) -> list[dict]:
        r = requests.get(self._url(f"/api/devices/{self.cfg['device_id']}/groups"), timeout=6)
        r.raise_for_status()
        return r.json().get("groups", [])

    def discover_groups(self) -> dict:
        """v0.8.15 发现全部群（含未加入的）"""
        r = requests.get(self._url(f"/api/groups/discover?device_id={self.cfg['device_id']}"), timeout=6)
        r.raise_for_status()
        return r.json()

    def publish(self, code: str, title: str, content: str, remind_at: int, urgent: bool) -> dict:
        r = requests.post(self._url(f"/api/groups/{code}/tasks"), json={
            "device_id": self.cfg["device_id"],
            "title": title,
            "content": content,
            "remind_at": remind_at,
            "urgent": urgent,
        }, timeout=6)
        r.raise_for_status()
        return r.json()

    def publish_broadcast(self, title: str, content: str, remind_at: int, urgent: bool) -> dict:
        """v0.8.3 广播：发给所有 device"""
        r = requests.post(self._url("/api/broadcast"), json={
            "device_id": self.cfg["device_id"],
            "nickname": self.cfg.get("nickname", "系统广播"),
            "title": title,
            "content": content,
            "remind_at": remind_at,
            "urgent": urgent,
        }, timeout=6)
        r.raise_for_status()
        return r.json()

    def mark_done(self, server_id: int) -> None:
        requests.post(self._url(f"/api/tasks/{server_id}/done"), json={
            "device_id": self.cfg["device_id"],
        }, timeout=6)

    def pull_unread(self, since_ms: int) -> list[dict]:
        """v0.8.14 亮屏/重连时补拉未读，避免 WS 断连期间丢消息"""
        try:
            r = requests.get(
                self._url(f"/api/devices/{self.cfg['device_id']}/unread"),
                params={"since": since_ms}, timeout=8,
            )
            r.raise_for_status()
            return r.json().get("tasks", [])
        except Exception:
            return []

    def check_pc_update(self) -> dict:
        """v0.8.15 查询后端最新 PC 版本"""
        try:
            r = requests.get(self._url("/api/latest_pc"), timeout=6)
            r.raise_for_status()
            return r.json()
        except Exception:
            return {}

    def download_file(self, url_path: str, dest_path: str, on_progress=None) -> bool:
        """v0.8.15 分片下载新 exe 到本地路径（timeout 拉长以适配 cpolar 1Mbps 带宽）。
        on_progress(downloaded_bytes, total_bytes): 每收到一片就回调，用于进度条。"""
        try:
            from urllib.parse import quote
            # url_path 可能含中文，requests 通常会 encode，但为保险手动 quote 一遍
            base = self._url("")
            # url_path 形如 /downloads/勇冠三军...exe
            safe_path = "/" + "/".join(quote(seg, safe="") for seg in url_path.lstrip("/").split("/"))
            full = base.rstrip("/") + safe_path
            with requests.get(full, stream=True, timeout=(10, 600)) as r:
                r.raise_for_status()
                total = int(r.headers.get("Content-Length") or 0)
                written = 0
                with open(dest_path, "wb") as f:
                    for chunk in r.iter_content(chunk_size=65536):
                        if not chunk:
                            continue
                        f.write(chunk)
                        written += len(chunk)
                        if on_progress:
                            try:
                                on_progress(written, total)
                            except Exception:
                                pass
            return True
        except Exception:
            return False

    # -------- 角色 / 管理员 --------
    def get_role(self) -> dict:
        try:
            r = requests.get(self._url(f"/api/role/{self.cfg['device_id']}"), timeout=6)
            r.raise_for_status()
            return r.json()
        except Exception:
            return {"role": "user", "expires_at": None, "is_super": False}

    def admin_create_code(self, expires_days: int, note: str) -> dict:
        r = requests.post(self._url("/api/admin/codes"), json={
            "device_id": self.cfg["device_id"],
            "expires_days": expires_days,      # v0.8.10：改为天单位
            "note": note,
        }, timeout=6)
        r.raise_for_status()
        return r.json()

    def admin_list_codes(self) -> list[dict]:
        r = requests.get(self._url("/api/admin/codes"),
                         params={"device_id": self.cfg["device_id"]}, timeout=6)
        r.raise_for_status()
        return r.json().get("codes", [])

    def admin_delete_code(self, code: str) -> None:
        requests.delete(self._url(f"/api/admin/codes/{code}"),
                        params={"device_id": self.cfg["device_id"]}, timeout=6)

    def admin_list_admins(self) -> list[dict]:
        r = requests.get(self._url("/api/admin/admins"),
                         params={"device_id": self.cfg["device_id"]}, timeout=6)
        r.raise_for_status()
        return r.json().get("admins", [])

    def admin_revoke(self, admin_device_id: str) -> None:
        requests.delete(self._url(f"/api/admin/admins/{admin_device_id}"),
                        params={"device_id": self.cfg["device_id"]}, timeout=6)

    def admin_stats(self) -> dict:
        """v0.8.11 后台统计（仅超管可查）"""
        r = requests.get(self._url("/api/admin/stats"),
                         params={"device_id": self.cfg["device_id"]}, timeout=6)
        r.raise_for_status()
        return r.json()

    def admin_users(self) -> list[dict]:
        """v0.8.12 所有用户列表（仅超管可查）"""
        r = requests.get(self._url("/api/admin/users"),
                         params={"device_id": self.cfg["device_id"]}, timeout=6)
        r.raise_for_status()
        return r.json().get("users", [])

    # -------- 周期性公告 --------
    def recurring_create(self, payload: dict) -> dict:
        r = requests.post(self._url("/api/recurring"), json=payload, timeout=6)
        r.raise_for_status()
        return r.json()

    def recurring_list(self) -> list[dict]:
        r = requests.get(self._url("/api/recurring"),
                         params={"device_id": self.cfg["device_id"]}, timeout=6)
        r.raise_for_status()
        return r.json().get("items", [])

    def recurring_toggle(self, rid: int, active: bool) -> None:
        requests.patch(self._url(f"/api/recurring/{rid}"),
                       json={"device_id": self.cfg["device_id"], "active": active}, timeout=6)

    def recurring_delete(self, rid: int) -> None:
        requests.delete(self._url(f"/api/recurring/{rid}"),
                        params={"device_id": self.cfg["device_id"]}, timeout=6)


API = Api(CONFIG)


# ============================================================
# WebSocket 客户端（后台线程 + 断线重连）
# ============================================================

class WsWorker:
    def __init__(self, on_event: Callable[[str, dict], None]) -> None:
        self.on_event = on_event
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._ws: websocket.WebSocketApp | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="ws", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        try:
            if self._ws:
                self._ws.close()
        except Exception:
            pass

    def _url(self) -> str | None:
        base = (CONFIG.get("server_url") or "").rstrip("/")
        if not base:
            return None
        ws_base = base.replace("http://", "ws://").replace("https://", "wss://")
        return f"{ws_base}/ws/{CONFIG['device_id']}"

    def _run(self) -> None:
        backoff = 2
        while not self._stop.is_set():
            url = self._url()
            if not url:
                time.sleep(3)
                continue
            try:
                def on_open(ws):
                    self.on_event("_open", {})

                def on_message(ws, msg):
                    try:
                        data = json.loads(msg)
                    except Exception:
                        return
                    self.on_event(data.get("type", ""), data)

                def on_error(ws, err):
                    pass

                def on_close(ws, *_):
                    self.on_event("_close", {})

                self._ws = websocket.WebSocketApp(
                    url, on_open=on_open, on_message=on_message,
                    on_error=on_error, on_close=on_close,
                )
                self._ws.run_forever(ping_interval=20, ping_timeout=10)
            except Exception:
                pass
            if self._stop.is_set():
                break
            time.sleep(backoff)
            backoff = min(backoff * 2, 30)


# ============================================================
# UI 事件桥（后台线程 -> Tk 主线程）
# ============================================================

UI_QUEUE: "queue.Queue[Callable[[], None]]" = queue.Queue()


def run_on_ui(func: Callable[[], None]) -> None:
    UI_QUEUE.put(func)


# ============================================================
# 托盘图标
# ============================================================

def _make_tray_icon(alert: bool) -> Image.Image:
    """加载勇字托盘图标。找不到时回退到简单画法。"""
    # v0.8.14：修复 PyInstaller onefile 打包后 __file__ 不指向 assets 的问题
    base = os.path.join(_resource_dir(), "assets")
    fn = "tray_alert.png" if alert else "tray_normal.png"
    path = os.path.join(base, fn)
    if os.path.exists(path):
        try:
            return Image.open(path).convert("RGBA")
        except Exception:
            pass
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    color = (255, 59, 48, 255) if alert else (220, 38, 38, 255)
    d.ellipse((4, 4, 60, 60), fill=color)
    d.text((22, 20), "勇", fill=(255, 255, 255, 255))
    return img


class Tray:
    def __init__(self, on_show: Callable[[], None], on_quit: Callable[[], None]) -> None:
        self._on_show = on_show
        self._on_quit = on_quit
        self._pending = 0
        self.icon = pystray.Icon(
            APP_NAME,
            _make_tray_icon(False),
            APP_NAME,
            menu=pystray.Menu(
                pystray.MenuItem("显示主窗口", self._show, default=True),
                pystray.MenuItem("退出", self._quit),
            ),
        )

    def _show(self, icon, item):
        run_on_ui(self._on_show)

    def _quit(self, icon, item):
        icon.stop()
        run_on_ui(self._on_quit)

    def set_pending(self, n: int) -> None:
        if n == self._pending:
            return
        self._pending = n
        self.icon.icon = _make_tray_icon(n > 0)
        self.icon.title = f"{APP_NAME} · 未读 {n}" if n > 0 else APP_NAME

    def run_detached(self) -> None:
        t = threading.Thread(target=self.icon.run, name="tray", daemon=True)
        t.start()


# ============================================================
# 强弹窗
# ============================================================

def show_urgent_popup(root: tk.Tk, task_row: sqlite3.Row, on_ack: Callable[[], None]) -> None:
    win = tk.Toplevel(root)
    win.title("🔴 紧急通知")
    win.configure(bg="#111827")
    win.attributes("-topmost", True)
    win.geometry("460x300")
    try:
        win.attributes("-alpha", 0.98)
    except Exception:
        pass

    # 居中
    win.update_idletasks()
    sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
    x = (sw - 460) // 2
    y = (sh - 300) // 2
    win.geometry(f"460x300+{x}+{y}")

    tag = tk.Label(win, text="🔴 紧急通知", fg="#FF3B30", bg="#111827",
                   font=("Microsoft YaHei", 10, "bold"))
    tag.pack(anchor="w", padx=20, pady=(18, 6))

    tk.Label(win, text=task_row["title"], fg="white", bg="#111827",
             font=("Microsoft YaHei", 15, "bold"), wraplength=420, justify="left"
             ).pack(anchor="w", padx=20)

    content = task_row["content"] or "（无正文）"
    tk.Label(win, text=content, fg="#E5E7EB", bg="#111827",
             font=("Microsoft YaHei", 11), wraplength=420, justify="left"
             ).pack(anchor="w", padx=20, pady=(10, 0))

    remind = task_row["remind_at"]
    time_str = datetime.fromtimestamp(remind / 1000).strftime("%m-%d %H:%M") if remind else "无提醒时间"
    meta = f"来自 {task_row['publisher']} · {time_str}"
    tk.Label(win, text=meta, fg="#9CA3AF", bg="#111827",
             font=("Microsoft YaHei", 9)).pack(anchor="w", padx=20, pady=(12, 0))

    btns = tk.Frame(win, bg="#111827")
    btns.pack(side="bottom", fill="x", padx=20, pady=16)

    def close_only():
        win.destroy()

    def ack():
        try:
            on_ack()
        finally:
            win.destroy()

    tk.Button(btns, text="稍后处理", command=close_only, width=12).pack(side="left")
    tk.Button(btns, text="✓ 我已知晓", command=ack, width=14,
              bg="#4C6FFF", fg="white", relief="flat"
              ).pack(side="right")

    win.protocol("WM_DELETE_WINDOW", close_only)
    win.after(200, lambda: (win.lift(), win.focus_force()))


# ============================================================
# 主窗口
# ============================================================

class MainWindow:
    def __init__(self, root: tk.Tk, tray: Tray, ws: WsWorker) -> None:
        self.root = root
        self.tray = tray
        self.ws = ws
        self.groups_cache: list[dict] = []

        root.title(f"{APP_NAME} v{APP_VERSION}")
        root.geometry("620x520")
        root.protocol("WM_DELETE_WINDOW", self.hide)

        style = ttk.Style()
        try:
            style.theme_use("vista")
        except Exception:
            pass

        nb = ttk.Notebook(root)
        nb.pack(fill="both", expand=True, padx=8, pady=8)
        self.nb = nb

        self.tab_tasks = ttk.Frame(nb)
        self.tab_publish = ttk.Frame(nb)
        self.tab_groups = ttk.Frame(nb)
        self.tab_history = ttk.Frame(nb)
        self.tab_settings = ttk.Frame(nb)
        self.tab_help = ttk.Frame(nb)
        self.tab_admin = ttk.Frame(nb)          # 超管专属
        self.tab_recurring = ttk.Frame(nb)      # 超管+管理员可见
        nb.add(self.tab_tasks, text="待办")
        nb.add(self.tab_publish, text="发布")
        nb.add(self.tab_groups, text="群")
        nb.add(self.tab_history, text="历史")
        nb.add(self.tab_settings, text="设置")
        nb.add(self.tab_help, text="📖 说明")

        # 角色状态
        self.role: str = "user"
        self.is_super: bool = False
        self.role_expires_at: Optional[int] = None
        self._admin_tab_added: bool = False
        self._recurring_tab_added: bool = False

        self._build_tasks()
        self._build_publish()
        self._build_groups()
        self._build_history()
        self._build_settings()
        self._build_help()
        self._build_admin()
        self._build_recurring()

        self.status_var = tk.StringVar(value="未连接")
        tk.Label(root, textvariable=self.status_var, anchor="w",
                 fg="#6B7280").pack(fill="x", side="bottom", padx=10, pady=2)

        self.refresh_tasks()
        self.refresh_groups()
        self._install_zoom_bindings()
        # 启动后异步查角色（避免阻塞 UI）
        self.root.after(500, self.refresh_role)
        # tab 切换自动刷新对应数据
        self.nb.bind("<<NotebookTabChanged>>", self._on_tab_changed)

    def _on_tab_changed(self, event=None) -> None:
        try:
            cur = self.nb.select()
            if not cur: return
            widget = self.nb.nametowidget(cur)
        except Exception:
            return
        if widget is self.tab_groups:
            self.refresh_groups()
        elif widget is self.tab_tasks:
            self.refresh_tasks()
        elif widget is self.tab_history and hasattr(self, "refresh_history"):
            self.refresh_history()
        elif getattr(self, "_admin_tab_added", False) and widget is self.tab_admin:
            self.refresh_admin()
        elif getattr(self, "_recurring_tab_added", False) and widget is self.tab_recurring:
            self.refresh_recurring()

    def _install_zoom_bindings(self) -> None:
        """Ctrl+滚轮缩放：调整全局字体大小，所有 tk/ttk 控件跟随。"""
        from tkinter import font as tkfont
        self._zoom_fonts = [
            tkfont.nametofont(n) for n in
            ("TkDefaultFont", "TkTextFont", "TkFixedFont",
             "TkMenuFont", "TkHeadingFont", "TkTooltipFont", "TkIconFont")
            if n in tkfont.names()
        ]

        def zoom(delta: int) -> None:
            step = 1 if delta > 0 else -1
            for f in self._zoom_fonts:
                cur = f.actual("size")
                # 部分平台返回负值表示像素；统一转正
                base = abs(cur) if cur else 10
                new = max(8, min(28, base + step))
                f.configure(size=new)

        def on_wheel(e):
            if e.state & 0x0004:  # Ctrl
                zoom(e.delta)
                return "break"

        self.root.bind_all("<Control-MouseWheel>", on_wheel)
        # 兼容 Linux（后续需要时）
        self.root.bind_all("<Control-Button-4>", lambda e: zoom(120))
        self.root.bind_all("<Control-Button-5>", lambda e: zoom(-120))

    # -------- 待办 --------
    def _build_tasks(self) -> None:
        f = self.tab_tasks
        top = tk.Frame(f)
        top.pack(fill="x", padx=4, pady=4)
        tk.Button(top, text="全部已读", command=self._act_done_all,
                  bg="#4C6FFF", fg="white", relief="flat", padx=12
                  ).pack(side="left")
        tk.Label(top, text="  ← 双击行 = 单条完成",
                 fg="#6B7280", font=("Microsoft YaHei", 9)).pack(side="left")

        cols = ("title", "publisher", "time", "src")
        self.tv_tasks = ttk.Treeview(f, columns=cols, show="headings", selectmode="browse")
        self.tv_tasks.heading("title", text="标题")
        self.tv_tasks.heading("publisher", text="来源")
        self.tv_tasks.heading("time", text="提醒时间")
        self.tv_tasks.heading("src", text="类型")
        self.tv_tasks.column("title", width=260)
        self.tv_tasks.column("publisher", width=90)
        self.tv_tasks.column("time", width=120)
        self.tv_tasks.column("src", width=60, anchor="center")
        self.tv_tasks.pack(fill="both", expand=True, padx=4, pady=4)
        # 双击直接标记已完成（单条）
        self.tv_tasks.bind("<Double-1>", lambda e: self._act_done())

    def refresh_tasks(self) -> None:
        for iid in self.tv_tasks.get_children():
            self.tv_tasks.delete(iid)
        for row in list_pending():
            t = row["remind_at"]
            tstr = datetime.fromtimestamp(t / 1000).strftime("%m-%d %H:%M") if t else "-"
            src = "群" if row["source"] == "group" else "本地"
            title = ("🔴 " if row["urgent"] else "") + row["title"]
            self.tv_tasks.insert("", "end", iid=str(row["id"]),
                                 values=(title, row["publisher"], tstr, src))
        pending = len(self.tv_tasks.get_children())
        self.tray.set_pending(pending)

    def _selected_task_id(self) -> int | None:
        sel = self.tv_tasks.selection()
        if not sel:
            return None
        return int(sel[0])

    def _act_done(self) -> None:
        tid = self._selected_task_id()
        if not tid:
            return
        row = mark_done(tid)
        if row and row["source"] == "group" and row["server_id"]:
            try:
                API.mark_done(int(row["server_id"]))
            except Exception:
                pass
        self.refresh_tasks()
        self.refresh_history()

    def _act_done_all(self) -> None:
        """全部已读：批量标记所有待办为已完成。"""
        ids = list(self.tv_tasks.get_children())
        if not ids:
            self.set_status("无待办")
            return
        for iid in ids:
            row = mark_done(int(iid))
            if row and row["source"] == "group" and row["server_id"]:
                try:
                    API.mark_done(int(row["server_id"]))
                except Exception:
                    pass
        self.refresh_tasks()
        self.refresh_history()
        self.set_status(f"✔ 已标记 {len(ids)} 条为已读")

    # -------- 发布 --------
    def _build_publish(self) -> None:
        f = self.tab_publish
        tk.Label(f, text="要发送的消息").grid(row=0, column=0, sticky="w", padx=8, pady=(10, 2))
        self.txt_content = tk.Text(f, height=6)
        self.txt_content.grid(row=1, column=0, columnspan=3, sticky="ew", padx=8)

        # 保留隐藏的 title 控件以兼容旧代码路径
        self.ent_title = tk.Entry(f)  # 不布局 = 隐藏

        tk.Label(f, text="发送到").grid(row=2, column=0, sticky="w", padx=8, pady=(10, 2))
        self.cbo_target = ttk.Combobox(f, state="readonly")
        self.cbo_target.grid(row=3, column=0, columnspan=3, sticky="ew", padx=8)

        self.var_urgent = tk.BooleanVar()
        tk.Checkbutton(f, text="🔴 强弹窗（收到后必须点确认才能关）",
                       variable=self.var_urgent).grid(row=4, column=0, columnspan=3,
                                                       sticky="w", padx=6, pady=(10, 4))

        tk.Label(f, text="提醒时间").grid(row=5, column=0, sticky="w", padx=8, pady=(6, 2))
        self.var_remind = tk.StringVar(value="未设置")
        tk.Label(f, textvariable=self.var_remind, fg="#4C6FFF").grid(row=6, column=0,
                                                                       sticky="w", padx=8)

        quick = tk.Frame(f)
        quick.grid(row=7, column=0, columnspan=3, sticky="w", padx=6, pady=6)
        tk.Button(quick, text="选择日期时间…", width=16,
                  command=self._pick_datetime).pack(side="left", padx=2)
        tk.Button(quick, text="清除", width=6,
                  command=self._clear_remind).pack(side="left", padx=2)

        tk.Button(f, text="发布", command=self._act_publish,
                  bg="#4C6FFF", fg="white", relief="flat", height=2
                  ).grid(row=8, column=0, columnspan=3, sticky="ew", padx=8, pady=12)

        f.columnconfigure(0, weight=1)
        self._remind_at = 0
        self._reload_publish_targets()

    def _reload_publish_targets(self) -> None:
        # 普通用户：只能"仅自己"（保留发提醒给自己的能力）
        if getattr(self, "role", "user") == "user":
            labels = ["仅自己（仅本机提醒，不推送他人）"]
            self.cbo_target["values"] = labels
            self.cbo_target.current(0)
            return
        # 管理员/超管：广播 + 群 + 仅自己
        labels = ["📢 所有人（广播）"]
        for g in self.groups_cache:
            labels.append(f"群「{g['name']}」 #{g['code']} · {g.get('member_count', 0)}人")
        labels.append("仅自己（不推送给他人）")
        self.cbo_target["values"] = labels
        # 默认选中第一项 = 广播
        self.cbo_target.current(0)

    def _quick_remind(self, minutes: int) -> None:
        self._remind_at = int((time.time() + minutes * 60) * 1000)
        self.var_remind.set(datetime.fromtimestamp(self._remind_at / 1000).strftime("%Y-%m-%d %H:%M"))

    def _quick_tomorrow(self) -> None:
        t = datetime.now().replace(hour=9, minute=0, second=0, microsecond=0) + timedelta(days=1)
        self._remind_at = int(t.timestamp() * 1000)
        self.var_remind.set(t.strftime("%Y-%m-%d %H:%M"))

    def _apply_relative(self) -> None:
        raw = self.ent_rel_hours.get().strip()
        try:
            hours = float(raw)
        except ValueError:
            messagebox.showwarning("提示", "请输入数字（例：7.5）", parent=self.root)
            return
        if hours <= 0:
            messagebox.showwarning("提示", "小时数必须 > 0", parent=self.root)
            return
        self._remind_at = int((time.time() + hours * 3600) * 1000)
        self.var_remind.set(datetime.fromtimestamp(self._remind_at / 1000).strftime("%Y-%m-%d %H:%M"))
        self.ent_rel_hours.delete(0, "end")

    def _clear_remind(self) -> None:
        self._remind_at = 0
        self.var_remind.set("未设置")

    def _pick_datetime(self) -> None:
        """日期+时分选择器（Toplevel + 5 Spinbox），无外部依赖。"""
        now = datetime.now()
        top = tk.Toplevel(self.root)
        top.title("选择日期时间")
        top.transient(self.root); top.grab_set()
        top.geometry("+%d+%d" % (self.root.winfo_rootx()+120, self.root.winfo_rooty()+120))

        frm = tk.Frame(top, padx=14, pady=12); frm.pack()
        def spin(parent, frm_, from_, to_, init, w=4):
            v = tk.StringVar(value=str(init).zfill(2 if to_ < 100 else 4))
            sb = tk.Spinbox(frm_, from_=from_, to=to_, width=w, textvariable=v,
                            format="%02.0f" if to_ < 100 else "%04.0f", wrap=True)
            sb.pack(side="left", padx=2)
            return v
        tk.Label(frm, text="年").pack(side="left")
        v_y = spin(top, frm, 2020, 2099, now.year, w=6)
        tk.Label(frm, text="月").pack(side="left")
        v_m = spin(top, frm, 1, 12, now.month)
        tk.Label(frm, text="日").pack(side="left")
        v_d = spin(top, frm, 1, 31, now.day)
        tk.Label(frm, text="  时").pack(side="left")
        v_h = spin(top, frm, 0, 23, now.hour)
        tk.Label(frm, text="分").pack(side="left")
        v_min = spin(top, frm, 0, 59, now.minute)

        # 快速填入按钮
        quick = tk.Frame(top); quick.pack(padx=14, pady=(2, 8))
        def set_quick(dt: datetime):
            v_y.set(f"{dt.year:04d}"); v_m.set(f"{dt.month:02d}")
            v_d.set(f"{dt.day:02d}"); v_h.set(f"{dt.hour:02d}"); v_min.set(f"{dt.minute:02d}")
        for lbl, delta_min in [("+30分", 30), ("+1时", 60), ("+3时", 180)]:
            tk.Button(quick, text=lbl, width=6,
                      command=lambda m=delta_min: set_quick(datetime.now()+timedelta(minutes=m))
                      ).pack(side="left", padx=2)
        tk.Button(quick, text="明早9点", width=8,
                  command=lambda: set_quick((datetime.now()+timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0))
                  ).pack(side="left", padx=2)

        result = {"dt": None}
        def ok():
            try:
                dt = datetime(int(v_y.get()), int(v_m.get()), int(v_d.get()),
                              int(v_h.get()), int(v_min.get()))
            except ValueError as e:
                messagebox.showerror("日期无效", str(e), parent=top); return
            result["dt"] = dt; top.destroy()
        def cancel():
            top.destroy()
        btns = tk.Frame(top); btns.pack(pady=(2, 12))
        tk.Button(btns, text="确定", width=10, command=ok,
                  bg="#4C6FFF", fg="white", relief="flat"
                  ).pack(side="left", padx=6)
        tk.Button(btns, text="取消", width=8, command=cancel).pack(side="left", padx=6)

        self.root.wait_window(top)
        dt = result["dt"]
        if not dt:
            return
        self._remind_at = int(dt.timestamp() * 1000)
        self.var_remind.set(dt.strftime("%Y-%m-%d %H:%M"))

    def _act_publish(self) -> None:
        content = self.txt_content.get("1.0", "end").strip()
        if not content:
            messagebox.showwarning("提示", "请填写消息内容", parent=self.root)
            return
        # 后端 API 仍需 title，取首行/前 30 字作为 title
        first_line = content.splitlines()[0].strip() if content else ""
        title = first_line if len(first_line) <= 30 else first_line[:30] + "…"
        body_rest = content if len(content) > len(title) else ""
        idx = self.cbo_target.current()
        urgent = self.var_urgent.get()
        # 普通用户：不管 spinner，一律本地
        if getattr(self, "role", "user") == "user":
            add_local_task(title, body_rest, self._remind_at, urgent)
            self.set_status("✔ 已入待办（普通用户仅本机提醒）")
        elif idx == 0:
            try:
                r = API.publish_broadcast(title, body_rest, self._remind_at, urgent)
                upsert_group_task(int(r["id"]), title, body_rest, self._remind_at,
                                  CONFIG["nickname"], "*", urgent)
            except Exception as e:
                messagebox.showerror("广播失败", str(e), parent=self.root)
                return
            self.set_status("📢 已广播")
        elif self.groups_cache and 1 <= idx <= len(self.groups_cache):
            g = self.groups_cache[idx - 1]
            try:
                r = API.publish(g["code"], title, body_rest, self._remind_at, urgent)
                upsert_group_task(int(r["id"]), title, body_rest, self._remind_at,
                                  CONFIG["nickname"], g["code"], urgent)
            except Exception as e:
                messagebox.showerror("发布失败", str(e), parent=self.root)
                return
            self.set_status("✔ 已发布")
        else:
            add_local_task(title, body_rest, self._remind_at, urgent)
            self.set_status("✔ 已发布（本地）")
        self.txt_content.delete("1.0", "end")
        self._clear_remind()
        self.var_urgent.set(False)
        self.refresh_tasks()

    # -------- 群 --------
    def _build_groups(self) -> None:
        f = self.tab_groups
        top = tk.Frame(f)
        top.pack(fill="x", padx=4, pady=4)
        tk.Button(top, text="新建群", command=self._act_new_group).pack(side="left", padx=4)
        tk.Button(top, text="加入群 / 兑换口令", command=self._act_join_group).pack(side="left")

        cols = ("name", "code", "members", "joined")
        self.tv_groups = ttk.Treeview(f, columns=cols, show="headings")
        self.tv_groups.heading("name", text="群名")
        self.tv_groups.heading("code", text="群号")
        self.tv_groups.heading("members", text="成员数")
        self.tv_groups.heading("joined", text="状态")
        self.tv_groups.column("name", width=240)
        self.tv_groups.column("code", width=100)
        self.tv_groups.column("members", width=70, anchor="center")
        self.tv_groups.column("joined", width=110, anchor="center")
        self.tv_groups.pack(fill="both", expand=True, padx=4, pady=4)
        self.tv_groups.bind("<Double-1>", self._on_group_dblclick)

    def refresh_groups(self) -> None:
        if not CONFIG.get("server_url"):
            return
        def work():
            try:
                data = API.discover_groups()
                gs = data.get("groups", [])
                total = data.get("total", len(gs))
                mx = data.get("max", 20)
            except Exception:
                gs, total, mx = [], 0, 20
            def apply():
                # 只把已加入的群作为发布可选目标
                self.groups_cache = [g for g in gs if g.get("joined")]
                for iid in self.tv_groups.get_children():
                    self.tv_groups.delete(iid)
                for g in gs:
                    mark = "✅ 已加入" if g.get("joined") else "➕ 未加入"
                    self.tv_groups.insert("", "end",
                                          values=(g["name"], g["code"], g.get("member_count", 0), mark))
                try:
                    self.root.title(f"{APP_NAME} v{APP_VERSION}  —  群 {total}/{mx}")
                except Exception:
                    pass
                self._reload_publish_targets()
            run_on_ui(apply)
        threading.Thread(target=work, daemon=True).start()

    def _on_group_dblclick(self, _event) -> None:
        sel = self.tv_groups.selection()
        if not sel:
            return
        vals = self.tv_groups.item(sel[0], "values")
        if not vals or len(vals) < 4:
            return
        name, code, _members, mark = vals[0], vals[1], vals[2], vals[3]
        if "已加入" in mark:
            messagebox.showinfo(name, f"群号：{code}\n成员：{_members} 人", parent=self.root)
            return
        if not messagebox.askyesno("加入群", f"确定加入【{name}】（群号 {code}）？", parent=self.root):
            return
        try:
            r = API.join_group(str(code))
            messagebox.showinfo("已加入", f"已加入【{r.get('name', name)}】", parent=self.root)
            self.refresh_groups()
        except Exception as e:
            messagebox.showerror("失败", str(e), parent=self.root)

    def _act_new_group(self) -> None:
        if not self._require_server():
            return
        name = simpledialog.askstring("新建群", "输入群名（例：项目周会通知）", parent=self.root)
        if not name:
            return
        try:
            r = API.create_group(name.strip())
            messagebox.showinfo("已创建", f"群「{r['name']}」\n群号: {r['code']}\n\n把群号发给同事即可加入。",
                                parent=self.root)
            self.refresh_groups()
        except Exception as e:
            messagebox.showerror("失败", str(e), parent=self.root)

    def _act_join_group(self) -> None:
        if not self._require_server():
            return
        code = simpledialog.askstring(
            "加入群 / 兑换管理员口令",
            "输入 4 位群号 或 8 位管理员口令码：", parent=self.root)
        if not code:
            return
        code = code.strip()
        if not (len(code) == 4 and code.isdigit()) and len(code) != 8:
            messagebox.showwarning("提示", "请输入 4 位数字群号 或 8 位字母数字口令码",
                                   parent=self.root)
            return
        try:
            r = API.join_group(code)
        except Exception as e:
            messagebox.showerror("失败", str(e), parent=self.root); return
        # 兑换成功 → role=admin；普通加群 → 没有 role 字段
        if r.get("role") == "admin":
            messagebox.showinfo("恭喜",
                                f"你已成为管理员！到期时间: "
                                + datetime.fromtimestamp(r['expires_at']/1000).strftime('%Y-%m-%d %H:%M'),
                                parent=self.root)
            self.refresh_role()
        else:
            messagebox.showinfo("已加入", f"已加入「{r['name']}」", parent=self.root)
        self.refresh_groups()

    def _require_server(self) -> bool:
        if not CONFIG.get("server_url"):
            messagebox.showwarning("提示", "请先到「设置」页配置服务器地址", parent=self.root)
            return False
        return True

    # -------- 设置 --------
    def _build_settings(self) -> None:
        f = self.tab_settings
        tk.Label(f, text="昵称").grid(row=0, column=0, sticky="w", padx=8, pady=(12, 2))
        self.ent_nick = tk.Entry(f)
        self.ent_nick.insert(0, CONFIG.get("nickname", ""))
        self.ent_nick.grid(row=1, column=0, sticky="ew", padx=8)

        tk.Label(f, text=f"设备 ID: {CONFIG['device_id']}", fg="#6B7280"
                 ).grid(row=2, column=0, sticky="w", padx=8, pady=(12, 2))
        tk.Label(f, text=f"服务器: {(CONFIG.get('server_url') or '')} （内置，如需更换请联系管理员）",
                 fg="#6B7280", wraplength=520, justify="left"
                 ).grid(row=3, column=0, sticky="w", padx=8, pady=(4, 2))
        self.lbl_role = tk.Label(f, text="角色: 查询中…", fg="#4C6FFF")
        self.lbl_role.grid(row=4, column=0, sticky="w", padx=8, pady=(4, 2))

        btns = tk.Frame(f)
        btns.grid(row=5, column=0, sticky="ew", padx=8, pady=16)
        tk.Button(btns, text="保存并重连", command=self._save_settings,
                  bg="#4C6FFF", fg="white", relief="flat", width=14
                  ).pack(side="left")
        tk.Button(btns, text="连接测试", command=self._test_health, width=10
                  ).pack(side="left", padx=8)
        f.columnconfigure(0, weight=1)

    def _save_settings(self) -> None:
        CONFIG["nickname"] = self.ent_nick.get().strip() or "PC 用户"
        save_config(CONFIG)
        self.ws.stop()
        self.ws.start()
        self.refresh_groups()
        messagebox.showinfo("已保存", "配置已保存，正在重新连接…", parent=self.root)

    def _test_health(self) -> None:
        ok = API.health()
        if ok:
            messagebox.showinfo("连接测试", "✅ 服务器可达", parent=self.root)
        else:
            messagebox.showerror("连接测试", "❌ 无法连接，请稍后重试", parent=self.root)

    # -------- 说明 --------
    def _build_help(self) -> None:
        f = self.tab_help
        txt = tk.Text(f, wrap="word", padx=12, pady=12)
        txt.pack(fill="both", expand=True)
        header = f"当前版本：v{APP_VERSION}    ·    数据目录：{DATA_DIR}\n" + ("=" * 50) + "\n\n"
        txt.insert("1.0", header + HELP_TEXT)
        txt.configure(state="disabled")

    # -------- 管理员管理（仅超管可见） --------
    def _build_admin(self) -> None:
        f = self.tab_admin
        # v0.8.12 顶部：所有用户列表（替代 v0.8.11 的统计栏）
        users_head = tk.Frame(f, bg="#F3F4F6")
        users_head.pack(fill="x", padx=4, pady=(6, 0))
        self.lbl_admin_users_title = tk.Label(
            users_head, text="👥 所有用户（加载中…）", anchor="w",
            bg="#F3F4F6", fg="#374151", font=("Segoe UI", 10, "bold"),
        )
        self.lbl_admin_users_title.pack(side="left", padx=8, pady=4)
        tk.Button(users_head, text="刷新用户", command=self.refresh_admin_users,
                  relief="flat").pack(side="right", padx=6)
        cols_u = ("online", "nickname", "role", "groups", "tasks", "last_seen", "device_id")
        self.tv_users = ttk.Treeview(f, columns=cols_u, show="headings",
                                     selectmode="browse", height=8)
        for c, t, w, anc in [
            ("online", "在线", 50, "center"),
            ("nickname", "昵称", 130, "w"),
            ("role", "角色", 80, "center"),
            ("groups", "群数", 55, "center"),
            ("tasks", "任务数", 65, "center"),
            ("last_seen", "最近活跃", 130, "center"),
            ("device_id", "设备ID", 220, "w"),
        ]:
            self.tv_users.heading(c, text=t)
            self.tv_users.column(c, width=w, anchor=anc)
        self.tv_users.tag_configure("super", background="#FEE2E2")
        self.tv_users.tag_configure("admin", background="#DBEAFE")
        self.tv_users.tag_configure("online", foreground="#059669")
        self.tv_users.pack(fill="x", padx=8, pady=(2, 6))

        top = tk.Frame(f)
        top.pack(fill="x", padx=4, pady=6)
        tk.Label(top, text="有效期(天):").pack(side="left")
        self.ent_admin_days = tk.Entry(top, width=6)
        self.ent_admin_days.insert(0, "7")
        self.ent_admin_days.pack(side="left", padx=4)
        tk.Label(top, text="备注:").pack(side="left")
        self.ent_admin_note = tk.Entry(top, width=20)
        self.ent_admin_note.pack(side="left", padx=4)
        tk.Button(top, text="生成口令码", command=self._act_create_admin_code,
                  bg="#4C6FFF", fg="white", relief="flat"
                  ).pack(side="left", padx=6)
        tk.Button(top, text="刷新", command=self.refresh_admin).pack(side="left", padx=4)

        # 上：口令码
        tk.Label(f, text="口令码（转发给需要成为管理员的人，他/她到「群」页输入即生效）",
                 fg="#374151").pack(anchor="w", padx=8, pady=(6, 2))
        cols = ("code", "note", "expires", "used_by")
        self.tv_codes = ttk.Treeview(f, columns=cols, show="headings",
                                     selectmode="browse", height=6)
        for c, t, w in [("code", "口令码", 110), ("note", "备注", 160),
                        ("expires", "失效时间", 120), ("used_by", "已被兑换", 160)]:
            self.tv_codes.heading(c, text=t); self.tv_codes.column(c, width=w)
        self.tv_codes.pack(fill="x", expand=False, padx=8)
        tk.Button(f, text="复制选中口令码", command=self._act_copy_admin_code,
                  bg="#059669", fg="white", relief="flat"
                  ).pack(anchor="w", padx=8, pady=(2, 0))
        tk.Button(f, text="撤销选中的口令码", command=self._act_delete_admin_code
                  ).pack(anchor="w", padx=8, pady=(2, 8))

        # 下：管理员列表
        tk.Label(f, text="当前管理员（到期自动降为普通用户；可主动撤销）",
                 fg="#374151").pack(anchor="w", padx=8, pady=(6, 2))
        cols2 = ("device_id", "nickname", "granted_at", "expires_at")
        self.tv_admins = ttk.Treeview(f, columns=cols2, show="headings",
                                      selectmode="browse", height=6)
        for c, t, w in [("device_id", "设备ID", 200), ("nickname", "昵称", 110),
                        ("granted_at", "授权时间", 120), ("expires_at", "到期时间", 120)]:
            self.tv_admins.heading(c, text=t); self.tv_admins.column(c, width=w)
        self.tv_admins.pack(fill="both", expand=True, padx=8)
        tk.Button(f, text="撤销选中的管理员", command=self._act_revoke_admin,
                  bg="#DC2626", fg="white", relief="flat"
                  ).pack(anchor="w", padx=8, pady=6)

    def _act_create_admin_code(self) -> None:
        try:
            days = int(self.ent_admin_days.get().strip() or "7")
        except ValueError:
            messagebox.showerror("参数错", "有效期需为整数（天）", parent=self.root); return
        if days <= 0:
            messagebox.showwarning("提示", "天数必须 > 0", parent=self.root); return
        note = self.ent_admin_note.get().strip()
        try:
            r = API.admin_create_code(days, note)
        except Exception as e:
            messagebox.showerror("生成失败", str(e), parent=self.root); return
        code = r['code']
        # 自动复制到剪贴板，方便直接发给手机
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(code)
            self.root.update()
            copied = "\uff08已自动复制到剪贴板）"
        except Exception:
            copied = ""
        messagebox.showinfo("生成成功",
                            f"口令码: {code}{copied}\n\n请转发给对方，他/她到「群」页输入即可成为管理员。\n有效期至: "
                            + datetime.fromtimestamp(r['expires_at']/1000).strftime('%Y-%m-%d %H:%M'),
                            parent=self.root)
        self.ent_admin_note.delete(0, "end")
        self.refresh_admin()

    def _act_copy_admin_code(self) -> None:
        sel = self.tv_codes.selection()
        if not sel:
            messagebox.showinfo("提示", "请先在上方口令码列表中选中一行", parent=self.root); return
        code = str(self.tv_codes.item(sel[0])["values"][0])
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(code)
            self.root.update()
            messagebox.showinfo("已复制", f"口令码 {code} 已复制到剪贴板。", parent=self.root)
        except Exception as e:
            messagebox.showerror("复制失败", str(e), parent=self.root)

    def _act_delete_admin_code(self) -> None:
        sel = self.tv_codes.selection()
        if not sel: return
        code = self.tv_codes.item(sel[0])["values"][0]
        if not messagebox.askyesno("确认", f"撤销口令码 {code}？", parent=self.root):
            return
        try:
            API.admin_delete_code(str(code))
        except Exception as e:
            messagebox.showerror("失败", str(e), parent=self.root); return
        self.refresh_admin()

    def _act_revoke_admin(self) -> None:
        sel = self.tv_admins.selection()
        if not sel: return
        dev = self.tv_admins.item(sel[0])["values"][0]
        if not messagebox.askyesno("确认", f"撤销该管理员？\ndevice_id={dev}", parent=self.root):
            return
        try:
            API.admin_revoke(str(dev))
        except Exception as e:
            messagebox.showerror("失败", str(e), parent=self.root); return
        self.refresh_admin()

    def refresh_admin(self) -> None:
        if not self.is_super:
            return
        # v0.8.9：后台拉取，避免阻塞 UI
        self.set_status("🔄 拉取管理员列表中…")
        def worker():
            try:
                codes = API.admin_list_codes()
                admins = API.admin_list_admins()
            except Exception as e:
                self.root.after(0, lambda: self.set_status(f"管理员列表拉取失败: {e}"))
                return
            self.root.after(0, lambda: self._fill_admin(codes, admins))
        threading.Thread(target=worker, daemon=True).start()
        # v0.8.12 同步刷新用户列表
        self.refresh_admin_users()

    def refresh_admin_users(self) -> None:
        """v0.8.12 拉取所有用户（超管专用），后台线程 + 主线程回填"""
        if not self.is_super:
            return
        if not hasattr(self, "tv_users"):
            return
        self.lbl_admin_users_title.configure(text="👥 所有用户（加载中…）")
        def worker():
            try:
                users = API.admin_users()
            except Exception as e:
                self.root.after(0, lambda: self.lbl_admin_users_title.configure(
                    text=f"👥 拉取失败: {e}"))
                return
            self.root.after(0, lambda: self._fill_admin_users(users))
        threading.Thread(target=worker, daemon=True).start()

    def _fill_admin_users(self, users: list[dict]) -> None:
        if not hasattr(self, "tv_users"):
            return
        for iid in self.tv_users.get_children():
            self.tv_users.delete(iid)
        online_cnt = 0
        super_cnt = 0
        admin_cnt = 0
        for u in users:
            role = u.get("role", "")
            online = u.get("online", False)
            if online:
                online_cnt += 1
            if role == "超管":
                super_cnt += 1
            elif role == "管理员":
                admin_cnt += 1
            last = u.get("last_seen", 0) or 0
            last_txt = "—" if not last else datetime.fromtimestamp(last/1000).strftime("%m-%d %H:%M")
            tags = []
            if role == "超管":
                tags.append("super")
            elif role == "管理员":
                tags.append("admin")
            if online:
                tags.append("online")
            self.tv_users.insert("", "end", values=(
                "🟢" if online else "—",
                u.get("nickname") or "（未设置）",
                role,
                u.get("groups", 0),
                u.get("tasks", 0),
                last_txt,
                u.get("device_id", ""),
            ), tags=tuple(tags))
        self.lbl_admin_users_title.configure(
            text=f"👥 所有用户 共 {len(users)} 人  |  🟢 在线 {online_cnt}  |  🛡️ 超管 {super_cnt}  |  🔑 管理员 {admin_cnt}"
        )

    def _fill_admin(self, codes, admins) -> None:
        for iid in self.tv_codes.get_children(): self.tv_codes.delete(iid)
        for r in codes:
            exp = datetime.fromtimestamp(r["expires_at"]/1000).strftime("%m-%d %H:%M")
            used = ""
            if r.get("used_by"):
                used = str(r["used_by"])[:16] + "…"
            self.tv_codes.insert("", "end", values=(r["code"], r.get("note", ""), exp, used))
        for iid in self.tv_admins.get_children(): self.tv_admins.delete(iid)
        for r in admins:
            ga = datetime.fromtimestamp(r["granted_at"]/1000).strftime("%m-%d %H:%M")
            ea = datetime.fromtimestamp(r["expires_at"]/1000).strftime("%m-%d %H:%M")
            self.tv_admins.insert("", "end",
                                  values=(r["device_id"], r.get("nickname", ""), ga, ea))
        self.set_status("✔ 管理员列表已刷新")

    # -------- 周期公告（超管+管理员） --------
    def _build_recurring(self) -> None:
        f = self.tab_recurring
        top = tk.Frame(f)
        top.pack(fill="x", padx=4, pady=6)
        tk.Button(top, text="➕ 新建周期公告", command=self._act_new_recurring,
                  bg="#4C6FFF", fg="white", relief="flat", padx=10
                  ).pack(side="left")
        tk.Label(top, text="  双击行=编辑；右键行=启用/停用/删除", fg="#6B7280"
                 ).pack(side="left", padx=8)

        cols = ("id", "title", "freq_desc", "end_at", "target", "status", "creator")
        self.tv_recurring = ttk.Treeview(f, columns=cols, show="headings",
                                         selectmode="browse")
        for c, t, w in [("id", "ID", 40), ("title", "标题", 200),
                        ("freq_desc", "触发规则", 200), ("end_at", "结束日期", 100),
                        ("target", "目标", 80), ("status", "状态", 60),
                        ("creator", "创建者", 100)]:
            self.tv_recurring.heading(c, text=t); self.tv_recurring.column(c, width=w)
        self.tv_recurring.pack(fill="both", expand=True, padx=8, pady=6)
        self.tv_recurring.bind("<Double-1>", lambda e: self._act_edit_recurring())
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label="编辑", command=self._act_edit_recurring)
        menu.add_command(label="启用/停用", command=self._act_toggle_recurring)
        menu.add_command(label="立即触发一次(测试)", command=self._act_test_fire)
        menu.add_separator()
        menu.add_command(label="删除", command=self._act_delete_recurring)
        def popup(e):
            row = self.tv_recurring.identify_row(e.y)
            if row:
                self.tv_recurring.selection_set(row)
                menu.tk_popup(e.x_root, e.y_root)
        self.tv_recurring.bind("<Button-3>", popup)

    def refresh_recurring(self) -> None:
        if self.role not in ("super", "admin"):
            return
        # v0.8.9：后台拉取
        self.set_status("🔄 拉取周期公告中…")
        def worker():
            try:
                items = API.recurring_list()
            except Exception as e:
                self.root.after(0, lambda: self.set_status(f"周期公告拉取失败: {e}"))
                return
            self.root.after(0, lambda: self._fill_recurring(items))
        threading.Thread(target=worker, daemon=True).start()

    def _fill_recurring(self, items) -> None:
        for iid in self.tv_recurring.get_children():
            self.tv_recurring.delete(iid)
        wd_names = ["一", "二", "三", "四", "五", "六", "日"]
        for r in items:
            hh, mm = r["hh"], r["mm"]
            if r["freq"] == "daily":
                desc = f"每日 {hh:02d}:{mm:02d}"
            elif r["freq"] == "weekly":
                wd = [wd_names[int(x)] for x in r["weekdays"].split(",") if x.strip().isdigit()]
                desc = f"每周 {'/'.join(wd)} {hh:02d}:{mm:02d}"
            elif r["freq"] == "monthly":
                md = [x for x in r["monthdays"].split(",") if x.strip()]
                desc = f"每月 {'/'.join(md)}日 {hh:02d}:{mm:02d}"
            else:
                desc = r["freq"]
            tgt = "📢 广播" if r["target"] == "*" else f"群#{r['target']}"
            st = "▶ 启用" if r["active"] else "⏸ 停用"
            _end = int(r.get("end_at", 0) or 0)
            end_txt = "永不" if _end == 0 else datetime.fromtimestamp(_end/1000).strftime("%Y-%m-%d")
            self.tv_recurring.insert("", "end", iid=str(r["id"]),
                                     values=(r["id"], r["title"], desc, end_txt, tgt, st, r["creator_name"]))
        self.set_status("✔ 周期公告已刷新")

    def _sel_recurring_id(self) -> int | None:
        sel = self.tv_recurring.selection()
        if not sel: return None
        return int(sel[0])

    def _act_toggle_recurring(self) -> None:
        rid = self._sel_recurring_id()
        if not rid: return
        try:
            items = API.recurring_list()
            cur = next((x for x in items if x["id"] == rid), None)
            if not cur: return
            API.recurring_toggle(rid, not bool(cur["active"]))
        except Exception as e:
            messagebox.showerror("失败", str(e), parent=self.root); return
        self.refresh_recurring()

    def _act_delete_recurring(self) -> None:
        rid = self._sel_recurring_id()
        if not rid: return
        if not messagebox.askyesno("确认", f"删除周期公告 #{rid}？", parent=self.root):
            return
        try:
            API.recurring_delete(rid)
        except Exception as e:
            messagebox.showerror("失败", str(e), parent=self.root); return
        self.refresh_recurring()

    def _act_test_fire(self) -> None:
        """立即触发一次（本地手动发一条 task 到目标）。

        v0.8.10 修复：后端 broadcast 会跳过发起者，所以发起者本机原本收不到弹窗。
        这里先调后端广播 → 其他设备正常收；再在本地手动 upsert + 弹窗，让 PC 自己也能看到，
        方便快速自测。
        """
        rid = self._sel_recurring_id()
        if not rid: return
        try:
            items = API.recurring_list()
            r = next((x for x in items if x["id"] == rid), None)
            if not r: return
            title = r["title"]; content = r["content"]; urgent = bool(r["urgent"])
            if r["target"] == "*":
                resp = API.publish_broadcast(title, content, 0, urgent)
                group_code = "*"
            else:
                resp = API.publish(r["target"], title, content, 0, urgent)
                group_code = r["target"]
            server_id = int(resp.get("id", 0)) if isinstance(resp, dict) else 0
            # 本地也 upsert 一条并（若强弹）弹窗，让发起者自己也看得到
            if server_id > 0:
                publisher = CONFIG.get("nickname") or "我"
                upsert_group_task(server_id, title, content, 0,
                                  publisher, group_code, urgent)
                self.refresh_tasks()
                if urgent:
                    with _db() as c:
                        row = c.execute(
                            "SELECT * FROM tasks WHERE server_id=?", (server_id,)
                        ).fetchone()
                    if row:
                        def ack():
                            mark_done_by_server(server_id)
                            try:
                                API.mark_done(server_id)
                            except Exception:
                                pass
                            self.refresh_tasks()
                        show_urgent_popup(self.root, row, ack)
            self.set_status(f"✔ 已手动触发 #{rid}（其他设备走 WS 推送，本机已本地弹）")
        except Exception as e:
            messagebox.showerror("失败", str(e), parent=self.root)

    def _act_new_recurring(self) -> None:
        self._open_recurring_dialog()

    def _act_edit_recurring(self) -> None:
        rid = self._sel_recurring_id()
        if not rid: return
        try:
            items = API.recurring_list()
            r = next((x for x in items if x["id"] == rid), None)
        except Exception as e:
            messagebox.showerror("获取失败", str(e), parent=self.root); return
        if not r:
            messagebox.showwarning("提示", "未找到选中的周期任务", parent=self.root); return
        # 后端未支持 PATCH（只改 active），采用先 DELETE 后 POST 新建策略
        self._open_recurring_dialog(initial=r, replace_id=rid)

    def _open_recurring_dialog(self, initial: dict | None = None, replace_id: int | None = None) -> None:
        top = tk.Toplevel(self.root)
        top.title("编辑周期公告" if replace_id else "新建周期公告")
        top.transient(self.root); top.grab_set()
        top.geometry("+%d+%d" % (self.root.winfo_rootx()+80, self.root.winfo_rooty()+80))

        pad = {"padx": 10, "pady": 4}
        init = initial or {}
        # 标题（v0.8.12 可选：留空自动取 content 首行前 20 字）
        tk.Label(top, text="标题").grid(row=0, column=0, sticky="w", **pad)
        v_title = tk.StringVar(value=init.get("title", ""))
        tk.Entry(top, textvariable=v_title, width=42).grid(row=0, column=1, columnspan=3, sticky="ew", **pad)
        tk.Label(top, text="(可留空，自动取内容首行)", fg="#6B7280"
                 ).grid(row=0, column=4, sticky="w", padx=4)
        # 内容
        tk.Label(top, text="内容").grid(row=1, column=0, sticky="nw", **pad)
        txt = tk.Text(top, width=42, height=4)
        if init.get("content"): txt.insert("1.0", init.get("content", ""))
        txt.grid(row=1, column=1, columnspan=3, sticky="ew", **pad)
        # 强弹窗
        v_urg = tk.BooleanVar(value=bool(init.get("urgent", False)))
        tk.Checkbutton(top, text="🔴 强弹窗", variable=v_urg
                       ).grid(row=2, column=1, sticky="w", **pad)
        # 目标
        tk.Label(top, text="目标").grid(row=3, column=0, sticky="w", **pad)
        target_labels = ["📢 所有人（广播）"] + [f"群「{g['name']}」 #{g['code']}"
                                                for g in self.groups_cache]
        target_codes = ["*"] + [g["code"] for g in self.groups_cache]
        # 预填目标
        init_tgt = init.get("target", "*")
        try:
            init_idx = target_codes.index(init_tgt)
        except ValueError:
            init_idx = 0
        v_tgt = tk.StringVar(value=target_labels[init_idx])
        cbo_tgt = ttk.Combobox(top, textvariable=v_tgt, values=target_labels,
                                state="readonly", width=40)
        cbo_tgt.grid(row=3, column=1, columnspan=3, sticky="ew", **pad)
        # 频率
        tk.Label(top, text="频率").grid(row=4, column=0, sticky="w", **pad)
        v_freq = tk.StringVar(value=init.get("freq", "daily"))
        freq_frame = tk.Frame(top); freq_frame.grid(row=4, column=1, columnspan=3, sticky="w", **pad)
        for label, val in [("每天", "daily"), ("每周", "weekly"), ("每月", "monthly")]:
            tk.Radiobutton(freq_frame, text=label, variable=v_freq, value=val,
                           command=lambda: refresh_extra()).pack(side="left", padx=6)
        # 周几多选 / 每月日期
        tk.Label(top, text="周几").grid(row=5, column=0, sticky="w", **pad)
        wd_frame = tk.Frame(top); wd_frame.grid(row=5, column=1, columnspan=3, sticky="w", **pad)
        wd_vars = []
        init_wds = init.get("weekdays") or []
        if isinstance(init_wds, str):
            init_wds = [int(x) for x in init_wds.split(",") if x.strip().isdigit()]
        for i, name in enumerate(["一", "二", "三", "四", "五", "六", "日"]):
            v = tk.BooleanVar(value=(i in init_wds))
            tk.Checkbutton(wd_frame, text=name, variable=v).pack(side="left")
            wd_vars.append(v)

        tk.Label(top, text="每月日期").grid(row=6, column=0, sticky="w", **pad)
        init_md = init.get("monthdays") or ""
        if isinstance(init_md, list):
            init_md = ",".join(str(x) for x in init_md)
        v_md = tk.StringVar(value=init_md)
        tk.Entry(top, textvariable=v_md, width=42).grid(row=6, column=1, columnspan=3, sticky="ew", **pad)
        tk.Label(top, text="(逗号分隔, 例: 1,15,28)", fg="#6B7280"
                 ).grid(row=7, column=1, columnspan=3, sticky="w", **pad)

        # 时间 时:分
        tk.Label(top, text="时刻").grid(row=8, column=0, sticky="w", **pad)
        tf = tk.Frame(top); tf.grid(row=8, column=1, sticky="w", **pad)
        v_hh = tk.StringVar(value=f"{int(init.get('hh', 9)):02d}")
        v_mm = tk.StringVar(value=f"{int(init.get('mm', 0)):02d}")
        tk.Spinbox(tf, from_=0, to=23, width=4, textvariable=v_hh, format="%02.0f", wrap=True
                   ).pack(side="left")
        tk.Label(tf, text=" : ").pack(side="left")
        tk.Spinbox(tf, from_=0, to=59, width=4, textvariable=v_mm, format="%02.0f", wrap=True
                   ).pack(side="left")

        # v0.8.12 结束日期（留空 = 永不结束）；格式 YYYY-MM-DD
        tk.Label(top, text="结束日期").grid(row=9, column=0, sticky="w", **pad)
        init_end_txt = ""
        try:
            _end = int(init.get("end_at", 0) or 0)
            if _end > 0:
                init_end_txt = datetime.fromtimestamp(_end/1000).strftime("%Y-%m-%d")
        except Exception:
            pass
        v_end = tk.StringVar(value=init_end_txt)
        end_frame = tk.Frame(top); end_frame.grid(row=9, column=1, columnspan=3, sticky="w", **pad)
        tk.Entry(end_frame, textvariable=v_end, width=14).pack(side="left")
        tk.Label(end_frame, text="  格式 YYYY-MM-DD（留空 = 永不结束）", fg="#6B7280"
                 ).pack(side="left")

        def refresh_extra():
            fq = v_freq.get()
            for w in wd_frame.winfo_children(): w.configure(state="normal" if fq == "weekly" else "disabled")
            # md entry：weekly/daily 禁用
        refresh_extra()

        def ok():
            title = v_title.get().strip()
            # v0.8.12 title 可留空
            content = txt.get("1.0", "end").strip()
            if not title and not content:
                messagebox.showwarning("提示", "标题和内容不能同时为空", parent=top); return
            try:
                hh = int(v_hh.get()); mm = int(v_mm.get())
            except ValueError:
                messagebox.showwarning("提示", "时刻格式错", parent=top); return
            # v0.8.12 结束时间
            end_at_ms = 0
            end_txt = v_end.get().strip()
            if end_txt:
                try:
                    dt_end = datetime.strptime(end_txt, "%Y-%m-%d")
                    # 结束日当天 23:59:59
                    dt_end = dt_end.replace(hour=23, minute=59, second=59)
                    end_at_ms = int(dt_end.timestamp() * 1000)
                    if end_at_ms < int(time.time() * 1000):
                        messagebox.showwarning("提示", "结束日期不能是过去", parent=top); return
                except ValueError:
                    messagebox.showwarning("提示", "结束日期格式需为 YYYY-MM-DD", parent=top); return
            fq = v_freq.get()
            weekdays = [i for i, v in enumerate(wd_vars) if v.get()] if fq == "weekly" else []
            monthdays: list[int] = []
            if fq == "monthly":
                try:
                    monthdays = [int(x.strip()) for x in v_md.get().split(",") if x.strip()]
                    for d in monthdays:
                        if d < 1 or d > 31: raise ValueError
                except ValueError:
                    messagebox.showwarning("提示", "每月日期需为 1-31 逗号分隔整数", parent=top); return
            if fq == "weekly" and not weekdays:
                messagebox.showwarning("提示", "请至少选一天", parent=top); return
            if fq == "monthly" and not monthdays:
                messagebox.showwarning("提示", "请至少填一个日期", parent=top); return
            idx = cbo_tgt.current()
            target = target_codes[idx] if 0 <= idx < len(target_codes) else "*"
            payload = {
                "device_id": CONFIG["device_id"],
                "nickname": CONFIG.get("nickname", "管理员"),
                "title": title, "content": content, "urgent": bool(v_urg.get()),
                "target": target, "freq": fq,
                "weekdays": weekdays, "monthdays": monthdays,
                "hh": hh, "mm": mm,
                "end_at": end_at_ms,
            }
            try:
                if replace_id:
                    # 先删旧，再新建（后端无 update endpoint）
                    try:
                        API.recurring_delete(replace_id)
                    except Exception:
                        pass
                API.recurring_create(payload)
            except Exception as e:
                messagebox.showerror("保存失败", str(e), parent=top); return
            top.destroy()
            self.refresh_recurring()

        btns = tk.Frame(top); btns.grid(row=10, column=0, columnspan=4, pady=12)
        tk.Button(btns, text=("保存" if replace_id else "创建"), command=ok, width=10,
                  bg="#4C6FFF", fg="white", relief="flat"
                  ).pack(side="left", padx=8)
        tk.Button(btns, text="取消", command=top.destroy, width=8).pack(side="left", padx=8)
        top.columnconfigure(1, weight=1)

    # -------- 角色刷新 --------
    def refresh_role(self) -> None:
        try:
            r = API.get_role()
        except Exception:
            r = {"role": "user", "is_super": False, "expires_at": None}
        self.role = r.get("role", "user")
        self.is_super = bool(r.get("is_super"))
        self.role_expires_at = r.get("expires_at")
        # 更新设置页 role 标签
        if hasattr(self, "lbl_role"):
            desc = {"super": "🛡️ 超级管理员（硬编码）",
                    "admin": "🔑 管理员",
                    "user": "👤 普通用户"}.get(self.role, self.role)
            if self.role == "admin" and self.role_expires_at:
                desc += "，到期: " + datetime.fromtimestamp(
                    self.role_expires_at/1000).strftime("%Y-%m-%d %H:%M")
            self.lbl_role.configure(text="当前角色: " + desc)
        # 超管：显示"管理员管理" tab
        if self.is_super and not self._admin_tab_added:
            self.nb.add(self.tab_admin, text="🛡️ 管理员管理")
            self._admin_tab_added = True
            self.refresh_admin()
        elif not self.is_super and self._admin_tab_added:
            try: self.nb.forget(self.tab_admin)
            except Exception: pass
            self._admin_tab_added = False
        # 超管/管理员：显示"周期公告" tab
        if self.role in ("super", "admin") and not self._recurring_tab_added:
            self.nb.add(self.tab_recurring, text="⏰ 周期公告")
            self._recurring_tab_added = True
            self.refresh_recurring()
        elif self.role not in ("super", "admin") and self._recurring_tab_added:
            try: self.nb.forget(self.tab_recurring)
            except Exception: pass
            self._recurring_tab_added = False
        # 刷新发布 tab 的目标列表（会根据 role 限制）
        self._reload_publish_targets()

    # -------- 历史 --------
    def _build_history(self) -> None:
        f = self.tab_history
        top = tk.Frame(f)
        top.pack(fill="x", padx=4, pady=4)
        tk.Button(top, text="刷新", command=self.refresh_history).pack(side="left")
        tk.Button(top, text="全部删除", command=self._act_delete_all_done,
                  bg="#DC2626", fg="white", relief="flat"
                  ).pack(side="right", padx=4)
        tk.Label(top, text="  最近已完成 200 条",
                 fg="#6B7280").pack(side="left", padx=8)

        cols = ("title", "publisher", "created", "src")
        self.tv_history = ttk.Treeview(f, columns=cols, show="headings", selectmode="browse")
        self.tv_history.heading("title", text="标题")
        self.tv_history.heading("publisher", text="来源")
        self.tv_history.heading("created", text="发布时间")
        self.tv_history.heading("src", text="类型")
        self.tv_history.column("title", width=280)
        self.tv_history.column("publisher", width=90)
        self.tv_history.column("created", width=120)
        self.tv_history.column("src", width=60, anchor="center")
        self.tv_history.pack(fill="both", expand=True, padx=4, pady=4)
        self.refresh_history()

    def refresh_history(self) -> None:
        if not hasattr(self, "tv_history"):
            return
        for iid in self.tv_history.get_children():
            self.tv_history.delete(iid)
        for row in list_done():
            ct = row["created_at"]
            tstr = datetime.fromtimestamp(ct / 1000).strftime("%m-%d %H:%M") if ct else "-"
            src = "群" if row["source"] == "group" else "本地"
            title = ("🔴 " if row["urgent"] else "") + row["title"]
            self.tv_history.insert("", "end", iid=str(row["id"]),
                                   values=(title, row["publisher"], tstr, src))

    def _act_delete_all_done(self) -> None:
        n = len(self.tv_history.get_children())
        if n == 0:
            return
        if not messagebox.askyesno("确认", f"确定要删除 {n} 条历史记录吗？此操作不可恢复。",
                                    parent=self.root):
            return
        delete_all_done()
        self.refresh_history()

    # -------- 通用 --------
    def show(self) -> None:
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def hide(self) -> None:
        self.root.withdraw()

    def set_status(self, text: str) -> None:
        self.status_var.set(text)


# ============================================================
# 主入口
# ============================================================

def _acquire_single_instance_lock() -> bool:
    """v0.8.14 Windows 单实例锁：True=获取成功；False=已有实例在跑。
    使用命名互斥体（默认 session-local 命名空间，同一用户会话内互斥）。"""
    try:
        import ctypes
        ERROR_ALREADY_EXISTS = 183
        h = ctypes.windll.kernel32.CreateMutexW(
            None, False, "YongGuanSanJun_App_Instance_v1"
        )
        if not h:
            return True  # 无法创建也不阻塞启动
        last_err = ctypes.windll.kernel32.GetLastError()
        if last_err == ERROR_ALREADY_EXISTS:
            return False
        # 保留全局引用，防止 GC 释放锁
        globals()["_singleton_mutex_handle"] = h
        return True
    except Exception:
        return True


def _release_single_instance_lock() -> None:
    """v0.8.15 自升级前主动释放互斥体，让新 exe 能立即拿锁"""
    try:
        h = globals().pop("_singleton_mutex_handle", None)
        if h:
            import ctypes
            ctypes.windll.kernel32.ReleaseMutex(h)
            ctypes.windll.kernel32.CloseHandle(h)
    except Exception:
        pass


def _perform_self_update(api: "Api", meta: dict) -> bool:
    """v0.8.15 自升级：下载新 exe → 生成 updater.bat → 启动 bat → 当前进程退出。
    返回 True 表示已启动升级流程（调用方应立即 sys.exit）。"""
    if not getattr(sys, "frozen", False):
        # 开发模式（python.exe 运行）不做替换升级
        return False
    if sys.platform != "win32":
        return False
    try:
        old_exe = os.path.abspath(sys.executable)
        temp_dir = os.environ.get("TEMP") or os.path.expanduser("~")
        new_exe = globals().get("_downloaded_new_exe_path") or os.path.join(
            temp_dir, f"yongguansanjun_new_{os.getpid()}.exe"
        )
        bat_path = os.path.join(temp_dir, f"yongguansanjun_updater_{os.getpid()}.bat")
        if not os.path.exists(new_exe):
            return False
        expected_size = int(meta.get("size") or 0)
        actual_size = os.path.getsize(new_exe)
        if actual_size < 15 * 1024 * 1024 or (expected_size > 0 and actual_size != expected_size):
            return False
        # 生成 updater.bat：
        # 方案：**不用 start 自动启动新版**，避免 Defender 拦截 _MEI 释放的 python312.dll。
        # 改用 explorer /select 高亮新 exe，让用户主动双击（Defender 认用户交互，不会拦）。
        bat_content = (
            "@echo off\r\n"
            'set "_MEIPASS2="\r\n'
            'set "_MEIPASS="\r\n'
            "ping -n 4 127.0.0.1 > nul\r\n"
            "set /a tries=0\r\n"
            ":retry\r\n"
            "set /a tries+=1\r\n"
            f'move /y "{new_exe}" "{old_exe}" > nul 2>&1\r\n'
            "if not errorlevel 1 goto done\r\n"
            "if %tries% GEQ 30 goto fail\r\n"
            "ping -n 2 127.0.0.1 > nul\r\n"
            "goto retry\r\n"
            ":done\r\n"
            f'explorer /select,"{old_exe}"\r\n'
            'del "%~f0"\r\n'
            "exit /b 0\r\n"
            ":fail\r\n"
            "exit /b 1\r\n"
        )
        with open(bat_path, "w", encoding="gbk", errors="replace", newline="") as f:
            f.write(bat_content)
        # v0.8.15.2 标记本次升级来源，新版启动时会弹窗提示"升级成功"
        try:
            CONFIG["pending_upgrade_from"] = APP_VERSION
            save_config(CONFIG)
        except Exception:
            pass
        # 释放单实例互斥体
        _release_single_instance_lock()
        # 启动 bat 隐藏窗口 + 脱离当前进程
        CREATE_NO_WINDOW = 0x08000000
        DETACHED_PROCESS = 0x00000008
        subprocess.Popen(
            ["cmd.exe", "/c", bat_path],
            creationflags=CREATE_NO_WINDOW | DETACHED_PROCESS,
            close_fds=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
    except Exception:
        return False


def _show_update_dialog(root: "tk.Tk", api: "Api", meta: dict) -> None:
    """v0.8.15 弹出带进度条的升级对话框。
    流程：弹窗（版本信息 + 更新按钮）→ 点更新 → 显示进度条 → 下载完 → 显示『安装并重启』按钮 → 点击后杀自己启动新版。"""
    try:
        remote_name = str(meta.get("versionName") or "")
        changelog = str(meta.get("changelog") or "")
        total_size = int(meta.get("size") or 0)

        top = tk.Toplevel(root)
        top.title(f"{APP_NAME} · 有新版本")
        top.geometry("480x320")
        top.transient(root)
        top.grab_set()
        top.attributes("-topmost", True)

        tk.Label(top, text=f"发现新版 v{remote_name}",
                 font=("Segoe UI", 12, "bold")).pack(pady=(12, 4))
        tk.Label(top, text=f"当前版本 v{APP_VERSION}",
                 fg="#6B7280").pack()

        info_frame = tk.LabelFrame(top, text="更新内容", padx=10, pady=6)
        info_frame.pack(fill="x", padx=16, pady=8)
        tk.Label(info_frame, text=changelog or "（无说明）",
                 anchor="w", justify="left", wraplength=430).pack(fill="x")

        tk.Label(top, text="您的昵称、服务器、群列表等设置将自动保留",
                 fg="#4B5563", font=("Segoe UI", 9)).pack()

        # 进度条 & 状态
        progress = ttk.Progressbar(top, orient="horizontal", mode="determinate",
                                   maximum=100, length=440)
        progress.pack(pady=(12, 4), padx=16, fill="x")
        status_var = tk.StringVar(value="点击『开始下载』后台下载新版本，完成后可选择立即安装。")
        tk.Label(top, textvariable=status_var, fg="#374151").pack(padx=16)

        btn_frame = tk.Frame(top)
        btn_frame.pack(pady=10)

        state = {"downloading": False, "downloaded": False, "new_exe": None}

        def on_download():
            if state["downloading"] or state["downloaded"]:
                return
            state["downloading"] = True
            btn_action.configure(text="下载中…", state="disabled")
            btn_cancel.configure(text="后台下载并关闭本窗口")
            temp_dir = os.environ.get("TEMP") or os.path.expanduser("~")
            new_exe = os.path.join(temp_dir, f"yongguansanjun_new_{os.getpid()}.exe")
            state["new_exe"] = new_exe

            def report(done_bytes, total_bytes):
                try:
                    total = total_bytes or total_size or 1
                    pct = min(100, int(done_bytes * 100 / total))
                    def apply():
                        progress["value"] = pct
                        mb_done = done_bytes / (1024 * 1024)
                        mb_total = total / (1024 * 1024)
                        status_var.set(f"下载中 {mb_done:.1f} / {mb_total:.1f} MB  ({pct}%)")
                    run_on_ui(apply)
                except Exception:
                    pass

            def work():
                ok = api.download_file(meta.get("url", ""), new_exe, on_progress=report)
                expected = int(meta.get("size") or 0)
                actual = os.path.getsize(new_exe) if os.path.exists(new_exe) else 0
                if not ok or actual < 15 * 1024 * 1024 or (expected > 0 and actual != expected):
                    def failed():
                        try:
                            if os.path.exists(new_exe):
                                os.remove(new_exe)
                        except Exception:
                            pass
                        status_var.set("下载失败或文件不完整，请稍后重试。")
                        btn_action.configure(text="重试下载", state="normal")
                        state["downloading"] = False
                    run_on_ui(failed)
                    return
                # v0.8.15.1 SHA256 校验：防止升级链路被中间人替换恶意 exe
                expected_sha = (meta.get("sha256") or "").strip().lower()
                if expected_sha:
                    try:
                        import hashlib as _hl
                        h = _hl.sha256()
                        with open(new_exe, "rb") as fh:
                            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                                h.update(chunk)
                        actual_sha = h.hexdigest()
                    except Exception:
                        actual_sha = ""
                    if actual_sha != expected_sha:
                        def sha_failed():
                            try:
                                if os.path.exists(new_exe):
                                    os.remove(new_exe)
                            except Exception:
                                pass
                            status_var.set(f"⚠️ 文件校验失败（SHA256 不匹配），已删除。请重试或联系管理员。")
                            btn_action.configure(text="重试下载", state="normal")
                            state["downloading"] = False
                        run_on_ui(sha_failed)
                        return
                def done():
                    state["downloading"] = False
                    state["downloaded"] = True
                    globals()["_downloaded_new_exe_path"] = new_exe
                    progress["value"] = 100
                    status_var.set(f"✅ 下载完成（{actual/(1024*1024):.1f} MB）。点击『立即安装』后本程序退出，会弹出文件夹高亮新版，请手动双击新版即可完成升级。")
                    btn_action.configure(text="立即安装（弹文件夹）", state="normal", bg="#10B981", fg="white")
                    btn_action.configure(command=on_install)
                run_on_ui(done)

            threading.Thread(target=work, daemon=True).start()

        def on_install():
            if not state["downloaded"]:
                return
            ok = _perform_self_update(api, meta)
            if ok:
                # 提前给出个提示（虽然本进程即将退出）
                try:
                    status_var.set("正在替换旧版本，几秒后会弹出文件夹，请手动双击新版...")
                    top.update()
                except Exception:
                    pass
                try:
                    time.sleep(0.3)
                except Exception:
                    pass
                os._exit(0)
            else:
                status_var.set("❌ 启动升级失败，请手动运行下载的新 exe。")

        def on_cancel():
            top.grab_release()
            top.destroy()

        btn_action = tk.Button(btn_frame, text="开始下载", width=18, command=on_download)
        btn_action.pack(side="left", padx=6)
        btn_cancel = tk.Button(btn_frame, text="稍后再说", width=18, command=on_cancel)
        btn_cancel.pack(side="left", padx=6)

        # 若用户强关窗口且未下载，视为取消
        top.protocol("WM_DELETE_WINDOW", on_cancel)
        # 让 root 显示以便看到 Toplevel（Windows Tk 有时 grab 不出焦点）
        try:
            root.deiconify()
        except Exception:
            pass
    except Exception:
        pass


def _check_and_prompt_update(root: "tk.Tk", api: "Api") -> None:
    """v0.8.15 后台线程：查最新版本 → UI 线程弹升级窗（含进度条）"""
    try:
        meta = api.check_pc_update()
        remote_code = int(meta.get("versionCode") or 0)
        if remote_code <= APP_VERSION_CODE:
            return
        run_on_ui(lambda: _show_update_dialog(root, api, meta))
    except Exception:
        pass


def main() -> None:
    # v0.8.14：单实例守护——避免重复双击 exe 造成两个托盘/两份气泡
    if sys.platform == "win32" and not _acquire_single_instance_lock():
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(
                0,
                "勇冠三军提醒器已经在运行了。\n\n请到系统托盘（屏幕右下角）找红色 “勇” 字图标，双击即可打开主窗口。",
                "勇冠三军提醒器",
                0x40,  # MB_ICONINFORMATION
            )
        except Exception:
            pass
        sys.exit(0)

    root = tk.Tk()
    root.withdraw()  # 启动即最小化到托盘

    win: MainWindow | None = None

    def on_show():
        if win:
            win.show()

    def on_quit():
        try:
            ws.stop()
        except Exception:
            pass
        root.after(50, root.destroy)

    tray = Tray(on_show=on_show, on_quit=on_quit)

    # v0.8.14：last_sync 毫秒时间戳（补拉未读时的水位线），存到 CONFIG
    def _get_last_sync() -> int:
        try:
            return int(CONFIG.get("last_sync_ms", 0) or 0)
        except Exception:
            return 0

    def _set_last_sync(v: int) -> None:
        CONFIG["last_sync_ms"] = int(v)
        try:
            save_config(CONFIG)
        except Exception:
            pass

    def _beep(urgent: bool) -> None:
        if not winsound:
            return
        try:
            if urgent:
                # 三连响提示紧急
                for _ in range(3):
                    winsound.MessageBeep(winsound.MB_ICONHAND)
                    time.sleep(0.15)
            else:
                winsound.MessageBeep(winsound.MB_ICONASTERISK)
        except Exception:
            pass

    def _tray_notify(title: str, msg: str) -> None:
        try:
            tray.icon.notify(msg or " ", title)
        except Exception:
            pass

    def _flash_taskbar(times: int = 6) -> None:
        """v0.8.14 兜底：闪 Windows 任务栏按钮，直到用户切到窗口。
        仅在主窗口已显示（有任务栏条目）时生效；托盘状态下不做。"""
        try:
            if not win or win.root.state() == "withdrawn":
                return
            import ctypes
            frame = win.root.wm_frame()
            if not frame:
                return
            hwnd = int(frame, 16)
            FLASHW_TRAY = 0x2
            FLASHW_TIMERNOFG = 0xC  # 闪到用户切到窗口为止

            class FLASHWINFO(ctypes.Structure):
                _fields_ = [
                    ("cbSize", ctypes.c_uint),
                    ("hwnd", ctypes.c_void_p),
                    ("dwFlags", ctypes.c_uint),
                    ("uCount", ctypes.c_uint),
                    ("dwTimeout", ctypes.c_uint),
                ]

            info = FLASHWINFO(ctypes.sizeof(FLASHWINFO), hwnd,
                              FLASHW_TRAY | FLASHW_TIMERNOFG, times, 0)
            ctypes.windll.user32.FlashWindowEx(ctypes.byref(info))
        except Exception:
            pass

    def handle_event(evt: str, data: dict) -> None:
        if evt == "_open":
            run_on_ui(lambda: win and win.set_status("已连接"))
            # v0.8.14：WS 每次重连后补拉一次未读，防止断连期间漏消息
            since = _get_last_sync()
            missed = API.pull_unread(since)
            if missed:
                max_ct = since
                for t in missed:
                    ct = int(t.get("created_at", 0) or 0)
                    if ct > max_ct:
                        max_ct = ct
                    # 复用 task.new 逻辑
                    handle_event("task.new", {"task": t})
                if max_ct > since:
                    _set_last_sync(max_ct)
            return
        if evt == "_close":
            run_on_ui(lambda: win and win.set_status("断开，重连中…"))
            return
        if evt == "task.new":
            t = data.get("task") or {}
            server_id = int(t.get("id", 0))
            if server_id <= 0:
                return
            title = t.get("title", "")
            content = t.get("content", "")
            remind_at = int(t.get("remind_at", 0))
            publisher = t.get("publisher_name", "群成员")
            group_code = t.get("group_code", "")
            urgent = bool(t.get("urgent", False))
            created_at = int(t.get("created_at", 0) or 0)
            # v0.8.14：推进 last_sync 水位线 + 铃声/托盘气泡（避免只在托盘变红看不见）
            if created_at > _get_last_sync():
                _set_last_sync(created_at)
            _beep(urgent)
            prefix = "🔴 紧急" if urgent else "💬 新消息"
            _tray_notify(
                f"{prefix} · {publisher}",
                (title or content or "（无内容）")[:120],
            )
            # v0.8.14：主窗口在前台/最小化时闪任务栏；托盘状态下已由 beep+balloon 提示
            run_on_ui(lambda: _flash_taskbar(9 if urgent else 5))

            def apply():
                upsert_group_task(server_id, title, content, remind_at,
                                  publisher, group_code, urgent)
                if win:
                    win.refresh_tasks()
                    if urgent:
                        with _db() as c:
                            row = c.execute(
                                "SELECT * FROM tasks WHERE server_id=?", (server_id,)
                            ).fetchone()
                        if row:
                            def ack():
                                mark_done_by_server(server_id)
                                try:
                                    API.mark_done(server_id)
                                except Exception:
                                    pass
                                win.refresh_tasks()
                            show_urgent_popup(root, row, ack)
            run_on_ui(apply)
            return
        if evt == "task.done":
            sid = int((data.get("task") or {}).get("id", 0))
            if sid > 0:
                mark_done_by_server(sid)
                run_on_ui(lambda: win and win.refresh_tasks())

    ws = WsWorker(handle_event)
    win = MainWindow(root, tray, ws)
    tray.run_detached()

    # 启动预置周期性提醒
    def _on_scheduled():
        run_on_ui(lambda: win and (win.refresh_tasks(), win.refresh_history()))
    start_scheduler(_on_scheduled)

    if CONFIG.get("server_url"):
        ws.start()
    # v0.8.14：首次运行一律弹主窗口（避免用户双击exe看不到反应）
    # v0.8.15.2：升级后启动也弹主窗口 + 提示升级成功，让用户看到"升级到新版了"
    just_upgraded_from = CONFIG.pop("pending_upgrade_from", None)
    if just_upgraded_from:
        try:
            save_config(CONFIG)
        except Exception:
            pass
    if IS_FIRST_RUN or not CONFIG.get("server_url") or just_upgraded_from:
        win.show()
    if just_upgraded_from:
        def _notify_upgraded():
            try:
                messagebox.showinfo(
                    APP_NAME,
                    f"升级成功！\n\nv{just_upgraded_from} → v{APP_VERSION}",
                )
            except Exception:
                pass
        run_on_ui(_notify_upgraded)

    # v0.8.15：启动后 5 秒后台检查升级（不阻塞 UI）
    def _delayed_update_check():
        try:
            time.sleep(5)
            if not CONFIG.get("server_url"):
                return
            _check_and_prompt_update(root, API)
        except Exception:
            pass
    threading.Thread(target=_delayed_update_check, daemon=True).start()

    # UI 事件循环 + 后台任务泵
    def pump():
        try:
            while True:
                func = UI_QUEUE.get_nowait()
                try:
                    func()
                except Exception:
                    pass
        except queue.Empty:
            pass
        root.after(80, pump)

    pump()
    try:
        root.mainloop()
    finally:
        ws.stop()
        try:
            tray.icon.stop()
        except Exception:
            pass


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
