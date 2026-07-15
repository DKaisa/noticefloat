package com.example.noticefloat.ui

import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.text.method.LinkMovementMethod
import android.widget.ArrayAdapter
import android.widget.Toast
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import com.example.noticefloat.databinding.ActivityGroupsBinding
import com.example.noticefloat.databinding.DialogInputBinding
import com.example.noticefloat.remote.ApiClient
import com.example.noticefloat.remote.Session
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

class GroupsActivity : AppCompatActivity() {

    private lateinit var b: ActivityGroupsBinding
    private lateinit var api: ApiClient
    private val items = mutableListOf<ApiClient.DiscoverGroup>()
    private lateinit var adapter: ArrayAdapter<String>

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        b = ActivityGroupsBinding.inflate(layoutInflater)
        setContentView(b.root)

        val session = Session.get(this)
        if (!session.isConfigured()) {
            Toast.makeText(this, "请先在设置中配置服务器地址和昵称", Toast.LENGTH_LONG).show()
            finish()
            return
        }
        api = ApiClient(session)

        adapter = ArrayAdapter(this, android.R.layout.simple_list_item_2, android.R.id.text1,
            mutableListOf())
        b.list.adapter = adapter
        b.list.setOnItemLongClickListener { _, _, position, _ ->
            val g = items[position]
            if (!g.joined) return@setOnItemLongClickListener true
            AlertDialog.Builder(this)
                .setTitle("退出「${g.name}」？")
                .setPositiveButton("退出") { _, _ -> leaveGroup(g.code) }
                .setNegativeButton("取消", null)
                .show()
            true
        }
        b.list.setOnItemClickListener { _, _, position, _ ->
            val g = items[position]
            if (g.joined) {
                AlertDialog.Builder(this)
                    .setTitle(g.name)
                    .setMessage("群号：${g.code}\n成员：${g.memberCount} 人\n创建者：${g.creatorName.ifBlank { "-" }}\n\n长按可退出。")
                    .setPositiveButton("确定", null)
                    .show()
            } else {
                AlertDialog.Builder(this)
                    .setTitle("加入「${g.name}」？")
                    .setMessage("群号：${g.code}\n成员：${g.memberCount} 人\n创建者：${g.creatorName.ifBlank { "-" }}")
                    .setPositiveButton("加入") { _, _ -> joinGroupByCode(g.code) }
                    .setNegativeButton("取消", null)
                    .show()
            }
        }

