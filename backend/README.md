# NoticeFloat Backend v0.2

极简后端：无鉴权（匿名设备 UUID）+ 群号邀请制 + WebSocket 广播。

## 本地运行

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/
python main.py
# 监听 0.0.0.0:8787
```

## API 一览

| 方法 | 路径 | 用途 |
|---|---|---|
| GET  | `/api/health` | 存活检查 |
| POST | `/api/groups` | 建群，返回 code+token |
| POST | `/api/groups/{code}/join` | 加群 (需 token) |
| GET  | `/api/devices/{device_id}/groups` | 我加入的所有群 |
| POST | `/api/groups/{code}/leave` | 退群 |
| POST | `/api/groups/{code}/tasks` | 发任务 |
| GET  | `/api/groups/{code}/tasks` | 拉任务列表 |
| POST | `/api/tasks/{id}/done` | 标完成 |
| GET  | `/api/tasks/{id}/receipts` | 查回执 (谁完成了) |
| WS   | `/ws/{device_id}` | 实时推送通道 |

## 安全模型

- 无正规登录；所有请求用 `device_id`（客户端本地生成的 UUID）识别
- 群号 8 位数字 + 加入令牌 6 位字母数字，暴力破解不现实
- **建议部署到 HTTPS（wss）**，防中间人监听 device_id
- 不做速率限制（自用规模），公开部署前请加 nginx / cloudflare 兜底

## 部署选项

- **Zeabur / Railway / Fly.io** 免费额度足够小群使用
- **自建 VPS + docker**：`docker run -p 8787:8787 -v $(pwd)/data:/app` + nginx SSL
- **内网穿透 (frp/cpolar)** 快速验证

## Dockerfile（可选）

见 `Dockerfile`。
