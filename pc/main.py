"""NoticeFloat PC 客户端。

托盘常驻 + Tkinter 主窗口 + WebSocket 长连接。与 Android 端共用后端 API/WS。
"""
from __future__ import annotations

import json
import os
import queue
import sqlite3
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

# ============================================================
# 配置与存储
# ============================================================

APP_NAME = "NoticeFloat"
DATA_DIR = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), APP_NAME)
os.makedirs(DATA_DIR, exist_ok=True)
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")
DB_PATH = os.path.join(DATA_DIR, "notice.db")


def load_config() -> dict:
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    cfg = {
        "server_url": "",
        "nickname": os.environ.get("USERNAME", "PC 用户"),
        "device_id": f"pc-{uuid.uuid4().hex[:16]}",
    }
    save_config(cfg)
    return cfg


def save_config(cfg: dict) -> None:
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


CONFIG = load_config()


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
            "name": name,
            "owner_device_id": self.cfg["device_id"],
            "owner_nickname": self.cfg["nickname"],
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

    def mark_done(self, server_id: int) -> None:
        requests.post(self._url(f"/api/tasks/{server_id}/done"), json={
            "device_id": self.cfg["device_id"],
        }, timeout=6)


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
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    color = (255, 59, 48, 255) if alert else (76, 111, 255, 255)
    d.ellipse((6, 6, 58, 58), fill=color)
    d.text((20, 18), "NF", fill=(255, 255, 255, 255))
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

        root.title(APP_NAME)
        root.geometry("620x520")
        root.protocol("WM_DELETE_WINDOW", self.hide)

        style = ttk.Style()
        try:
            style.theme_use("vista")
        except Exception:
            pass

        nb = ttk.Notebook(root)
        nb.pack(fill="both", expand=True, padx=8, pady=8)

        self.tab_tasks = ttk.Frame(nb)
        self.tab_publish = ttk.Frame(nb)
        self.tab_groups = ttk.Frame(nb)
        self.tab_settings = ttk.Frame(nb)
        nb.add(self.tab_tasks, text="待办")
        nb.add(self.tab_publish, text="发布")
        nb.add(self.tab_groups, text="群")
        nb.add(self.tab_settings, text="设置")

        self._build_tasks()
        self._build_publish()
        self._build_groups()
        self._build_settings()

        self.status_var = tk.StringVar(value="未连接")
        tk.Label(root, textvariable=self.status_var, anchor="w",
                 fg="#6B7280").pack(fill="x", side="bottom", padx=10, pady=2)

        self.refresh_tasks()
        self.refresh_groups()

    # -------- 待办 --------
    def _build_tasks(self) -> None:
        f = self.tab_tasks
        top = tk.Frame(f)
        top.pack(fill="x", padx=4, pady=4)
        tk.Button(top, text="刷新", command=self.refresh_tasks).pack(side="left")
        tk.Button(top, text="标记完成", command=self._act_done).pack(side="left", padx=4)
        tk.Button(top, text="删除", command=self._act_delete).pack(side="left")

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

    def _act_delete(self) -> None:
        tid = self._selected_task_id()
        if not tid:
            return
        delete_task(tid)
        self.refresh_tasks()

    # -------- 发布 --------
    def _build_publish(self) -> None:
        f = self.tab_publish
        tk.Label(f, text="标题").grid(row=0, column=0, sticky="w", padx=8, pady=(10, 2))
        self.ent_title = tk.Entry(f)
        self.ent_title.grid(row=1, column=0, columnspan=3, sticky="ew", padx=8)

        tk.Label(f, text="内容").grid(row=2, column=0, sticky="w", padx=8, pady=(10, 2))
        self.txt_content = tk.Text(f, height=5)
        self.txt_content.grid(row=3, column=0, columnspan=3, sticky="ew", padx=8)

        tk.Label(f, text="发送到").grid(row=4, column=0, sticky="w", padx=8, pady=(10, 2))
        self.cbo_target = ttk.Combobox(f, state="readonly")
        self.cbo_target.grid(row=5, column=0, columnspan=3, sticky="ew", padx=8)

        self.var_urgent = tk.BooleanVar()
        tk.Checkbutton(f, text="🔴 强弹窗（收到后必须点确认才能关）",
                       variable=self.var_urgent).grid(row=6, column=0, columnspan=3,
                                                       sticky="w", padx=6, pady=(10, 4))

        tk.Label(f, text="提醒时间").grid(row=7, column=0, sticky="w", padx=8, pady=(6, 2))
        self.var_remind = tk.StringVar(value="未设置")
        tk.Label(f, textvariable=self.var_remind, fg="#4C6FFF").grid(row=8, column=0,
                                                                       sticky="w", padx=8)

        quick = tk.Frame(f)
        quick.grid(row=9, column=0, columnspan=3, sticky="w", padx=6, pady=6)
        for text, mins in [("30 分钟", 30), ("1 小时", 60), ("3 小时", 180)]:
            tk.Button(quick, text=text, width=8,
                      command=lambda m=mins: self._quick_remind(m)).pack(side="left", padx=2)
        tk.Button(quick, text="明早 9 点", width=10,
                  command=self._quick_tomorrow).pack(side="left", padx=2)
        tk.Button(quick, text="自定义…", width=8,
                  command=self._pick_datetime).pack(side="left", padx=2)
        tk.Button(quick, text="清除", width=6,
                  command=self._clear_remind).pack(side="left", padx=2)

        tk.Button(f, text="发布", command=self._act_publish,
                  bg="#4C6FFF", fg="white", relief="flat", height=2
                  ).grid(row=10, column=0, columnspan=3, sticky="ew", padx=8, pady=12)

        f.columnconfigure(0, weight=1)
        self._remind_at = 0
        self._reload_publish_targets()

    def _reload_publish_targets(self) -> None:
        labels = ["本地私有（仅自己看到）"]
        for g in self.groups_cache:
            labels.append(f"群「{g['name']}」 #{g['code']} · {g.get('member_count', 0)}人")
        self.cbo_target["values"] = labels
        self.cbo_target.current(0)

    def _quick_remind(self, minutes: int) -> None:
        self._remind_at = int((time.time() + minutes * 60) * 1000)
        self.var_remind.set(datetime.fromtimestamp(self._remind_at / 1000).strftime("%Y-%m-%d %H:%M"))

    def _quick_tomorrow(self) -> None:
        t = datetime.now().replace(hour=9, minute=0, second=0, microsecond=0) + timedelta(days=1)
        self._remind_at = int(t.timestamp() * 1000)
        self.var_remind.set(t.strftime("%Y-%m-%d %H:%M"))

    def _clear_remind(self) -> None:
        self._remind_at = 0
        self.var_remind.set("未设置")

    def _pick_datetime(self) -> None:
        default = datetime.now().strftime("%Y-%m-%d %H:%M")
        s = simpledialog.askstring("自定义时间", "格式 YYYY-MM-DD HH:MM", initialvalue=default, parent=self.root)
        if not s:
            return
        try:
            dt = datetime.strptime(s.strip(), "%Y-%m-%d %H:%M")
        except ValueError:
            messagebox.showerror("格式错误", "请按 YYYY-MM-DD HH:MM 输入", parent=self.root)
            return
        self._remind_at = int(dt.timestamp() * 1000)
        self.var_remind.set(dt.strftime("%Y-%m-%d %H:%M"))

    def _act_publish(self) -> None:
        title = self.ent_title.get().strip()
        content = self.txt_content.get("1.0", "end").strip()
        if not title:
            messagebox.showwarning("提示", "请填写标题", parent=self.root)
            return
        idx = self.cbo_target.current()
        urgent = self.var_urgent.get()
        if idx <= 0 or (idx - 1) >= len(self.groups_cache):
            add_local_task(title, content, self._remind_at, urgent)
        else:
            g = self.groups_cache[idx - 1]
            try:
                r = API.publish(g["code"], title, content, self._remind_at, urgent)
                upsert_group_task(int(r["id"]), title, content, self._remind_at,
                                  CONFIG["nickname"], g["code"], urgent)
            except Exception as e:
                messagebox.showerror("发布失败", str(e), parent=self.root)
                return
        self.ent_title.delete(0, "end")
        self.txt_content.delete("1.0", "end")
        self._clear_remind()
        self.var_urgent.set(False)
        self.refresh_tasks()
        messagebox.showinfo("已发布", "任务已发布", parent=self.root)

    # -------- 群 --------
    def _build_groups(self) -> None:
        f = self.tab_groups
        top = tk.Frame(f)
        top.pack(fill="x", padx=4, pady=4)
        tk.Button(top, text="刷新", command=self.refresh_groups).pack(side="left")
        tk.Button(top, text="新建群", command=self._act_new_group).pack(side="left", padx=4)
        tk.Button(top, text="加入群", command=self._act_join_group).pack(side="left")

        cols = ("name", "code", "members")
        self.tv_groups = ttk.Treeview(f, columns=cols, show="headings")
        self.tv_groups.heading("name", text="群名")
        self.tv_groups.heading("code", text="群号")
        self.tv_groups.heading("members", text="成员数")
        self.tv_groups.column("name", width=260)
        self.tv_groups.column("code", width=120)
        self.tv_groups.column("members", width=80, anchor="center")
        self.tv_groups.pack(fill="both", expand=True, padx=4, pady=4)

    def refresh_groups(self) -> None:
        if not CONFIG.get("server_url"):
            return
        def work():
            try:
                gs = API.my_groups()
            except Exception:
                gs = []
            def apply():
                self.groups_cache = gs
                for iid in self.tv_groups.get_children():
                    self.tv_groups.delete(iid)
                for g in gs:
                    self.tv_groups.insert("", "end", values=(g["name"], g["code"], g.get("member_count", 0)))
                self._reload_publish_targets()
            run_on_ui(apply)
        threading.Thread(target=work, daemon=True).start()

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
        code = simpledialog.askstring("加入群", "输入 8 位群号", parent=self.root)
        if not code:
            return
        code = "".join(ch for ch in code if ch.isdigit())
        if len(code) != 8:
            messagebox.showwarning("提示", "群号应为 8 位数字", parent=self.root)
            return
        try:
            r = API.join_group(code)
            messagebox.showinfo("已加入", f"已加入「{r['name']}」", parent=self.root)
            self.refresh_groups()
        except Exception as e:
            messagebox.showerror("失败", str(e), parent=self.root)

    def _require_server(self) -> bool:
        if not CONFIG.get("server_url"):
            messagebox.showwarning("提示", "请先到「设置」页配置服务器地址", parent=self.root)
            return False
        return True

    # -------- 设置 --------
    def _build_settings(self) -> None:
        f = self.tab_settings
        tk.Label(f, text="服务器地址（例：http://192.168.1.10:8787）"
                 ).grid(row=0, column=0, sticky="w", padx=8, pady=(12, 2))
        self.ent_server = tk.Entry(f)
        self.ent_server.insert(0, CONFIG.get("server_url", ""))
        self.ent_server.grid(row=1, column=0, sticky="ew", padx=8)

        tk.Label(f, text="昵称").grid(row=2, column=0, sticky="w", padx=8, pady=(12, 2))
        self.ent_nick = tk.Entry(f)
        self.ent_nick.insert(0, CONFIG.get("nickname", ""))
        self.ent_nick.grid(row=3, column=0, sticky="ew", padx=8)

        tk.Label(f, text=f"设备 ID: {CONFIG['device_id']}", fg="#6B7280"
                 ).grid(row=4, column=0, sticky="w", padx=8, pady=(12, 2))

        btns = tk.Frame(f)
        btns.grid(row=5, column=0, sticky="ew", padx=8, pady=16)
        tk.Button(btns, text="保存并重连", command=self._save_settings,
                  bg="#4C6FFF", fg="white", relief="flat", width=14
                  ).pack(side="left")
        tk.Button(btns, text="连接测试", command=self._test_health, width=10
                  ).pack(side="left", padx=8)
        f.columnconfigure(0, weight=1)

    def _save_settings(self) -> None:
        CONFIG["server_url"] = self.ent_server.get().strip()
        CONFIG["nickname"] = self.ent_nick.get().strip() or "PC 用户"
        save_config(CONFIG)
        self.ws.stop()
        self.ws.start()
        self.refresh_groups()
        messagebox.showinfo("已保存", "配置已保存，正在重新连接…", parent=self.root)

    def _test_health(self) -> None:
        CONFIG["server_url"] = self.ent_server.get().strip()
        ok = API.health()
        if ok:
            messagebox.showinfo("连接测试", "✅ 服务器可达", parent=self.root)
        else:
            messagebox.showerror("连接测试", "❌ 无法连接，请检查地址或后端进程", parent=self.root)

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

def main() -> None:
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

    def handle_event(evt: str, data: dict) -> None:
        if evt == "_open":
            run_on_ui(lambda: win and win.set_status("已连接"))
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

    if CONFIG.get("server_url"):
        ws.start()
    else:
        # 首次运行主动弹主窗口引导用户配置
        win.show()

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
