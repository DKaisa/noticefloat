# NoticeFloat PC 版

跨设备任务/通知桌面客户端，与 Android 端共用后端。

## 直接运行（开发）

```powershell
cd pc
python -m venv .venv
.\.venv\Scripts\activate
pip install -i https://mirrors.aliyun.com/pypi/simple/ -r requirements.txt
python main.py
```

## 打包 exe

```powershell
cd pc
.\build.bat
# 产物: pc\dist\NoticeFloat.exe
```

## 首次使用

1. 启动后系统托盘出现 NF 图标（蓝色=无未读，红色=有未读）
2. 双击托盘图标 → 打开主窗口
3. 进入「设置」→ 填写服务器地址（例：`http://192.168.1.10:8787`）+ 昵称
4. 「群」页 → 新建/加入群（8 位群号）
5. 「发布」页 → 选择目标 + 是否强弹窗 → 发布

## 功能对齐

| 功能 | Android | PC |
|---|---|---|
| 群管理 | ✅ | ✅ |
| 待办列表 | ✅ | ✅ |
| 发布任务 | ✅ | ✅ |
| 强弹窗 | ✅（全屏遮罩） | ✅（TopMost 置顶窗口） |
| 悬浮球 | ✅ | ❌（用托盘替代） |
| 微信前台检测 | ✅ | ❌ |
| WebSocket 实时推送 | ✅ | ✅ |
| 本地私有任务 | ✅ | ✅ |

## 数据存储

- 配置：`%APPDATA%\NoticeFloat\config.json`
- 待办：SQLite `%APPDATA%\NoticeFloat\notice.db`
