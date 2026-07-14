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
from pydantic import BaseModel, Field

DB_PATH = Path(os.environ.get("NOTICEFLOAT_DB", str(Path(__file__).parent / "noticefloat.db")))

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
            """
        )

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

class AckReq(BaseModel):
    device_id: str

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
    return "".join(secrets.choice(string.digits) for _ in range(8))

def gen_token() -> str:
    alphabet = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(6))

def group_members(conn, code: str) -> list[sqlite3.Row]:
    return list(conn.execute(
        "SELECT * FROM members WHERE group_code = ?", (code,)
    ).fetchall())

async def broadcast_new_task(code: str, task_id: int):
    with db() as c:
        row = c.execute(
            "SELECT * FROM tasks WHERE id = ?", (task_id,)
        ).fetchone()
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
        # 不推送给发布者自己（避免服务端和本地都插入）
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

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield

app = FastAPI(title="NoticeFloat Backend", version="0.2.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)

@app.get("/api/health")
def health():
    return {"ok": True, "ts": now_ms()}

# -------- 群管理 --------

@app.post("/api/groups", response_model=CreateGroupResp)
def create_group(req: CreateGroupReq):
    with db() as c:
        # 生成不冲突的 code
        for _ in range(10):
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
            raise HTTPException(404, "群号不存在")
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

@app.post("/api/groups/{code}/leave")
def leave_group(code: str, req: AckReq):
    with db() as c:
        c.execute(
            "DELETE FROM members WHERE group_code=? AND device_id=?",
            (code, req.device_id),
        )
    return {"ok": True}

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


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8787))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
