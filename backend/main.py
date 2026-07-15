"""
NoticeFloat 后端 v0.2
- 无鉴权装即用：8位群号 + 6位加入令牌
- 完全匿名：只用设备 UUID 识别
- REST + WebSocket 混合
"""
import asyncio
import json
import os
import secrets
import string
import sqlite3
import time
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, RedirectResponse
import re
from pydantic import BaseModel, Field

DB_PATH = Path(os.environ.get("NOTICEFLOAT_DB", str(Path(__file__).parent / "noticefloat.db")))

# ==================== 权限：硬编码超管 ====================
# v0.8.5 三级角色：super（硬编码）→ admin（超管发口令码升级）→ user（默认）
SUPER_ADMINS: set[str] = {
    # v0.8.10：手机端 device_id 从硬编码 super 中移除，改为通过 PC 发口令兑换成 admin，用来验证完整流程
    # "d-c6fab6138d9c4b469df0",   # 手机 BVL-AN16（曾经硬编码；已移除）
    "pc-1c80d2beeb934319",       # PC zhangkai_b（保留：唯一 super，用于发口令）
}

# ==================== 数据库 ====================

@contextmanager
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()

def init_db():
    with db() as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS groups (
                code TEXT PRIMARY KEY,        -- 8 位数字
                token TEXT NOT NULL,           -- 6 位大写字母数字混合
                name TEXT NOT NULL,
                created_by TEXT NOT NULL,      -- device_id
                created_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS members (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_code TEXT NOT NULL,
                device_id TEXT NOT NULL,
                nickname TEXT NOT NULL,
                joined_at INTEGER NOT NULL,
                UNIQUE(group_code, device_id),
                FOREIGN KEY(group_code) REFERENCES groups(code) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_members_device ON members(device_id);

            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_code TEXT NOT NULL,
                publisher_device TEXT NOT NULL,
                publisher_name TEXT NOT NULL,
                title TEXT NOT NULL,
                content TEXT NOT NULL DEFAULT '',
                remind_at INTEGER NOT NULL DEFAULT 0,
                urgent INTEGER NOT NULL DEFAULT 0,
                created_at INTEGER NOT NULL,
                FOREIGN KEY(group_code) REFERENCES groups(code) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_tasks_group ON tasks(group_code);

            CREATE TABLE IF NOT EXISTS task_receipts (
                task_id INTEGER NOT NULL,
                device_id TEXT NOT NULL,
                status INTEGER NOT NULL DEFAULT 0,   -- 0 待办 / 1 完成
                acked_at INTEGER,
                PRIMARY KEY(task_id, device_id),
                FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE
            );

            -- v0.8.5 管理员：admin_codes 一次性口令码；admins 记录已授权 device
            CREATE TABLE IF NOT EXISTS admin_codes (
                code TEXT PRIMARY KEY,
                created_by TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                expires_at INTEGER NOT NULL,      -- 毫秒；到期前可兑换
                note TEXT NOT NULL DEFAULT '',
                used_by TEXT,                     -- 被谁兑换（未兑换为 NULL）
                used_at INTEGER
            );

            CREATE TABLE IF NOT EXISTS admins (
                device_id TEXT PRIMARY KEY,
                nickname TEXT NOT NULL DEFAULT '',
                granted_by TEXT NOT NULL,         -- 超管的 device_id
                granted_at INTEGER NOT NULL,
                expires_at INTEGER NOT NULL       -- 毫秒；到期自动降为 user
            );

            -- v0.8.5 周期性公告
            CREATE TABLE IF NOT EXISTS recurring_tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                creator_device TEXT NOT NULL,
                creator_name TEXT NOT NULL,
                title TEXT NOT NULL,
                content TEXT NOT NULL DEFAULT '',
                urgent INTEGER NOT NULL DEFAULT 0,
                target TEXT NOT NULL,               -- '*' 广播 / '<4位群号>' 群
                freq TEXT NOT NULL,                 -- daily / weekly / monthly
                weekdays TEXT NOT NULL DEFAULT '',  -- weekly: '0,2,4' (Mon=0)
                monthdays TEXT NOT NULL DEFAULT '', -- monthly: '1,15,28'
                hh INTEGER NOT NULL,
                mm INTEGER NOT NULL,
                active INTEGER NOT NULL DEFAULT 1,
                last_fired_key TEXT NOT NULL DEFAULT '',
                created_at INTEGER NOT NULL
            );
            """
        )
        # 广播占位群：tasks.group_code='*' 外键指向它，必须存在
        c.execute(
            "INSERT OR IGNORE INTO groups(code, token, name, created_by, created_at) VALUES(?,?,?,?,?)",
            ("*", "*", "广播", "system", now_ms()),
        )
        # v0.8.6 预置示例周期公告（首次启动才插；已删除则不再自动插回）
        exists = c.execute("SELECT COUNT(*) FROM recurring_tasks").fetchone()[0]
        preset = c.execute("SELECT value FROM app_meta WHERE key='preset_recurring_done'" ).fetchone() \
                 if c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='app_meta'").fetchone() else None
        c.execute("CREATE TABLE IF NOT EXISTS app_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        # v0.8.12 迁移：recurring_tasks 增加 end_at 列（0 = 无结束时间）
        cols = [r[1] for r in c.execute("PRAGMA table_info(recurring_tasks)").fetchall()]
        if "end_at" not in cols:
            c.execute("ALTER TABLE recurring_tasks ADD COLUMN end_at INTEGER NOT NULL DEFAULT 0")
        done = c.execute("SELECT value FROM app_meta WHERE key='preset_recurring_done'").fetchone()
        if not done and exists == 0:
            now = now_ms()
            samples = [
                ("system", "系统", "☀️ 早会打卡", "9:15 之前完成打卡", 0, "*", "daily", "", "", 9, 0),
                ("system", "系统", "🍱 别忘了吃午饭", "起来动一动，眼睛也休息一下", 0, "*", "weekly", "0,1,2,3,4", "", 12, 0),
                ("system", "系统", "🧾 周五写周报", "本周完成事项/下周计划/风险", 0, "*", "weekly", "4", "", 16, 30),
            ]
            for s in samples:
                c.execute(
                    "INSERT INTO recurring_tasks(creator_device,creator_name,title,content,urgent,target,freq,weekdays,monthdays,hh,mm,active,last_fired_key,created_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,1,'',?)",
                    (*s, now),
                )
            c.execute("INSERT OR REPLACE INTO app_meta(key,value) VALUES('preset_recurring_done','1')")

# ==================== Pydantic 模型 ====================

class CreateGroupReq(BaseModel):
    device_id: str = Field(min_length=8, max_length=64)
    nickname: str = Field(min_length=1, max_length=32)
    group_name: str = Field(min_length=1, max_length=48)

class CreateGroupResp(BaseModel):
    code: str
    token: str
    invite: str
    name: str

class JoinGroupReq(BaseModel):
    device_id: str = Field(min_length=8, max_length=64)
    nickname: str = Field(min_length=1, max_length=32)
    token: str = ""          # 已保留字段但不再校验（完全无鉴权）

class PublishTaskReq(BaseModel):
    device_id: str
    title: str = Field(min_length=1, max_length=100)
    content: str = Field(default="", max_length=500)
    remind_at: int = 0            # 毫秒时间戳，0 表示立即
    urgent: bool = False          # true = 强弹窗模式

class BroadcastReq(BaseModel):
    """v0.8.3 广播：发给所有 device（不区分群）"""
    device_id: str
    nickname: str = "系统广播"
    title: str = Field(min_length=1, max_length=100)
    content: str = Field(default="", max_length=500)
    remind_at: int = 0
    urgent: bool = False

class AckReq(BaseModel):
    device_id: str

class AdminCodeReq(BaseModel):
    device_id: str            # 超管的 device_id
    expires_hours: int = 24   # 口令码有效期（旧字段，兼容）
    expires_days: int = 0     # v0.8.10 新增：若 > 0 则以天为单位，优先于 expires_hours
    note: str = ""

class AdminRedeemReq(BaseModel):
    device_id: str
    nickname: str = ""
    code: str                 # 8 位字母数字口令码

class RecurringReq(BaseModel):
    device_id: str            # 创建者，需为 super 或 admin
    nickname: str = ""
    title: str = ""           # v0.8.12 允许为空（自动从 content 取前 20 字）
    content: str = ""
    urgent: bool = False
    target: str               # '*' 广播 / '<4位群号>' 群
    freq: str                 # daily / weekly / monthly
    weekdays: list[int] = []  # 0-6 (Mon=0)
    monthdays: list[int] = [] # 1-31
    hh: int = Field(ge=0, le=23)
    mm: int = Field(ge=0, le=59)
    end_at: int = 0           # v0.8.12 结束时间（毫秒）；0 = 永不结束

class RecurringPatchReq(BaseModel):
    device_id: str
    active: Optional[bool] = None

# ==================== WebSocket 广播 ====================

class Hub:
    def __init__(self):
        # device_id -> set[WebSocket]
        self._conns: dict[str, set[WebSocket]] = {}
        self._lock = asyncio.Lock()

    async def register(self, device_id: str, ws: WebSocket):
        async with self._lock:
            self._conns.setdefault(device_id, set()).add(ws)

    async def unregister(self, device_id: str, ws: WebSocket):
        async with self._lock:
            s = self._conns.get(device_id)
            if s:
                s.discard(ws)
                if not s:
                    self._conns.pop(device_id, None)

    async def send_to(self, device_id: str, payload: dict):
        conns = list(self._conns.get(device_id, ()))
        for ws in conns:
            try:
                await ws.send_text(json.dumps(payload, ensure_ascii=False))
            except Exception:
                await self.unregister(device_id, ws)

hub = Hub()

# ==================== Helpers ====================

def now_ms() -> int:
    return int(time.time() * 1000)

def gen_code() -> str:
    # 4 位数字群号（用户体验优先，10000 种够小群使用）
    return "".join(secrets.choice(string.digits) for _ in range(4))

def gen_token() -> str:
    alphabet = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(6))

def gen_admin_code() -> str:
    # 8 位大写字母数字，避开易混字符 0/O/1/I
    alphabet = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
    return "".join(secrets.choice(alphabet) for _ in range(8))

def role_of(device_id: str) -> tuple[str, Optional[int]]:
    """返回 (role, expires_at)。role ∈ {'super','admin','user'}"""
    if device_id in SUPER_ADMINS:
        return "super", None
    with db() as c:
        row = c.execute(
            "SELECT expires_at FROM admins WHERE device_id=?", (device_id,)
        ).fetchone()
        if row and row["expires_at"] > now_ms():
            return "admin", int(row["expires_at"])
    return "user", None

def require_super(device_id: str):
    if device_id not in SUPER_ADMINS:
        raise HTTPException(403, "仅超管可执行此操作")

def require_admin(device_id: str):
    """super 或 admin 都放行"""
    role, _ = role_of(device_id)
    if role not in ("super", "admin"):
        raise HTTPException(403, "仅管理员/超管可执行此操作")

def group_members(conn, code: str) -> list[sqlite3.Row]:
    return list(conn.execute(
        "SELECT * FROM members WHERE group_code = ?", (code,)
    ).fetchall())

async def broadcast_new_task(code: str, task_id: int):
    with db() as c:
        row = c.execute(
            "SELECT * FROM tasks WHERE id = ?", (task_id,)
        ).fetchone()
        if code == "*":
            # 广播：发给所有已知 device（distinct）
            members = list(c.execute(
                "SELECT DISTINCT device_id FROM members"
            ).fetchall())
        else:
            members = group_members(c, code)
    if not row:
        return
    payload = {
        "type": "task.new",
        "task": {
            "id": row["id"],
            "group_code": row["group_code"],
            "publisher_name": row["publisher_name"],
            "title": row["title"],
            "content": row["content"],
            "remind_at": row["remind_at"],
            "urgent": bool(row["urgent"]) if "urgent" in row.keys() else False,
            "created_at": row["created_at"],
        }
    }
    for m in members:
        if m["device_id"] == row["publisher_device"]:
            continue
        await hub.send_to(m["device_id"], payload)

async def broadcast_task_done(code: str, task_id: int, by_device: str, by_name: str):
    payload = {
        "type": "task.done",
        "task_id": task_id,
        "by_device": by_device,
        "by_name": by_name,
    }
    with db() as c:
        members = group_members(c, code)
    for m in members:
        await hub.send_to(m["device_id"], payload)

# ==================== 应用 ====================

# v0.8.15 后台线程：自动同步 cpolar tunnel URL 到 GitHub server_url.txt
# 免费版 cpolar 每天变 URL 太麻烦，改由 backend 定时监控本地 cpolar log，
# URL 变了自动 write server_url.txt + git add/commit/push；
# 客户端只从 GitHub raw 拉 server_url.txt，无需重打包。
_REPO_ROOT = Path(__file__).resolve().parent.parent
_CPOLAR_LOG_DIR = _REPO_ROOT / "cpolar"
_SERVER_URL_FILE = _REPO_ROOT / "server_url.txt"


def _extract_latest_cpolar_url() -> Optional[str]:
    """从最近的 cpolar log 中提取最新一次 Tunnel established 的 https URL。
    v0.8.15.1 只读文件末尾 128KB，避免 log 变很大时全量入内存。"""
    import re as _re
    pattern = _re.compile(r'Tunnel established at (https://[a-z0-9]+\.r[0-9]\.cpolar\.cn)')
    TAIL_BYTES = 128 * 1024
    try:
        logs = sorted(_CPOLAR_LOG_DIR.glob("cpolar.log*"),
                      key=lambda p: p.stat().st_mtime, reverse=True)
    except Exception:
        return None
    for lf in logs[:3]:
        try:
            with open(lf, "rb") as f:
                size = lf.stat().st_size
                if size > TAIL_BYTES:
                    f.seek(-TAIL_BYTES, 2)
                content = f.read().decode("utf-8", errors="ignore")
        except Exception:
            continue
        matches = pattern.findall(content)
        if matches:
            return matches[-1]
    return None


def _sync_cpolar_url_loop():
    """后台线程：每 5 分钟检查一次；URL 变了 → 更新 server_url.txt + git push"""
    import subprocess as _sp
    while True:
        try:
            new_url = _extract_latest_cpolar_url()
            if new_url and _SERVER_URL_FILE.exists():
                current = _SERVER_URL_FILE.read_text(encoding="utf-8").strip()
                if new_url != current:
                    print(f"[url-sync] cpolar URL changed: {current} -> {new_url}", flush=True)
                    _SERVER_URL_FILE.write_text(new_url + "\n", encoding="utf-8")
                    try:
                        _sp.run(["git", "-C", str(_REPO_ROOT), "add", "server_url.txt"],
                                check=True, timeout=10, capture_output=True)
                        _sp.run(["git", "-C", str(_REPO_ROOT), "commit", "--no-verify",
                                 "-m", f"chore(auto): cpolar URL -> {new_url}"],
                                check=True, timeout=10, capture_output=True)
                        r = _sp.run(["git", "-C", str(_REPO_ROOT), "push", "origin", "main"],
                                    timeout=30, capture_output=True, text=True)
                        if r.returncode == 0:
                            print(f"[url-sync] git push OK", flush=True)
                        else:
                            print(f"[url-sync] git push failed: {r.stderr.strip()[:200]}", flush=True)
                    except _sp.CalledProcessError as e:
                        # commit 可能因"nothing to commit"失败，忽略
                        err = (e.stderr.decode(errors="ignore") if e.stderr else "").strip()[:200]
                        print(f"[url-sync] git op failed (rc={e.returncode}): {err}", flush=True)
                    except Exception as e:
                        print(f"[url-sync] git op error: {e}", flush=True)
        except Exception as e:
            print(f"[url-sync] loop error: {e}", flush=True)
        time.sleep(300)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    task = asyncio.create_task(scheduler_loop())
    # v0.8.15 启动 cpolar URL 自动同步线程
    import threading as _th
    _th.Thread(target=_sync_cpolar_url_loop, daemon=True, name="cpolar-url-sync").start()
    print("[url-sync] background thread started (checks every 5min)", flush=True)
    try:
        yield
    finally:
        task.cancel()

app = FastAPI(title="NoticeFloat Backend", version="0.2.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)

# v0.8.6 APK 静态托管：把 apk 放到 backend/downloads/ 下即可
DOWNLOAD_DIR = Path(__file__).parent / "downloads"
DOWNLOAD_DIR.mkdir(exist_ok=True)
app.mount("/downloads", StaticFiles(directory=str(DOWNLOAD_DIR)), name="downloads")

@app.get("/api/health")
def health():
    return {"ok": True, "ts": now_ms()}

# -------- 群管理 --------

MAX_GROUPS = 20

@app.post("/api/groups", response_model=CreateGroupResp)
def create_group(req: CreateGroupReq):
    with db() as c:
        # v0.8.15 全局群数量上限（不计入广播伪群 '*'）
        total = c.execute("SELECT COUNT(*) AS n FROM groups WHERE code != '*'").fetchone()["n"]
        if total >= MAX_GROUPS:
            raise HTTPException(400, f"群总数已达上限（{MAX_GROUPS} 个），请联系管理员清理后再试")
        # 生成不冲突的 code（4 位数字空间 1 万，重试 20 次）
        for _ in range(20):
            code = gen_code()
            if not c.execute("SELECT 1 FROM groups WHERE code=?", (code,)).fetchone():
                break
        else:
            raise HTTPException(500, "无法生成群号")
        token = gen_token()
        c.execute(
            "INSERT INTO groups(code, token, name, created_by, created_at) VALUES(?,?,?,?,?)",
            (code, token, req.group_name, req.device_id, now_ms()),
        )
        # 创建者自动入群
        c.execute(
            "INSERT INTO members(group_code, device_id, nickname, joined_at) VALUES(?,?,?,?)",
            (code, req.device_id, req.nickname, now_ms()),
        )
    return CreateGroupResp(code=code, token=token, invite=f"{code}-{token}", name=req.group_name)

@app.post("/api/groups/{code}/join")
def join_group(code: str, req: JoinGroupReq):
    with db() as c:
        g = c.execute("SELECT * FROM groups WHERE code=?", (code,)).fetchone()
        if not g:
            # 尝试当作管理员口令码兑换（口令码为 8 位大写字母数字）
            code_up = code.upper()
            ac = c.execute("SELECT * FROM admin_codes WHERE code=?", (code_up,)).fetchone()
            if ac:
                if ac["used_by"]:
                    raise HTTPException(400, "该口令码已被兑换")
                if ac["expires_at"] < now_ms():
                    raise HTTPException(400, "该口令码已过期")
                c.execute(
                    "INSERT INTO admins(device_id, nickname, granted_by, granted_at, expires_at) "
                    "VALUES(?,?,?,?,?) ON CONFLICT(device_id) DO UPDATE SET "
                    "nickname=excluded.nickname, granted_by=excluded.granted_by, "
                    "granted_at=excluded.granted_at, expires_at=excluded.expires_at",
                    (req.device_id, req.nickname, ac["created_by"], now_ms(), ac["expires_at"]),
                )
                c.execute(
                    "UPDATE admin_codes SET used_by=?, used_at=? WHERE code=?",
                    (req.device_id, now_ms(), code_up),
                )
                return {"code": code_up, "name": "🔑 管理员口令", "nickname": req.nickname,
                        "role": "admin", "expires_at": ac["expires_at"]}
            raise HTTPException(404, "群号不存在（如果输入的是管理员口令码，请检查是否过期或已被兑换）")
        # v0.2 起完全无鉴权：不再校验 token
        try:
            c.execute(
                "INSERT INTO members(group_code, device_id, nickname, joined_at) "
                "VALUES(?,?,?,?) ON CONFLICT(group_code, device_id) DO UPDATE SET nickname=excluded.nickname",
                (code, req.device_id, req.nickname, now_ms()),
            )
        except sqlite3.IntegrityError as e:
            raise HTTPException(400, str(e))
        return {"code": code, "name": g["name"], "nickname": req.nickname}

@app.get("/api/devices/{device_id}/groups")
def list_my_groups(device_id: str):
    with db() as c:
        rows = c.execute(
            """
            SELECT g.code, g.name, m.nickname, m.joined_at,
                   (SELECT COUNT(*) FROM members WHERE group_code=g.code) AS member_count
            FROM groups g JOIN members m ON m.group_code = g.code
            WHERE m.device_id = ?
            ORDER BY m.joined_at DESC
            """,
            (device_id,),
        ).fetchall()
    return {"groups": [dict(r) for r in rows]}

@app.get("/api/groups/discover")
def discover_groups(device_id: str = ""):
    """v0.8.15 列出全部群 + 我是否已加入。总量上限 MAX_GROUPS。
    宗旨：装上即用，不做鉴权门槛。任何知道后端地址的客户端都能列群+加入。
    （风险已知：backend URL = cpolar tunnel URL，语义上属半公开）"""
    with db() as c:
        rows = c.execute(
            """
            SELECT g.code, g.name, g.created_by, g.created_at,
                   (SELECT nickname FROM members WHERE group_code=g.code AND device_id=g.created_by) AS creator_name,
                   (SELECT COUNT(*) FROM members WHERE group_code=g.code) AS member_count,
                   CASE WHEN EXISTS(
                       SELECT 1 FROM members WHERE group_code=g.code AND device_id=?
                   ) THEN 1 ELSE 0 END AS joined
            FROM groups g
            WHERE g.code != '*'
            ORDER BY g.created_at DESC
            """,
            (device_id,),
        ).fetchall()
    return {
        "groups": [dict(r) for r in rows],
        "total": len(rows),
        "max": MAX_GROUPS,
    }

@app.post("/api/groups/{code}/leave")
def leave_group(code: str, req: AckReq):
    with db() as c:
        c.execute(
            "DELETE FROM members WHERE group_code=? AND device_id=?",
            (code, req.device_id),
        )
    return {"ok": True}


@app.delete("/api/groups/{code}")
def delete_group(code: str, device_id: str = ""):
    """v0.8.15.1 删群（仅超管可调用）。级联清 members/tasks/messages/recurring_tasks 等。
    用于 MAX_GROUPS 到顶后的自助清理。"""
    if not device_id:
        raise HTTPException(400, "缺少 device_id")
    require_super(device_id)
    if code == "*":
        raise HTTPException(400, "不可删除广播群")
    with db() as c:
        g = c.execute("SELECT code, name FROM groups WHERE code=?", (code,)).fetchone()
        if not g:
            raise HTTPException(404, "群号不存在")
        # 外键 CASCADE 会自动清 members/tasks/task_acks/recurring_tasks/messages
        c.execute("DELETE FROM groups WHERE code=?", (code,))
    return {"ok": True, "code": code, "name": g["name"]}

# -------- 任务 --------

def _assert_member(conn, code: str, device_id: str) -> sqlite3.Row:
    m = conn.execute(
        "SELECT * FROM members WHERE group_code=? AND device_id=?",
        (code, device_id),
    ).fetchone()
    if not m:
        raise HTTPException(403, "非群成员")
    return m

@app.post("/api/groups/{code}/tasks")
async def publish_task(code: str, req: PublishTaskReq):
    with db() as c:
        m = _assert_member(c, code, req.device_id)
        cur = c.execute(
            "INSERT INTO tasks(group_code, publisher_device, publisher_name, title, content, remind_at, urgent, created_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (code, req.device_id, m["nickname"], req.title, req.content, req.remind_at, 1 if req.urgent else 0, now_ms()),
        )
        task_id = cur.lastrowid
    await broadcast_new_task(code, task_id)
    return {"id": task_id}

@app.post("/api/broadcast")
async def publish_broadcast(req: BroadcastReq):
    """v0.8.3 广播任务：不需要加群，所有 device 都会收到"""
    with db() as c:
        cur = c.execute(
            "INSERT INTO tasks(group_code, publisher_device, publisher_name, title, content, remind_at, urgent, created_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            ("*", req.device_id, req.nickname, req.title, req.content, req.remind_at, 1 if req.urgent else 0, now_ms()),
        )
        task_id = cur.lastrowid
    await broadcast_new_task("*", task_id)
    return {"id": task_id, "group_code": "*"}

@app.get("/api/groups/{code}/tasks")
def list_tasks(code: str, since: int = 0, limit: int = 100):
    with db() as c:
        rows = c.execute(
            """
            SELECT * FROM tasks
            WHERE group_code = ? AND created_at > ?
            ORDER BY created_at DESC LIMIT ?
            """,
            (code, since, limit),
        ).fetchall()
    return {"tasks": [dict(r) for r in rows]}

@app.get("/api/devices/{device_id}/unread")
def list_unread(device_id: str, since: int = 0, limit: int = 200):
    """
    亮屏/重连时拉取未读：
    - 该 device 所有已加群中，created_at > since
    - 且发布者不是自己（自己发的不需要再回推）
    - 且该 device 未 done（task_receipts.status != 1）
    """
    with db() as c:
        rows = c.execute(
            """
            SELECT DISTINCT t.*
            FROM tasks t
            LEFT JOIN members m ON m.group_code = t.group_code
            LEFT JOIN task_receipts r
                   ON r.task_id = t.id AND r.device_id = ?
            WHERE (m.device_id = ? OR t.group_code = '*')
              AND t.created_at > ?
              AND t.publisher_device != ?
              AND COALESCE(r.status, 0) != 1
            ORDER BY t.created_at DESC
            LIMIT ?
            """,
            (device_id, device_id, since, device_id, limit),
        ).fetchall()
    return {"tasks": [dict(r) for r in rows]}

@app.post("/api/tasks/{task_id}/done")
async def mark_done(task_id: int, req: AckReq):
    with db() as c:
        t = c.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not t:
            raise HTTPException(404, "任务不存在")
        m = _assert_member(c, t["group_code"], req.device_id)
        c.execute(
            "INSERT INTO task_receipts(task_id, device_id, status, acked_at) "
            "VALUES(?,?,1,?) ON CONFLICT(task_id, device_id) DO UPDATE SET status=1, acked_at=excluded.acked_at",
            (task_id, req.device_id, now_ms()),
        )
    await broadcast_task_done(t["group_code"], task_id, req.device_id, m["nickname"])
    return {"ok": True}

@app.get("/api/tasks/{task_id}/receipts")
def list_receipts(task_id: int):
    with db() as c:
        rows = c.execute(
            """
            SELECT m.nickname, m.device_id, COALESCE(r.status, 0) AS status, r.acked_at
            FROM members m
            JOIN tasks t ON t.group_code = m.group_code
            LEFT JOIN task_receipts r ON r.task_id = t.id AND r.device_id = m.device_id
            WHERE t.id = ?
            """,
            (task_id,),
        ).fetchall()
    return {"receipts": [dict(r) for r in rows]}

# -------- WebSocket --------

@app.websocket("/ws/{device_id}")
async def ws_endpoint(ws: WebSocket, device_id: str):
    await ws.accept()
    await hub.register(device_id, ws)
    try:
        # 心跳循环
        while True:
            msg = await ws.receive_text()
            if msg == "ping":
                await ws.send_text("pong")
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        await hub.unregister(device_id, ws)


# ==================== 管理员 endpoints ====================

@app.get("/api/role/{device_id}")
def get_role(device_id: str):
    role, expires_at = role_of(device_id)
    return {"role": role, "expires_at": expires_at,
            "is_super": device_id in SUPER_ADMINS}

@app.post("/api/admin/codes")
def create_admin_code(req: AdminCodeReq):
    require_super(req.device_id)
    # v0.8.10：expires_days > 0 时以天为单位（优先）；否则回退 expires_hours
    if req.expires_days > 0:
        if req.expires_days > 3650:
            raise HTTPException(400, "有效期需在 1 ~ 3650 天之间")
        hours = req.expires_days * 24
    else:
        if req.expires_hours <= 0 or req.expires_hours > 24 * 3650:
            raise HTTPException(400, "有效期需在 1 小时 ~ 3650 天之间")
        hours = req.expires_hours
    code = gen_admin_code()
    exp = now_ms() + hours * 3600 * 1000
    with db() as c:
        c.execute(
            "INSERT INTO admin_codes(code, created_by, created_at, expires_at, note) "
            "VALUES(?,?,?,?,?)",
            (code, req.device_id, now_ms(), exp, req.note),
        )
    return {"code": code, "expires_at": exp, "note": req.note}

@app.get("/api/admin/codes")
def list_admin_codes(device_id: str):
    require_super(device_id)
    with db() as c:
        rows = c.execute(
            "SELECT * FROM admin_codes ORDER BY created_at DESC LIMIT 100"
        ).fetchall()
    return {"codes": [dict(r) for r in rows]}

@app.delete("/api/admin/codes/{code}")
def delete_admin_code(code: str, device_id: str):
    require_super(device_id)
    with db() as c:
        c.execute("DELETE FROM admin_codes WHERE code=?", (code.upper(),))
    return {"ok": True}

@app.get("/api/admin/admins")
def list_admins(device_id: str):
    require_super(device_id)
    with db() as c:
        rows = c.execute(
            "SELECT * FROM admins ORDER BY granted_at DESC"
        ).fetchall()
    return {"admins": [dict(r) for r in rows]}

@app.delete("/api/admin/admins/{admin_device_id}")
def revoke_admin(admin_device_id: str, device_id: str):
    require_super(device_id)
    with db() as c:
        c.execute("DELETE FROM admins WHERE device_id=?", (admin_device_id,))
    return {"ok": True}


# ==================== 周期性公告 ====================

def _fire_recurring(r: sqlite3.Row) -> Optional[int]:
    """把周期性公告发一次：insert tasks + broadcast。返回新 task_id。"""
    with db() as c:
        cur = c.execute(
            "INSERT INTO tasks(group_code, publisher_device, publisher_name, title, content, "
            "remind_at, urgent, created_at) VALUES(?,?,?,?,?,?,?,?)",
            (r["target"], r["creator_device"], r["creator_name"],
             r["title"], r["content"], 0, r["urgent"], now_ms()),
        )
        task_id = cur.lastrowid
    return task_id

async def scheduler_loop():
    """每 30 秒扫描一次 recurring_tasks，触发到点的公告。"""
    import datetime as _dt
    while True:
        try:
            now = _dt.datetime.now()
            now_ts_ms = int(now.timestamp() * 1000)
            key = now.strftime("%Y%m%d-%H%M")
            with db() as c:
                rows = c.execute(
                    "SELECT * FROM recurring_tasks WHERE active=1"
                ).fetchall()
            for r in rows:
                if r["last_fired_key"] == key:
                    continue
                # v0.8.12：结束时间到了就自动跳过（并置为 inactive 一次）
                end_at = r["end_at"] if "end_at" in r.keys() else 0
                if end_at and now_ts_ms > end_at:
                    with db() as c:
                        c.execute("UPDATE recurring_tasks SET active=0 WHERE id=?", (r["id"],))
                    continue
                if now.hour != r["hh"] or now.minute != r["mm"]:
                    continue
                # 判周期
                fire = False
                if r["freq"] == "daily":
                    fire = True
                elif r["freq"] == "weekly":
                    wdays = {int(x) for x in r["weekdays"].split(",") if x.strip().isdigit()}
                    fire = now.weekday() in wdays
                elif r["freq"] == "monthly":
                    mdays = {int(x) for x in r["monthdays"].split(",") if x.strip().isdigit()}
                    fire = now.day in mdays
                if not fire:
                    continue
                task_id = _fire_recurring(r)
                if task_id:
                    with db() as c:
                        c.execute("UPDATE recurring_tasks SET last_fired_key=? WHERE id=?",
                                  (key, r["id"]))
                    try:
                        await broadcast_new_task(r["target"], task_id)
                    except Exception:
                        pass
        except Exception as e:
            print(f"[scheduler] error: {e}")
        await asyncio.sleep(30)

@app.post("/api/recurring")
def create_recurring(req: RecurringReq):
    require_admin(req.device_id)
    if req.freq not in ("daily", "weekly", "monthly"):
        raise HTTPException(400, "freq 必须是 daily/weekly/monthly 之一")
    if req.freq == "weekly" and not req.weekdays:
        raise HTTPException(400, "weekly 必须指定 weekdays")
    if req.freq == "monthly" and not req.monthdays:
        raise HTTPException(400, "monthly 必须指定 monthdays")
    # v0.8.12 title 可留空：自动从 content 首行前 20 字生成
    title = (req.title or "").strip()
    if not title:
        first = (req.content or "").splitlines()[0].strip() if req.content else ""
        title = (first[:20] + ("…" if len(first) > 20 else "")) if first else "（无标题）"
    if req.end_at and req.end_at < now_ms():
        raise HTTPException(400, "end_at 已过期")
    # 校验 target
    if req.target != "*":
        with db() as c:
            g = c.execute("SELECT 1 FROM groups WHERE code=?", (req.target,)).fetchone()
            if not g:
                raise HTTPException(404, f"目标群 {req.target} 不存在")
    with db() as c:
        cur = c.execute(
            "INSERT INTO recurring_tasks(creator_device, creator_name, title, content, urgent, "
            "target, freq, weekdays, monthdays, hh, mm, active, last_fired_key, created_at, end_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,1,'',?,?)",
            (req.device_id, req.nickname, title, req.content, 1 if req.urgent else 0,
             req.target, req.freq,
             ",".join(str(x) for x in req.weekdays),
             ",".join(str(x) for x in req.monthdays),
             req.hh, req.mm, now_ms(), int(req.end_at or 0)),
        )
    return {"id": cur.lastrowid}

@app.get("/api/recurring")
def list_recurring(device_id: str):
    """admin/super 看全部；user 只能看自己创建的（其实 user 也创建不了）"""
    role, _ = role_of(device_id)
    with db() as c:
        if role in ("super", "admin"):
            rows = c.execute("SELECT * FROM recurring_tasks ORDER BY created_at DESC").fetchall()
        else:
            rows = c.execute(
                "SELECT * FROM recurring_tasks WHERE creator_device=? ORDER BY created_at DESC",
                (device_id,)
            ).fetchall()
    return {"items": [dict(r) for r in rows]}

@app.patch("/api/recurring/{rid}")
def patch_recurring(rid: int, req: RecurringPatchReq):
    require_admin(req.device_id)
    if req.active is None:
        raise HTTPException(400, "无可更新字段")
    with db() as c:
        c.execute("UPDATE recurring_tasks SET active=? WHERE id=?",
                  (1 if req.active else 0, rid))
    return {"ok": True}

@app.delete("/api/recurring/{rid}")
def delete_recurring(rid: int, device_id: str):
    require_admin(device_id)
    with db() as c:
        c.execute("DELETE FROM recurring_tasks WHERE id=?", (rid,))
    return {"ok": True}


# ==================== v0.8.6 自动升级 ====================

LATEST_APK_META = {
    "versionCode": 24,
    "versionName": "0.8.15.1",
    "fileName": "勇冠三军提醒器-v0.8.15.1.apk",
    "changelog": "v0.8.15.1：安全加固——server_url.txt 拉取加域名白名单，只接受 cpolar 域名，防止 GitHub 上被人换成恶意后端 URL"
}

@app.get("/api/latest_apk")
def latest_apk():
    """APK 启动时查询最新版本；latestCode > 本地 versionCode 就提示升级"""
    fn = LATEST_APK_META["fileName"]
    fp = DOWNLOAD_DIR / fn
    exists = fp.exists()
    size = fp.stat().st_size if exists else 0
    return {
        "versionCode": LATEST_APK_META["versionCode"],
        "versionName": LATEST_APK_META["versionName"],
        "url": f"/downloads/{fn}",
        "size": size,
        "available": exists,
        "changelog": LATEST_APK_META["changelog"],
    }


@app.get("/apk/latest")
def apk_latest_redirect():
    """v0.8.10 永久链接：始终 302 到当前 LATEST_APK_META 指向的 apk 文件。
    用于对外分享给群友，每次点击都拿到最新版本，无需修改链接。"""
    fn = LATEST_APK_META["fileName"]
    return RedirectResponse(url=f"/downloads/{fn}", status_code=302)


@app.get("/api/admin/stats")
def admin_stats(device_id: str):
    """v0.8.11 超管统计：给管理员管理界面看的一组数字。"""
    require_super(device_id)
    with db() as c:
        groups_cnt = c.execute("SELECT COUNT(*) FROM groups WHERE code!='*'").fetchone()[0]
        # 已知用户 = members / admins / tasks 发布者 三处去重
        rows = c.execute(
            "SELECT DISTINCT device_id FROM members "
            "UNION SELECT device_id FROM admins "
            "UNION SELECT publisher_device FROM tasks"
        ).fetchall()
        users_cnt = len({r[0] for r in rows if r[0]})
        admins_cnt = c.execute("SELECT COUNT(*) FROM admins WHERE expires_at > ?", (now_ms(),)).fetchone()[0]
        pending_tasks = c.execute("SELECT COUNT(*) FROM tasks WHERE created_at > ?", (now_ms() - 7*24*3600*1000,)).fetchone()[0]
        recurring_cnt = c.execute("SELECT COUNT(*) FROM recurring_tasks WHERE active=1").fetchone()[0]
    return {
        "users": users_cnt,               # 已知 device 总数（进过群/兑换过/发过任务）
        "supers": len(SUPER_ADMINS),      # 硬编码超管数
        "admins": admins_cnt,             # 有效期内的管理员数
        "groups": groups_cnt,             # 群组数（排除广播占位）
        "tasks_7d": pending_tasks,        # 近 7 天任务数
        "recurring_active": recurring_cnt, # 生效中周期公告
        "online": len(hub._conns),        # 当前 WS 在线设备数
    }


@app.get("/api/admin/users")
def admin_users(device_id: str):
    """v0.8.12 超管专用：所有已知用户列表 + 角色标记。"""
    require_super(device_id)
    now = now_ms()
    online_set = set(hub._conns.keys()) if hasattr(hub, "_conns") else set()
    with db() as c:
        # 收集所有 device_id 及最新昵称/最近活跃时间
        # 1) members
        rows_m = c.execute(
            "SELECT device_id, nickname, group_code, joined_at FROM members"
        ).fetchall()
        # 2) tasks（发布者）
        rows_t = c.execute(
            "SELECT publisher_device AS device_id, publisher_name AS nickname, "
            "created_at AS ts FROM tasks"
        ).fetchall()
        # 3) admins
        rows_a = c.execute(
            "SELECT device_id, nickname, expires_at FROM admins"
        ).fetchall()
        admins_map = {r["device_id"]: r["expires_at"] for r in rows_a}

    users: dict[str, dict] = {}

    def touch(dev: str, nick: str, ts: int) -> None:
        if not dev:
            return
        u = users.get(dev)
        if u is None:
            users[dev] = {
                "device_id": dev,
                "nickname": nick or "",
                "groups": set(),
                "tasks": 0,
                "last_seen": ts or 0,
            }
        else:
            if nick and (not u["nickname"] or (ts and ts >= u["last_seen"])):
                u["nickname"] = nick
            if ts and ts > u["last_seen"]:
                u["last_seen"] = ts

    for r in rows_m:
        touch(r["device_id"], r["nickname"], r["joined_at"])
        users[r["device_id"]]["groups"].add(r["group_code"])
    for r in rows_t:
        touch(r["device_id"], r["nickname"], r["ts"])
        users[r["device_id"]]["tasks"] += 1
    for r in rows_a:
        touch(r["device_id"], r["nickname"], 0)

    # 超管肯定要出现
    for sd in SUPER_ADMINS:
        touch(sd, "", 0)

    out = []
    for dev, u in users.items():
        if dev in SUPER_ADMINS:
            role = "超管"
        elif dev in admins_map and admins_map[dev] > now:
            role = "管理员"
        else:
            role = "普通"
        out.append({
            "device_id": dev,
            "nickname": u["nickname"],
            "role": role,
            "groups": len(u["groups"]),
            "tasks": u["tasks"],
            "last_seen": u["last_seen"],
            "admin_expires_at": admins_map.get(dev, 0),
            "online": dev in online_set,
        })
    # 排序：在线 > 角色（超管>管理员>普通） > last_seen desc
    role_rank = {"超管": 0, "管理员": 1, "普通": 2}
    out.sort(key=lambda x: (not x["online"], role_rank.get(x["role"], 3), -x["last_seen"]))
    return {"users": out, "total": len(out)}


LATEST_PC_META = {
    "versionCode": 16,
    "versionName": "0.8.15.1",
    "fileName": "勇冠三军提醒器PC-v0.8.15.1.exe",
    "changelog": "v0.8.15.1 安全加固：discover 需先入群才可发现新群；升级包新增 SHA256 校验；server_url.txt 域名白名单防篡改劇持；新增删群接口防 20 群卡死",
}


@app.get("/pc/latest")
def pc_latest_redirect():
    """v0.8.10 PC 版永久下载链接：302 到当前 LATEST_PC_META 指向的 exe。"""
    fn = LATEST_PC_META["fileName"]
    return RedirectResponse(url=f"/downloads/{fn}", status_code=302)


@app.get("/api/latest_pc")
def latest_pc():
    """v0.8.15 PC 版启动时查询最新版本，用于自动升级
    v0.8.15.1 加 sha256 字段，PC 端下载后校验，防止升级链路被中间人替换"""
    import hashlib as _hl
    fn = LATEST_PC_META["fileName"]
    fp = DOWNLOAD_DIR / fn
    sha256 = ""
    size = 0
    if fp.exists():
        size = fp.stat().st_size
        try:
            h = _hl.sha256()
            with open(fp, "rb") as f:
                for chunk in iter(lambda: f.read(1024 * 1024), b""):
                    h.update(chunk)
            sha256 = h.hexdigest()
        except Exception:
            sha256 = ""
    return {
        "versionCode": LATEST_PC_META["versionCode"],
        "versionName": LATEST_PC_META["versionName"],
        "fileName": fn,
        "url": f"/downloads/{fn}",
        "size": size,
        "sha256": sha256,
        "available": fp.exists(),
        "changelog": LATEST_PC_META["changelog"],
    }


# ==================== v0.8.6 自然语言 → 周期公告 ====================

_CN_NUM = {"零":0,"一":1,"二":2,"两":2,"三":3,"四":4,"五":5,"六":6,"七":7,"八":8,"九":9,"十":10}
_WEEKDAY_MAP = {"一":0,"二":1,"三":2,"四":3,"五":4,"六":5,"日":6,"天":6,"末":6}

def _cn_to_int(s: str) -> Optional[int]:
    if s.isdigit():
        return int(s)
    if s in _CN_NUM:
        return _CN_NUM[s]
    # 十X / X十 / X十Y
    if "十" in s:
        if s == "十":
            return 10
        if s.startswith("十"):
            return 10 + _CN_NUM.get(s[1], 0)
        if s.endswith("十"):
            return _CN_NUM.get(s[0], 0) * 10
        # X十Y
        parts = s.split("十")
        if len(parts) == 2:
            return _CN_NUM.get(parts[0], 0) * 10 + _CN_NUM.get(parts[1], 0)
    return None


def parse_recurring_text(text: str) -> dict:
    """把 '每周三19点50提醒所有人发赛车' 之类的中文，解析成 recurring_tasks 字段。
    返回 {ok, freq, weekdays, monthdays, hh, mm, target, title, content, error?}
    - 至少要能识别 时刻 + 频率 才算 ok；识别不出的字段用默认
    """
    raw = text
    t = text.strip()
    if not t:
        return {"ok": False, "error": "空文本"}

    # ---- 时刻 ----
    hh = None; mm = 0
    # 24小时格式 HH:MM
    m = re.search(r"(\d{1,2})\s*[:：]\s*(\d{1,2})", t)
    if m:
        hh, mm = int(m.group(1)), int(m.group(2))
    else:
        # X点Y分 / X点半 / X点
        # 支持中文数字：一二三四五六七八九十/十一/十二
        num_pat = r"(\d{1,2}|[零一二两三四五六七八九十]{1,3})"
        m = re.search(num_pat + r"\s*点\s*(半|" + num_pat + r"\s*分?)?", t)
        if m:
            hh = _cn_to_int(m.group(1))
            g2 = m.group(2)
            if g2 == "半":
                mm = 30
            elif g2:
                mm = _cn_to_int(m.group(3)) or 0
        else:
            # 早上/上午/中午/下午/晚上 8/9/10 点 等（无 分 也算）
            m = re.search(num_pat + r"\s*点", t)
            if m:
                hh = _cn_to_int(m.group(1))
                mm = 0

    # 时段调整：下午/晚上 X点 → X+12（X<12）
    if hh is not None:
        if re.search(r"(下午|晚上|傍晚|夜里|夜间|pm|PM)", t) and hh < 12:
            hh += 12
        if re.search(r"(上午|早上|凌晨|am|AM)", t) and hh == 12:
            hh = 0

    if hh is None or not (0 <= hh <= 23) or not (0 <= mm <= 59):
        return {"ok": False, "error": "无法识别时刻（示例：每周三 19:50 或 每天9点半）"}

    # ---- 频率 ----
    freq = "daily"
    weekdays = ""
    monthdays = ""

    if re.search(r"每天|每日|天天|逐日", t):
        freq = "daily"
    else:
        # 每周X / 每周X和Y
        wm = re.findall(r"周([一二三四五六日天末])", t)
        if wm:
            freq = "weekly"
            wset = sorted({_WEEKDAY_MAP[w] for w in wm if w in _WEEKDAY_MAP})
            weekdays = ",".join(str(x) for x in wset)
        else:
            # 每月N号 / 每月N日
            mm2 = re.findall(r"每?月\s*(\d{1,2}|[一二两三四五六七八九十]{1,3})\s*[号日]", t)
            if mm2:
                freq = "monthly"
                dset = sorted({_cn_to_int(x) for x in mm2 if _cn_to_int(x)})
                monthdays = ",".join(str(x) for x in dset if x)
            elif re.search(r"工作日", t):
                freq = "weekly"; weekdays = "0,1,2,3,4"
            elif re.search(r"周末", t):
                freq = "weekly"; weekdays = "5,6"

    # ---- 目标 ----
    target = "*"  # 默认广播
    m = re.search(r"群\s*(\d{4})", t)
    if m:
        target = m.group(1)
    elif re.search(r"仅自己|只自己|只我|只提醒我", t):
        target = "SELF"  # 前端应转为本地任务

    # ---- 紧急 ----
    urgent = 1 if re.search(r"紧急|强弹|重要|加急", t) else 0

    # ---- 标题 ----
    # 去掉时间/频率/目标部分，剩下当标题
    title = raw
    strip_patterns = [
        r"每天|每日|天天|逐日|每周[一二三四五六日天末](和[一二三四五六日天末])?|每?月\s*[0-9一二两三四五六七八九十]{1,3}\s*[号日]",
        r"工作日|周末",
        r"\d{1,2}\s*[:：]\s*\d{1,2}",
        r"[0-9零一二两三四五六七八九十]{1,3}\s*点\s*(半|[0-9零一二两三四五六七八九十]{1,3}\s*分?)?",
        r"(上午|下午|晚上|早上|凌晨|中午|傍晚|夜里|夜间)",
        r"提醒(所有人|大家|全部)?",
        r"通知(所有人|大家|全部)?",
        r"广播",
        r"群\d{4}",
        r"仅自己|只自己|只我|只提醒我",
        r"紧急|强弹|重要|加急",
    ]
    for p in strip_patterns:
        title = re.sub(p, "", title)
    title = title.strip(" ,，。；:：、-")
    if not title:
        title = raw[:30]

    return {
        "ok": True,
        "freq": freq,
        "weekdays": weekdays,
        "monthdays": monthdays,
        "hh": hh,
        "mm": mm,
        "target": target,
        "urgent": urgent,
        "title": title[:100],
        "content": "",
    }


class ParseRecurringReq(BaseModel):
    text: str

@app.post("/api/recurring/parse")
def api_parse_recurring(req: ParseRecurringReq):
    """PC/APK：把中文一句话解析成 recurring_tasks 字段。仅解析，不入库。"""
    return parse_recurring_text(req.text)


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8787))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
