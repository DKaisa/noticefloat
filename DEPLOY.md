# NoticeFloat 完全免费部署指南（Render + UptimeRobot）

> 长期成本 **¥0**，需要注册 3 个账号（GitHub + Render + UptimeRobot，全部邮箱注册）。
> 缺点：Render 平台偶尔重启时（一般几周一次）SQLite 数据会清空 → 大家重新拉一遍任务即可。

---

## 步骤 1｜准备 GitHub 仓库（约 5 分钟）

如果你还没 GitHub 账号：
1. 打开 https://github.com/signup → 用邮箱注册
2. 邮箱收验证码，填完即可

已有账号：直接下一步。

**把项目推上 GitHub：**

在 GitHub 网页点右上角 `+` → `New repository`：
- Repository name：`noticefloat`
- **Public**（免费用户 Render 只能读 public 仓库；如果非要 private 也行，多一步授权）
- 不勾任何 README/gitignore（我们本地已有）
- 点 `Create repository`

创建完后 GitHub 会显示一段命令，你在**这个项目根目录**（`d:\zhangkai_b\work\project\AI记录\NoticeFloat`）打开 PowerShell 跑：

```powershell
cd 'd:\zhangkai_b\work\project\AI记录\NoticeFloat'
git init
git add .
git commit -m "init"
git branch -M main
git remote add origin https://github.com/你的用户名/noticefloat.git
git push -u origin main
```

第一次 push 会弹窗让登录 GitHub —— 用浏览器登进去授权即可。

---

## 步骤 2｜Render 部署（约 3 分钟）

1. 打开 https://render.com → 右上角 `Get Started` → 用 **GitHub 账号登录**（一键授权，不用再注册）
2. 首页点 `+ New` → `Blueprint`
3. 选你刚推的 `noticefloat` 仓库 → 点 `Connect`
4. Render 会自动读取 `render.yaml`，显示要创建 1 个 web 服务 → 点 `Apply`
5. 等 3~5 分钟自动构建（进度条会走完）
6. 构建完成后进入服务详情页，**顶部会显示一个 URL**，形如：

   `https://noticefloat-backend-xxxx.onrender.com`

   把这个 URL **复制发我**，我把它焊进 APK 出 v0.4 版本。

---

## 步骤 3｜UptimeRobot 保活（约 3 分钟）— 让服务永不休眠

1. 打开 https://uptimerobot.com → 右上 `Register for FREE` → 邮箱注册
2. 邮箱收验证链接，点开即可
3. 登录后点 `+ New monitor`：
   - Monitor Type：`HTTP(s)`
   - Friendly Name：`NoticeFloat`
   - URL：**你 Render 的 URL 后面加 /api/health**，如 `https://noticefloat-backend-xxxx.onrender.com/api/health`
   - Monitoring Interval：`5 minutes`
   - 其他默认
4. 点 `Create Monitor` 完成 ✅

之后 UptimeRobot 会每 5 分钟 ping 一次你的服务，Render 检测到有请求就不会休眠。

---

## 步骤 4｜等 URL 就位后我出 APK v0.4

你把 Render URL 发我 → 我改 `app/build.gradle.kts` 里的 `defaultServerUrl` → 重编 → 交付 APK。

---

## FAQ

**Q：Render 数据清空频率？**
A：Render 平台重启很少见（一般几周才一次维护），另外你重新部署代码也会重启。数据是"当天/近几天的提醒任务" → 清了也就重发一遍。

**Q：GitHub 一定要 public 仓库吗？**
A：Render 免费版只能连 public 仓库。如果你介意（其实里面没敏感信息），可以后续升级 Render 或用其他 CI/CD。建议先 public。

**Q：UptimeRobot 免费限额？**
A：50 个监控 + 5 分钟间隔，你一个项目 1 个监控完全够用，永久免费。

**Q：Render URL 会变吗？**
A：不会。第一次部署时随机生成，之后永久固定（除非你手动删掉服务重建）。

**Q：想国内访问更快？**
A：Render Singapore 节点已经算国内友好。若嫌慢，可后续换阿里云函数计算 / 腾讯云 CloudBase（都有免费额度但需实名）。
