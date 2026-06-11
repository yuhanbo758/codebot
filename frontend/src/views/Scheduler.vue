<template>
  <div class="scheduler-view">
    <el-header>
      <el-button type="primary" @click="openCreateDialog">
        <el-icon><Plus /></el-icon>
        新建定时任务
      </el-button>
      <div class="candidate-notify-switch">
        <span>开启通知</span>
        <el-switch
          v-model="candidateNotificationEnabled"
          :loading="candidateNotificationSaving"
          @change="saveCandidateNotificationSetting"
        />
        <span class="candidate-notify-hint">任务进入成长候选时提醒我</span>
      </div>
    </el-header>

    <!-- 活跃任务列表 -->
    <el-table :data="tasks" style="width: 100%">
      <el-table-column prop="name" label="任务名称" width="180">
        <template #default="{ row }">
          {{ row.name }}
          <el-tag v-if="row.run_once" size="small" type="warning" style="margin-left:4px">一次性</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="执行器" width="100">
        <template #default="{ row }">
          <el-tag size="small" :type="row.executor === 'hermes' ? 'success' : 'info'">
            {{ executorLabel(row.executor) }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="执行模型" width="190" show-overflow-tooltip>
        <template #default="{ row }">{{ modelLabel(row.execution_model) }}</template>
      </el-table-column>
      <el-table-column prop="cron_expression" label="Cron 表达式" width="140" />
      <el-table-column prop="next_run" label="下次运行" width="170">
        <template #default="{ row }">{{ formatTime(row.next_run) }}</template>
      </el-table-column>
      <el-table-column prop="last_run" label="上次运行" width="170">
        <template #default="{ row }">{{ formatTime(row.last_run) }}</template>
      </el-table-column>
      <el-table-column label="通知渠道" width="180">
        <template #default="{ row }">
          <el-tag
            v-for="channel in row.notify_channels"
            :key="channel"
            size="small"
            style="margin-right:4px"
          >{{ channelLabel(channel) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="状态" width="80">
        <template #default="{ row }">
          <el-switch v-model="row.enabled" @change="toggleTask(row)" />
        </template>
      </el-table-column>
      <el-table-column label="操作" width="280" class-name="op-col">
        <template #default="{ row }">
          <div class="op-row">
            <el-button size="small" @click="runTaskNow(row)">执行</el-button>
            <el-button size="small" @click="editTask(row)">编辑</el-button>
            <el-button size="small" type="warning" plain @click="archiveTask(row)">归档</el-button>
            <el-button size="small" type="danger" plain @click="deleteTask(row)">删除</el-button>
          </div>
        </template>
      </el-table-column>
    </el-table>

    <!-- 已归档任务（折叠） -->
    <el-collapse style="margin-top:24px">
      <el-collapse-item title="已归档任务" name="archived">
        <el-button size="small" @click="loadArchivedTasks" style="margin-bottom:8px">刷新</el-button>
        <el-table :data="archivedTasks" style="width: 100%">
          <el-table-column prop="name" label="任务名称" width="200" />
          <el-table-column label="执行器" width="100">
            <template #default="{ row }">
              <el-tag size="small" :type="row.executor === 'hermes' ? 'success' : 'info'">
                {{ executorLabel(row.executor) }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="执行模型" width="190" show-overflow-tooltip>
            <template #default="{ row }">{{ modelLabel(row.execution_model) }}</template>
          </el-table-column>
          <el-table-column prop="cron_expression" label="Cron 表达式" width="140" />
          <el-table-column prop="last_run" label="最后运行" width="170">
            <template #default="{ row }">{{ formatTime(row.last_run) }}</template>
          </el-table-column>
          <el-table-column label="通知渠道" width="180">
            <template #default="{ row }">
              <el-tag
                v-for="channel in row.notify_channels"
                :key="channel"
                size="small"
                style="margin-right:4px"
              >{{ channelLabel(channel) }}</el-tag>
            </template>
          </el-table-column>
        </el-table>
      </el-collapse-item>
    </el-collapse>

    <!-- 创建/编辑对话框 -->
    <el-dialog v-model="showCreateDialog" :title="editMode ? '编辑任务' : '创建任务'" width="600px">
      <el-form :model="newTask" label-width="110px">
        <el-form-item label="任务名称">
          <el-input v-model="newTask.name" placeholder="例如：每日日报" />
        </el-form-item>
        <el-form-item label="AI 辅助生成">
          <el-input
            v-model="aiPrompt"
            type="textarea"
            :rows="2"
            placeholder="例如：每天早上 9 点生成日报"
          />
          <el-button size="small" style="margin-top:6px" @click="generateWithAI" :loading="aiLoading">
            AI 生成 Cron
          </el-button>
        </el-form-item>
        <el-form-item label="Cron 表达式">
          <el-input v-model="newTask.cron_expression" placeholder="0 9 * * *" />
          <small style="color:#909399">示例：每天 9 点 = "0 9 * * *"</small>
        </el-form-item>
        <el-form-item label="任务内容">
          <el-input
            v-model="newTask.task_prompt"
            type="textarea"
            :rows="4"
            placeholder="描述任务要做什么..."
          />
        </el-form-item>
        <el-form-item label="执行器">
          <el-radio-group v-model="newTask.executor">
            <el-radio-button label="opencode">OpenCode</el-radio-button>
            <el-radio-button label="hermes">Hermes</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="执行模型">
          <el-select
            v-model="newTask.execution_model"
            filterable
            clearable
            placeholder="使用记忆整理备用模型"
            style="width: 100%"
            :loading="modelsLoading"
            @focus="loadModels"
          >
            <el-option label="使用记忆整理备用模型" value="" />
            <el-option
              v-for="model in availableModels"
              :key="model.id"
              :label="model.name || model.id"
              :value="model.id"
            />
          </el-select>
          <div style="color:#909399;font-size:12px;margin-top:4px">
            聊天中创建的任务会自动记录当时选择的主模型；若该模型后续不可用，执行时会回退到记忆整理模型
          </div>
        </el-form-item>
        <el-form-item label="通知渠道">
          <el-checkbox-group v-model="newTask.notify_channels">
            <el-checkbox label="app">应用内</el-checkbox>
            <el-checkbox label="desktop">系统桌面</el-checkbox>
            <el-checkbox label="lark">飞书</el-checkbox>
            <el-checkbox label="email">邮箱</el-checkbox>
          </el-checkbox-group>
          <div style="color:#909399;font-size:12px;margin-top:4px">留空则使用设置中的默认通知配置</div>
        </el-form-item>
        <el-form-item label="一次性任务">
          <el-switch v-model="newTask.run_once" />
          <span style="color:#909399;font-size:12px;margin-left:8px">执行完成后自动关闭并归档</span>
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
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus } from '@element-plus/icons-vue'
import axios from 'axios'

const tasks = ref([])
const archivedTasks = ref([])
const showCreateDialog = ref(false)
const editMode = ref(false)
const newTask = ref({
  name: '',
  cron_expression: '',
  task_prompt: '',
  notify_channels: [],
  run_once: false,
  executor: 'opencode',
  execution_model: '',
})
const aiPrompt = ref('')
const aiLoading = ref(false)
const availableModels = ref([])
const modelsLoading = ref(false)
const candidateNotificationEnabled = ref(true)
const candidateNotificationSaving = ref(false)

const CHANNEL_LABELS = {
  app: '应用内',
  desktop: '系统桌面',
  lark: '飞书',
  email: '邮箱',
}

const channelLabel = (ch) => CHANNEL_LABELS[ch] || ch
const executorLabel = (executor) => executor === 'hermes' ? 'Hermes' : 'OpenCode'
const modelLabel = (modelId) => {
  const id = String(modelId || '').trim()
  if (!id) return '记忆整理备用模型'
  const found = availableModels.value.find((model) => model.id === id)
  return found?.name || id
}

const loadModels = async () => {
  if (availableModels.value.length > 0 || modelsLoading.value) return
  modelsLoading.value = true
  try {
    const response = await axios.get('/api/chat/models')
    if (response.data?.success) {
      availableModels.value = response.data.data?.models || []
    }
  } catch {
    // 表格仍显示已保存的模型 ID，模型列表加载失败不阻断任务编辑
  } finally {
    modelsLoading.value = false
  }
}

const formatTime = (iso) => {
  if (!iso) return '-'
  try {
    return new Date(iso).toLocaleString('zh-CN', {
      year: 'numeric', month: '2-digit', day: '2-digit',
      hour: '2-digit', minute: '2-digit',
    })
  } catch {
    return iso
  }
}

const loadTasks = async () => {
  try {
    const response = await axios.get('/api/scheduler/tasks')
    tasks.value = response.data.data || []
  } catch {
    ElMessage.error('加载任务失败')
  }
}

const loadArchivedTasks = async () => {
  try {
    const response = await axios.get('/api/scheduler/tasks/archived')
    archivedTasks.value = response.data.data || []
  } catch {
    ElMessage.error('加载归档任务失败')
  }
}

const loadCandidateNotificationSetting = async () => {
  try {
    const response = await axios.get('/api/config/general')
    candidateNotificationEnabled.value = response.data?.data?.task_candidate_notification_enabled !== false
  } catch {
    candidateNotificationEnabled.value = true
  }
}

const saveCandidateNotificationSetting = async () => {
  candidateNotificationSaving.value = true
  try {
    await axios.patch('/api/config/general', {
      task_candidate_notification_enabled: Boolean(candidateNotificationEnabled.value),
    })
    ElMessage.success(candidateNotificationEnabled.value ? '候选通知已开启' : '候选通知已关闭')
  } catch {
    candidateNotificationEnabled.value = !candidateNotificationEnabled.value
    ElMessage.error('保存通知设置失败')
  } finally {
    candidateNotificationSaving.value = false
  }
}

const openCreateDialog = () => {
  editMode.value = false
  newTask.value = {
    name: '',
    cron_expression: '',
    task_prompt: '',
    notify_channels: [],
    run_once: false,
    executor: 'opencode',
    execution_model: '',
  }
  aiPrompt.value = ''
  showCreateDialog.value = true
}

const generateWithAI = async () => {
  if (!aiPrompt.value) return
  aiLoading.value = true
  try {
    const response = await axios.post('/api/scheduler/ai-generate', {
      prompt: aiPrompt.value,
    })
    const result = response.data.data
    newTask.value.cron_expression = result.cron
    ElMessage.success(`AI 生成：${result.description}`)
  } catch {
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
        notify_channels: newTask.value.notify_channels,
        run_once: newTask.value.run_once,
        executor: newTask.value.executor || 'opencode',
        execution_model: newTask.value.execution_model || '',
      })
      ElMessage.success('任务已更新')
    } else {
      await axios.post('/api/scheduler/tasks', newTask.value)
      ElMessage.success('任务创建成功')
    }
    showCreateDialog.value = false
    editMode.value = false
    loadTasks()
  } catch {
    ElMessage.error('保存失败')
  }
}

