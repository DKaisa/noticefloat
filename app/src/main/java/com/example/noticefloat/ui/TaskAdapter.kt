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
        holder.b.title.text = t.title
        holder.b.content.text = t.content.ifBlank { "点击标记完成 · 长按删除" }
        holder.b.meta.text = when {
            t.remindAt > 0 -> "⏰ ${TIME_FMT.format(Date(t.remindAt))} · ${t.publisher}"
            else -> "· ${t.publisher}"
        }
        holder.itemView.setOnClickListener { onClick(t) }
        holder.itemView.setOnLongClickListener { onLongClick(t); true }
    }
}
