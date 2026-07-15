package com.example.noticefloat.ui

import android.os.Bundle
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import com.example.noticefloat.R

/** v0.8.3 使用说明 */
class HelpActivity : AppCompatActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_help)
        findViewById<TextView>(R.id.tvHelp).text = HELP_TEXT
    }

    companion object {
        val HELP_TEXT = """
🟠 悬浮球
• 点击 → 打开待办面板
• 长按 → 打开发布页并自动开始语音识别（识别中可视，失败可点【停止/重录】重来，或用手机键盘话筒直接输入）
• 拖动 → 移动位置，松手自动吸左/右边

📢 发布消息
• 首行前 30 字自动作为标题
• 「📢 发所有人（广播）」：勾选后忽略群，发给所有已用过本 App 的 device（无需加群）
• 「🔴 强弹窗」：接收方必须点【✓ 我已知晓】才关，防冲刷

🎙 语音输入
• 系统 ASR 优先（EXTRA_PREFER_OFFLINE=true）；荣耀/华为无 GMS 时可能报网络异常
• 兜底：用手机自带输入法话筒按钮说话，效果一样

⚡ 悬浮球关键字
• 有 1 条待办时圆球中央显示标题关键字（盐场/十殿/竞技…）
• 多条时右上角红角标显示数字

💡 边缘条模式
• 设置里可切换圆球 / 贴边条两种样式
• 贴边条更省空间，仅露出屏幕边缘 27dp

⏰ 提醒
• 支持"30分/1时/3时/明天9点"快捷 + 自定义日期时间
• 手机休眠也会响铃（USE_EXACT_ALARM）

🖥 PC 端
• Ctrl + 鼠标滚轮 = 全局缩放字体
• 双击任务行 = 直接完成
• 支持相对时间输入（N 小时后）
• 托盘：单击最小化/双击显示，红色勇字有新提醒

🔧 服务器配置
• 「服务器与昵称设置」里改 URL；cpolar tunnel 每次重启会变域名
        """.trimIndent()
    }
}
