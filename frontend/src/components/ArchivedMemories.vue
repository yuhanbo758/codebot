<template>
  <div class="archived-memories">
    <el-table :data="memories" style="width: 100%">
      <el-table-column prop="category" label="类别" width="120" />
      <el-table-column prop="content" label="内容" />
      <el-table-column prop="created_at" label="创建时间" width="180" />
      <el-table-column label="操作" width="200">
        <template #default="{ row }">
          <el-button size="small" @click="restoreMemory(row.id)">恢复</el-button>
          <el-button size="small" type="danger" @click="deleteMemory(row.id)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import axios from 'axios'

const memories = ref([])

const loadMemories = async () => {
  try {
    const response = await axios.get('/api/memory/memories?archived=true')
    memories.value = response.data.data.items || []
  } catch (error) {
    ElMessage.error('加载记忆失败')
  }
}

const restoreMemory = async (id) => {
  try {
    await axios.post(`/api/memory/memories/${id}/restore`)
    ElMessage.success('已恢复')
    loadMemories()
  } catch (error) {
    ElMessage.error('恢复失败')
  }
}

const deleteMemory = async (id) => {
  try {
    await axios.delete(`/api/memory/memories/${id}`)
    ElMessage.success('已删除')
    loadMemories()
  } catch (error) {
    ElMessage.error('删除失败')
  }
}

onMounted(loadMemories)
</script>
