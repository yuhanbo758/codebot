<template>
  <div class="scheduler-view">
    <el-header>
      <el-button type="primary" @click="showCreateDialog = true">
        <el-icon><Plus /></el-icon>
        新建定时任务
      </el-button>
    </el-header>
    
    <el-table :data="tasks" style="width: 100%">
      <el-table-column prop="name" label="任务名称" width="200" />
      <el-table-column prop="cron_expression" label="Cron 表达式" width="150" />
      <el-table-column prop="next_run" label="下次运行" width="180" />
      <el-table-column prop="last_run" label="上次运行" width="180" />
      <el-table-column label="通知渠道" width="200">
        <template #default="{ row }">
          <el-tag v-for="channel in row.notify_channels" :key="channel" size="small">
            {{ channel }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="状态" width="100">
        <template #default="{ row }">
          <el-switch v-model="row.enabled" @change="toggleTask(row)" />
        </template>
      </el-table-column>
      <el-table-column label="操作" width="250">
        <template #default="{ row }">
          <el-button size="small" @click="runTaskNow(row)">立即执行</el-button>
          <el-button size="small" @click="editTask(row)">编辑</el-button>
          <el-button size="small" type="danger" @click="deleteTask(row)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>
    
    <!-- 创建/编辑对话框 -->
    <el-dialog v-model="showCreateDialog" :title="editMode ? '编辑任务' : '创建任务'">
      <el-form :model="newTask" label-width="100px">
        <el-form-item label="任务名称">
          <el-input v-model="newTask.name" placeholder="例如：每日日报" />
        </el-form-item>
        <el-form-item label="Cron 表达式">
          <el-input v-model="newTask.cron_expression" placeholder="0 9 * * *" />
          <small>示例：每天 9 点 = "0 9 * * *"</small>
        </el-form-item>
        <el-form-item label="AI 辅助">
          <el-input
            v-model="aiPrompt"
            type="textarea"
            :rows="2"
            placeholder="例如：每天早上 9 点生成日报"
          />
          <el-button size="small" @click="generateWithAI" :loading="aiLoading">
            AI 生成 Cron
          </el-button>
        </el-form-item>
        <el-form-item label="任务内容">
          <el-input
            v-model="newTask.task_prompt"
            type="textarea"
            :rows="4"
            placeholder="描述任务要做什么..."
          />
        </el-form-item>
        <el-form-item label="通知渠道">
          <el-checkbox-group v-model="newTask.notify_channels">
            <el-checkbox label="app">应用内</el-checkbox>
            <el-checkbox label="lark">飞书</el-checkbox>
            <el-checkbox label="email">邮箱</el-checkbox>
          </el-checkbox-group>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showCreateDialog = false">取消</el-button>
        <el-button type="primary" @click="saveTask">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import axios from 'axios'

const tasks = ref([])
const showCreateDialog = ref(false)
const editMode = ref(false)
const newTask = ref({
  name: '',
  cron_expression: '',
  task_prompt: '',
  notify_channels: ['app']
})
const aiPrompt = ref('')
const aiLoading = ref(false)

const loadTasks = async () => {
  try {
    const response = await axios.get('/api/scheduler/tasks')
    tasks.value = response.data.data || []
  } catch (error) {
    ElMessage.error('加载任务失败')
  }
}

const generateWithAI = async () => {
  if (!aiPrompt.value) return
  
  aiLoading.value = true
  try {
    const response = await axios.post('/api/scheduler/ai-generate', {
      prompt: aiPrompt.value
    })
    const result = response.data.data
    newTask.value.cron_expression = result.cron
    ElMessage.success(`AI 生成：${result.description}`)
  } catch (error) {
    ElMessage.error('AI 生成失败')
  } finally {
    aiLoading.value = false
  }
}

const saveTask = async () => {
  try {
    if (editMode.value) {
      await axios.put(`/api/scheduler/tasks/${newTask.value.id}`, {
        name: newTask.value.name,
        cron_expression: newTask.value.cron_expression,
        task_prompt: newTask.value.task_prompt,
        enabled: newTask.value.enabled,
        notify_channels: newTask.value.notify_channels
      })
      ElMessage.success('任务已更新')
    } else {
      await axios.post('/api/scheduler/tasks', newTask.value)
      ElMessage.success('任务创建成功')
    }
    showCreateDialog.value = false
    editMode.value = false
    loadTasks()
  } catch (error) {
    ElMessage.error('保存失败')
  }
}

const runTaskNow = async (task) => {
  try {
    await axios.post(`/api/scheduler/tasks/${task.id}/run`)
    ElMessage.success('任务执行中')
  } catch (error) {
    ElMessage.error('执行失败')
  }
}

const toggleTask = async (task) => {
  try {
    await axios.put(`/api/scheduler/tasks/${task.id}`, {
      enabled: task.enabled
    })
  } catch (error) {
    ElMessage.error('更新失败')
  }
}

const deleteTask = async (task) => {
  try {
    await axios.delete(`/api/scheduler/tasks/${task.id}`)
    ElMessage.success('已删除')
    loadTasks()
  } catch (error) {
    ElMessage.error('删除失败')
  }
}

const editTask = (task) => {
  editMode.value = true
  newTask.value = { ...task }
  showCreateDialog.value = true
}

onMounted(loadTasks)
</script>

<style scoped>
.scheduler-view {
  padding: 20px;
}

.el-header {
  padding: 0 0 20px 0;
  background: none;
  border-bottom: 1px solid #e4e7ed;
  margin-bottom: 20px;
}
</style>
