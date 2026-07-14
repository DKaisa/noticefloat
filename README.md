# NoticeFloat v0.1 · Android 悬浮窗待办 MVP

> 目的：解决微信大群通知被聊天消息冲掉、@全体也被忽略的痛点。
> 本 v0.1 走**完全独立、零集成**方案：不读取微信内容，只用系统 API 判断微信是否在前台，然后弹出自己的悬浮球呈现待办。

## 功能（v0.1）

- 悬浮球常驻（可选：仅微信前台时显示 / 全程显示；有待办时始终显示）
- 点击悬浮球展开待办列表：
  - 单击一条 = 标记完成
  - 长按 = 删除
  - 一键"全部完成"
- 半屏发布器：标题 / 内容 / 快捷时间（30 分钟 / 1 小时 / 3 小时 / 明早 9 点）/ 自定义日期时间
- 到时提醒（AlarmManager + 高优先级通知）
- 本地 Room 数据库，纯离线，不联网
- 前台服务 + Ongoing 通知，抗系统杀

## 构建

1. 用 Android Studio Iguana+ 打开 `NoticeFloat/` 目录，等 Gradle Sync
2. 首次 sync 会下载 gradle-8.4（配置为腾讯镜像，国内快）
3. **需要手动补 `gradle/wrapper/gradle-wrapper.jar`**：从任意现有 AS 项目复制过来，或运行一次 `gradle wrapper --gradle-version 8.4`
4. 编译：`./gradlew assembleDebug`，产物在 `app/build/outputs/apk/debug/`

## 使用步骤

1. 安装 APK 后打开 App，按提示逐项授权：
   - **悬浮窗权限**（跳系统设置）
   - **使用情况访问**（用于判断微信前台，不读微信任何内容）
   - **通知权限**（Android 13+）
   - **精确闹钟**（Android 12+，用于到时提醒）
2. 勾选"仅在微信打开时显示悬浮球"（推荐）
3. 点"启动悬浮窗服务"
4. 打开微信，你会看到左侧一个圆点；退出微信自动隐藏
5. 点"发布一条任务" 试试从 App 内部发布

## 已知坑

- 华为/小米/OV 系机型可能杀前台服务，需要在系统设置里加"自启动 + 后台运行"白名单
- 无 `gradle-wrapper.jar` 时执行 `./gradlew` 会失败，见上"构建 第 3 步"
- iOS 平台不支持系统级悬浮窗，需另做方案（通知/灵动岛/快捷指令）

## 后续路线（不在 v0.1 范围）

- v0.2：微信"分享到"入口（长按微信消息 → 转发 → 送入本 App）
- v0.3：PC 端 Tauri 客户端 + 后端同步
- v0.4：NLP 自动识别时间（"明天下午 3 点开会" → 自动预约）
- v0.5：多群/多项目分组、协作看板

## 项目结构

```
NoticeFloat/
├── settings.gradle.kts
├── build.gradle.kts
├── gradle.properties
├── gradle/wrapper/gradle-wrapper.properties
└── app/
    ├── build.gradle.kts
    ├── proguard-rules.pro
    └── src/main/
        ├── AndroidManifest.xml
        ├── java/com/example/noticefloat/
        │   ├── NoticeApp.kt
        │   ├── data/          # Room 层
        │   ├── service/       # 悬浮窗前台服务 + 微信前台检测
        │   ├── reminder/      # 闹钟提醒 + 开机重启
        │   └── ui/            # MainActivity / PublisherActivity / TaskAdapter
        └── res/
            ├── layout/
            ├── drawable/
            ├── values/
            └── mipmap-anydpi-v26/
```