const runTaskNow = async (task) => {
  try {
    await axios.post(`/api/scheduler/tasks/${task.id}/run`)
    ElMessage.success('任务执行中')
  } catch {
    ElMessage.error('执行失败')
  }
}

const toggleTask = async (task) => {
  try {
    await axios.put(`/api/scheduler/tasks/${task.id}`, { enabled: task.enabled })
  } catch {
    ElMessage.error('更新失败')
  }
}

const archiveTask = async (task) => {
  try {
    await ElMessageBox.confirm(`确定要归档任务"${task.name}"吗？归档后将从列表移除，可在归档区查看。`, '归档确认', {
      confirmButtonText: '归档',
      cancelButtonText: '取消',
      type: 'warning',
    })
    await axios.post(`/api/scheduler/tasks/${task.id}/archive`)
    ElMessage.success('已归档')
    loadTasks()
  } catch (e) {
    if (e !== 'cancel') ElMessage.error('归档失败')
  }
}

const deleteTask = async (task) => {
  try {
    await ElMessageBox.confirm(`确定要删除任务"${task.name}"吗？`, '删除确认', {
      confirmButtonText: '删除',
      cancelButtonText: '取消',
      type: 'danger',
    })
    await axios.delete(`/api/scheduler/tasks/${task.id}`)
    ElMessage.success('已删除')
    loadTasks()
  } catch (e) {
    if (e !== 'cancel') ElMessage.error('删除失败')
  }
}

const editTask = (task) => {
  editMode.value = true
  newTask.value = {
    ...task,
    executor: task.executor || 'opencode',
    execution_model: task.execution_model || '',
    notify_channels: [...(task.notify_channels || [])]
  }
  aiPrompt.value = ''
  showCreateDialog.value = true
  loadModels()
}

onMounted(() => {
  loadTasks()
  loadArchivedTasks()
  loadModels()
  loadCandidateNotificationSetting()
})
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
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
}

.candidate-notify-switch {
  display: flex;
  align-items: center;
  gap: 8px;
  color: #606266;
  font-size: 14px;
}

.candidate-notify-hint {
  color: #909399;
  font-size: 12px;
}

/* 操作列：强制单行，按钮紧凑排列 */
.op-row {
  display: flex;
  flex-wrap: nowrap;
  gap: 4px;
  align-items: center;
}
.op-row .el-button {
  margin: 0;
  padding: 4px 8px;
}
</style>