        b.btnCreate.setOnClickListener { showCreateDialog() }
        b.btnJoin.setOnClickListener { showJoinDialog() }
        refresh()
    }

    private fun refresh() {
        lifecycleScope.launch {
            val result = withContext(Dispatchers.IO) {
                runCatching { api.discoverGroups() }
            }
            result.onSuccess { (list, meta) ->
                items.clear(); items.addAll(list)
                val labels = list.map {
                    val mark = if (it.joined) "✅ 已加入" else "➕ 未加入"
                    "${it.name}  ·  #${it.code}  ·  ${it.memberCount} 人  ·  $mark"
                }
                adapter.clear(); adapter.addAll(labels); adapter.notifyDataSetChanged()
                val (total, max) = meta
                title = "群列表  ($total / $max)"
                b.empty.visibility = if (list.isEmpty()) android.view.View.VISIBLE else android.view.View.GONE
            }.onFailure {
                Toast.makeText(this@GroupsActivity, "拉取失败：${it.message}", Toast.LENGTH_LONG).show()
            }
        }
    }

    private fun joinGroupByCode(code: String) {
        lifecycleScope.launch {
            val r = withContext(Dispatchers.IO) { runCatching { api.joinGroup(code) } }
            r.onSuccess {
                Toast.makeText(this@GroupsActivity, "已加入「${it.optString("name")}」", Toast.LENGTH_LONG).show()
                refresh()
            }.onFailure {
                Toast.makeText(this@GroupsActivity, "加入失败：${it.message}", Toast.LENGTH_LONG).show()
            }
        }
    }

    private fun showCreateDialog() {
        val dialogB = DialogInputBinding.inflate(layoutInflater)
        dialogB.tvHint.text = "输入群名称"
        dialogB.etInput.hint = "例：技术部通知群"
        AlertDialog.Builder(this)
            .setTitle("新建群")
            .setView(dialogB.root)
            .setPositiveButton("创建") { _, _ ->
                val name = dialogB.etInput.text?.toString()?.trim().orEmpty()
                if (name.isBlank()) return@setPositiveButton
                lifecycleScope.launch {
                    val r = withContext(Dispatchers.IO) { runCatching { api.createGroup(name) } }
                    r.onSuccess { (code, token, gname) ->
                        showInvite(gname, code, token)
                        refresh()
                    }.onFailure {
                        Toast.makeText(this@GroupsActivity, "创建失败：${it.message}", Toast.LENGTH_LONG).show()
                    }
                }
            }
            .setNegativeButton("取消", null)
            .show()
    }

    private fun showJoinDialog() {
        val dialogB = DialogInputBinding.inflate(layoutInflater)
        dialogB.tvHint.text = "输入 4 位群号 或 8 位管理员口令码"
        dialogB.etInput.hint = "例：1234  或  ABCD1234"
        AlertDialog.Builder(this)
            .setTitle("加入群 / 兑换口令")
            .setView(dialogB.root)
            .setPositiveButton("提交") { _, _ ->
                val code = dialogB.etInput.text?.toString()?.trim().orEmpty()
                if (!((code.length == 4 && code.all { it.isDigit() }) ||
                      (code.length == 8 && code.all { it.isLetterOrDigit() }))) {
                    Toast.makeText(this,
                        "请输入 4 位数字群号 或 8 位字母数字口令码",
                        Toast.LENGTH_SHORT).show()
                    return@setPositiveButton
                }
                lifecycleScope.launch {
                    val r = withContext(Dispatchers.IO) { runCatching { api.joinGroup(code) } }
                    r.onSuccess { obj ->
                        val role = obj.optString("role")
                        val name = obj.optString("name")
                        if (role == "admin") {
                            Toast.makeText(this@GroupsActivity,
                                "🎉 已成为管理员", Toast.LENGTH_LONG).show()
                        } else {
                            Toast.makeText(this@GroupsActivity,
                                "已加入「$name」", Toast.LENGTH_LONG).show()
                        }
                        refresh()
                    }.onFailure {
                        Toast.makeText(this@GroupsActivity, "失败：${it.message}", Toast.LENGTH_LONG).show()
                    }
                }
            }
            .setNegativeButton("取消", null)
            .show()
    }

    private fun leaveGroup(code: String) {
        lifecycleScope.launch {
            val r = withContext(Dispatchers.IO) { runCatching { api.leaveGroup(code) } }
            r.onSuccess {
                Toast.makeText(this@GroupsActivity, "已退出", Toast.LENGTH_SHORT).show()
                refresh()
            }.onFailure {
                Toast.makeText(this@GroupsActivity, "退出失败：${it.message}", Toast.LENGTH_LONG).show()
            }
        }
    }

    private fun showInvite(name: String, code: String, token: String) {
        AlertDialog.Builder(this)
            .setTitle("群「$name」已创建")
            .setMessage("群号：$code\n\n把这个 8 位群号发给同事，他们在 App 里选「加入群」输入即可。")
            .setPositiveButton("复制群号") { _, _ ->
                val cm = getSystemService(CLIPBOARD_SERVICE) as android.content.ClipboardManager
                cm.setPrimaryClip(android.content.ClipData.newPlainText("code", code))
                Toast.makeText(this, "已复制", Toast.LENGTH_SHORT).show()
            }
            .setNegativeButton("关闭", null)
            .show()
    }
}
