package com.example.noticefloat.ui

import android.view.LayoutInflater
import android.view.ViewGroup
import androidx.recyclerview.widget.DiffUtil
import androidx.recyclerview.widget.ListAdapter
import androidx.recyclerview.widget.RecyclerView
import com.example.noticefloat.data.Task
import com.example.noticefloat.databinding.ItemTaskBinding
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

class TaskAdapter(
    private val onClick: (Task) -> Unit,
    private val onLongClick: (Task) -> Unit
) : ListAdapter<Task, TaskAdapter.VH>(DIFF) {

    companion object {
        val DIFF = object : DiffUtil.ItemCallback<Task>() {
            override fun areItemsTheSame(o: Task, n: Task) = o.id == n.id
            override fun areContentsTheSame(o: Task, n: Task) = o == n
        }
        val TIME_FMT = SimpleDateFormat("MM-dd HH:mm", Locale.CHINA)
    }

    inner class VH(val b: ItemTaskBinding) : RecyclerView.ViewHolder(b.root)

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): VH {
        val b = ItemTaskBinding.inflate(LayoutInflater.from(parent.context), parent, false)
        return VH(b)
    }

    override fun onBindViewHolder(holder: VH, position: Int) {
        val t = getItem(position)
        // v0.8.6：升级特殊 task 不显示"发送方"
        val isUpdate = t.publisher == "__update__"
        val publisher = if (isUpdate) "" else t.publisher

        // 短消息合并：title 加上发送方，避免占多余一行
        holder.b.title.text = if (publisher.isNotBlank()) "${t.title}   · $publisher" else t.title

        val cnt = t.content
        if (cnt.isBlank()) {
            holder.b.content.visibility = android.view.View.GONE
        } else {
            holder.b.content.visibility = android.view.View.VISIBLE
            holder.b.content.text = cnt
        }

        if (t.remindAt > 0) {
            holder.b.meta.visibility = android.view.View.VISIBLE
            holder.b.meta.text = "⏰ " + TIME_FMT.format(Date(t.remindAt))
        } else {
            holder.b.meta.visibility = android.view.View.GONE
        }

        holder.itemView.setOnClickListener { onClick(t) }
        holder.itemView.setOnLongClickListener { onLongClick(t); true }
    }
}
