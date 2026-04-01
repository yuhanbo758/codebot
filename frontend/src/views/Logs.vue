<template>
  <div class="logs-view">
    <el-tabs v-model="activeTab" class="logs-tabs">
      <!-- ── Tab 1: 任务执行日志 ─────────────────────────────────────── -->
      <el-tab-pane label="任务执行日志" name="task">
        <div class="tab-content">
          <div class="tab-header">
            <div class="header-actions">
              <el-select v-model="filterTaskId" placeholder="全部任务" style="width: 200px">
                <el-option label="全部任务" value="" />
                <el-option v-for="task in tasks" :key="task.id" :label="task.name" :value="task.id" />
              </el-select>
              <el-button @click="loadLogs" style="margin-left: 10px">查询</el-button>
            </div>
          </div>

          <!-- 批量操作工具栏 -->
          <div class="batch-toolbar" v-if="selectedLogIds.length > 0">
            <span class="batch-info">已选择 {{ selectedLogIds.length }} 条日志</span>
            <div class="batch-actions">
              <el-button size="small" @click="clearSelection">取消选择</el-button>
              <el-button size="small" type="danger" @click="batchDeleteLogs">
                <el-icon><Delete /></el-icon>
                删除所选
              </el-button>
            </div>
          </div>

          <div class="table-wrapper">
            <el-table
              :data="logs"
              style="width: 100%"
              height="100%"
              @selection-change="handleSelectionChange"
              ref="logTableRef"
            >
              <el-table-column type="selection" width="50" />
              <el-table-column prop="task_name" label="任务名称" min-width="140" show-overflow-tooltip />
              <el-table-column prop="started_at" label="开始时间" width="175">
                <template #default="{ row }">{{ formatDateTime(row.started_at) }}</template>
              </el-table-column>
              <el-table-column prop="completed_at" label="完成时间" width="175">
                <template #default="{ row }">{{ formatDateTime(row.completed_at) }}</template>
              </el-table-column>
              <el-table-column label="状态" width="90">
                <template #default="{ row }">
                  <el-tag :type="getStatusType(row.status)">
                    {{ getStatusText(row.status) }}
                  </el-tag>
                </template>
              </el-table-column>
              <el-table-column prop="tokens_used" label="Token 消耗" width="110" align="right" />
              <el-table-column label="操作" width="140" fixed="right">
                <template #default="{ row }">
                  <el-button size="small" type="primary" link @click="viewDetail(row)">
                    <el-icon><View /></el-icon>详情
                  </el-button>
                  <el-button size="small" type="danger" link @click="deleteSingleLog(row)">
                    <el-icon><Delete /></el-icon>删除
                  </el-button>
                </template>
              </el-table-column>
            </el-table>
          </div>

          <!-- 分页 -->
          <div class="pagination-bar">
            <el-pagination
              v-model:current-page="currentPage"
              v-model:page-size="pageSize"
              :page-sizes="[20, 50, 100, 200]"
              :total="totalLogs"
              layout="total, sizes, prev, pager, next, jumper"
              @size-change="handlePageSizeChange"
              @current-change="handlePageChange"
              background
            />
          </div>

          <el-divider />

          <!-- 日志配置 -->
          <el-form inline class="config-form">
            <el-form-item label="任务日志保留天数">
              <el-input-number v-model="retentionDays" :min="0" :max="365" />
            </el-form-item>
            <el-form-item label="聊天日志保留天数">
              <el-input-number v-model="chatRetentionDays" :min="0" :max="365" />
            </el-form-item>
            <el-form-item label="系统日志保留天数">
              <el-input-number v-model="logConfig.system_log_retention_days" :min="1" :max="90" />
            </el-form-item>
            <el-form-item label="日志级别">
              <el-select v-model="logConfig.log_level" style="width: 100px">
                <el-option label="DEBUG" value="DEBUG" />
                <el-option label="INFO" value="INFO" />
                <el-option label="WARNING" value="WARNING" />
                <el-option label="ERROR" value="ERROR" />
              </el-select>
            </el-form-item>
            <el-form-item>
              <el-button type="primary" @click="saveConfig">保存配置</el-button>
              <el-button type="warning" @click="cleanupLogs">立即清理</el-button>
            </el-form-item>
          </el-form>
          <div class="config-tip">
            <el-icon style="margin-right:4px"><InfoFilled /></el-icon>
            设为 0 表示永久保留不自动清理。自动整理时会按保留天数自动清理过期日志。
          </div>
        </div>
      </el-tab-pane>

      <!-- ── Tab 2: 聊天日志 ────────────────────────────────────────── -->
      <el-tab-pane label="聊天日志" name="chat">
        <div class="tab-content">
          <div class="tab-header">
            <div class="header-actions">
              <el-input
                v-model="chatLogSearch"
                placeholder="搜索用户消息..."
                clearable
                style="width: 220px"
                @clear="loadChatLogs"
                @keyup.enter="loadChatLogs"
              />
              <el-button @click="loadChatLogs" style="margin-left: 10px">查询</el-button>
            </div>
          </div>

          <!-- 批量操作工具栏 -->
          <div class="batch-toolbar" v-if="selectedChatLogIds.length > 0">
            <span class="batch-info">已选择 {{ selectedChatLogIds.length }} 条日志</span>
            <div class="batch-actions">
              <el-button size="small" @click="clearChatSelection">取消选择</el-button>
              <el-button size="small" type="danger" @click="batchDeleteChatLogs">
                <el-icon><Delete /></el-icon>
                删除所选
              </el-button>
            </div>
          </div>

          <div class="table-wrapper">
            <el-table
              :data="chatLogs"
              style="width: 100%"
              height="100%"
              @selection-change="handleChatSelectionChange"
              ref="chatLogTableRef"
            >
              <el-table-column type="selection" width="50" />
              <el-table-column prop="created_at" label="时间" width="175">
                <template #default="{ row }">{{ formatDateTime(row.created_at) }}</template>
              </el-table-column>
              <el-table-column prop="conversation_title" label="对话" width="150" show-overflow-tooltip />
              <el-table-column prop="user_message" label="用户消息" min-width="200" show-overflow-tooltip />
              <el-table-column prop="final_reply" label="最终回复" min-width="200" show-overflow-tooltip />
              <el-table-column prop="model" label="模型" width="120" show-overflow-tooltip />
              <el-table-column label="操作" width="140" fixed="right">
                <template #default="{ row }">
                  <el-button size="small" type="primary" link @click="viewChatLogDetail(row)">
                    <el-icon><View /></el-icon>详情
                  </el-button>
                  <el-button size="small" type="danger" link @click="deleteSingleChatLog(row)">
                    <el-icon><Delete /></el-icon>删除
                  </el-button>
                </template>
              </el-table-column>
            </el-table>
          </div>

          <!-- 分页 -->
          <div class="pagination-bar">
            <el-pagination
              v-model:current-page="chatCurrentPage"
              v-model:page-size="chatPageSize"
              :page-sizes="[20, 50, 100, 200]"
              :total="totalChatLogs"
              layout="total, sizes, prev, pager, next, jumper"
              @size-change="handleChatPageSizeChange"
              @current-change="handleChatPageChange"
              background
            />
          </div>

          <el-divider />

          <div class="chat-log-tip">
            <el-icon style="margin-right:6px"><InfoFilled /></el-icon>
            聊天日志记录每次对话的内部提示词、推理过程和最终回复，可用于学习和优化 Prompt。
            <el-button type="warning" size="small" @click="cleanupChatLogs" style="margin-left: 12px">
              立即清理聊天日志
            </el-button>
          </div>
        </div>
      </el-tab-pane>
    </el-tabs>

    <!-- 任务日志详情对话框 -->
    <el-dialog
      v-model="detailVisible"
      title="任务日志详情"
      width="680px"
      top="5vh"
      destroy-on-close
    >
      <div v-if="selectedLog" class="log-detail">
        <el-descriptions :column="2" border>
          <el-descriptions-item label="任务名称" :span="2">
            {{ selectedLog.task_name }}
          </el-descriptions-item>
          <el-descriptions-item label="日志 ID" :span="2">
            <span class="mono">{{ selectedLog.id }}</span>
          </el-descriptions-item>
          <el-descriptions-item label="任务 ID" :span="2">
            <span class="mono">{{ selectedLog.task_id }}</span>
          </el-descriptions-item>
          <el-descriptions-item label="开始时间">
            {{ formatDateTime(selectedLog.started_at) }}
          </el-descriptions-item>
          <el-descriptions-item label="完成时间">
            {{ formatDateTime(selectedLog.completed_at) || '—' }}
          </el-descriptions-item>
          <el-descriptions-item label="状态">
            <el-tag :type="getStatusType(selectedLog.status)">
              {{ getStatusText(selectedLog.status) }}
            </el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="Token 消耗">
            {{ selectedLog.tokens_used ?? 0 }}
          </el-descriptions-item>
          <el-descriptions-item label="通知渠道" :span="2">
            {{ formatNotifyChannels(selectedLog.notify_channels) }}
          </el-descriptions-item>
        </el-descriptions>

        <div v-if="selectedLog.result" class="detail-section">
          <div class="detail-label">执行结果</div>
          <el-input
            type="textarea"
            :value="selectedLog.result"
            :rows="6"
            readonly
            resize="none"
            class="detail-textarea"
          />
        </div>

        <div v-if="selectedLog.error" class="detail-section">
          <div class="detail-label error-label">错误信息</div>
          <el-input
            type="textarea"
            :value="selectedLog.error"
            :rows="4"
            readonly
            resize="none"
            class="detail-textarea error-textarea"
          />
        </div>

        <div v-if="!selectedLog.result && !selectedLog.error" class="detail-section">
          <el-empty description="暂无详细内容" :image-size="60" />
        </div>
      </div>
      <template #footer>
        <el-button @click="detailVisible = false">关闭</el-button>
        <el-button type="danger" @click="deleteFromDetail(selectedLog)">删除此日志</el-button>
      </template>
    </el-dialog>

    <!-- 聊天日志详情对话框 -->
    <el-dialog
      v-model="chatDetailVisible"
      title="聊天日志详情"
      width="760px"
      top="3vh"
      destroy-on-close
    >
      <div v-if="selectedChatLog" class="log-detail">
        <el-descriptions :column="2" border>
          <el-descriptions-item label="对话" :span="2">
            {{ selectedChatLog.conversation_title || '—' }}
          </el-descriptions-item>
          <el-descriptions-item label="时间" :span="2">
            {{ formatDateTime(selectedChatLog.created_at) }}
          </el-descriptions-item>
          <el-descriptions-item label="模型">
            {{ selectedChatLog.model || '默认' }}
          </el-descriptions-item>
          <el-descriptions-item label="模式">
            {{ selectedChatLog.mode || '默认' }}
          </el-descriptions-item>
        </el-descriptions>

        <div class="detail-section">
          <div class="detail-label">用户消息</div>
          <el-input
            type="textarea"
            :value="selectedChatLog.user_message"
            :rows="3"
            readonly
            resize="none"
            class="detail-textarea"
          />
        </div>

        <div v-if="selectedChatLog.internal_prompt" class="detail-section">
          <div class="detail-label">内部提示词（发送给 OpenCode 的完整 Prompt）</div>
          <el-input
            type="textarea"
            :value="selectedChatLog.internal_prompt"
            :rows="8"
            readonly
            resize="none"
            class="detail-textarea prompt-textarea"
          />
        </div>

        <div v-if="parsedToolEvents(selectedChatLog).length > 0" class="detail-section">
          <div class="detail-label">推理过程（Tool Events）</div>
          <div class="tool-events-list">
            <div
              v-for="(ev, idx) in parsedToolEvents(selectedChatLog)"
              :key="idx"
              class="tool-event-item"
            >
              <span class="event-type-badge">{{ ev.type || 'event' }}</span>
              <span class="event-summary">{{ formatToolEvent(ev) }}</span>
            </div>
          </div>
        </div>

        <div class="detail-section">
          <div class="detail-label">最终回复</div>
          <el-input
            type="textarea"
            :value="selectedChatLog.final_reply"
            :rows="6"
            readonly
            resize="none"
            class="detail-textarea"
          />
        </div>
      </div>
      <template #footer>
        <el-button @click="chatDetailVisible = false">关闭</el-button>
        <el-button type="danger" @click="deleteFromChatDetail(selectedChatLog)">删除此日志</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Delete, View, InfoFilled } from '@element-plus/icons-vue'
import axios from 'axios'

const activeTab = ref('task')

// ── 任务执行日志 ──────────────────────────────────────────────────────────
const logs = ref([])
const tasks = ref([])
const filterTaskId = ref('')
const retentionDays = ref(30)
const chatRetentionDays = ref(30)
const logConfig = ref({
  task_log_retention_days: 30,
  chat_log_retention_days: 30,
  system_log_retention_days: 7,
  log_level: 'INFO'
})

const currentPage = ref(1)
const pageSize = ref(50)
const totalLogs = ref(0)

const selectedLogIds = ref([])
const logTableRef = ref(null)

const detailVisible = ref(false)
const selectedLog = ref(null)

// ── 聊天日志 ──────────────────────────────────────────────────────────────
const chatLogs = ref([])
const chatLogSearch = ref('')
const chatCurrentPage = ref(1)
const chatPageSize = ref(50)
const totalChatLogs = ref(0)

const selectedChatLogIds = ref([])
const chatLogTableRef = ref(null)

const chatDetailVisible = ref(false)
const selectedChatLog = ref(null)

// ── 共用工具函数 ──────────────────────────────────────────────────────────
const getStatusType = (status) => {
  const types = { success: 'success', failed: 'danger', running: 'warning' }
  return types[status] || 'info'
}

const getStatusText = (status) => {
  const texts = { success: '成功', failed: '失败', running: '运行中' }
  return texts[status] || status
}

const formatDateTime = (dateStr) => {
  if (!dateStr) return ''
  try {
    const d = new Date(dateStr)
    return d.toLocaleString('zh-CN', {
      year: 'numeric', month: '2-digit', day: '2-digit',
      hour: '2-digit', minute: '2-digit', second: '2-digit'
    })
  } catch {
    return dateStr
  }
}

const formatNotifyChannels = (channels) => {
  if (!channels) return '无'
  try {
    const arr = typeof channels === 'string' ? JSON.parse(channels) : channels
    return Array.isArray(arr) && arr.length > 0 ? arr.join(', ') : '无'
  } catch {
    return channels || '无'
  }
}

// ── 任务日志操作 ──────────────────────────────────────────────────────────
const loadLogs = async () => {
  try {
    const offset = (currentPage.value - 1) * pageSize.value
    const response = await axios.get('/api/logs/task-logs', {
      params: {
        task_id: filterTaskId.value || undefined,
        limit: pageSize.value,
        offset
      }
    })
    const data = response.data.data
    logs.value = data.items || []
    totalLogs.value = data.total || 0
  } catch (error) {
    ElMessage.error('加载日志失败')
  }
}

const handlePageChange = (page) => {
  currentPage.value = page
  loadLogs()
}

const handlePageSizeChange = (size) => {
  pageSize.value = size
  currentPage.value = 1
  loadLogs()
}

const handleSelectionChange = (selection) => {
  selectedLogIds.value = selection.map(row => row.id)
}

const clearSelection = () => {
  logTableRef.value?.clearSelection()
  selectedLogIds.value = []
}

const viewDetail = (log) => {
  selectedLog.value = log
  detailVisible.value = true
}

const deleteSingleLog = async (log) => {
  try {
    await ElMessageBox.confirm('确定删除该日志吗？', '删除日志', {
      confirmButtonText: '删除',
      cancelButtonText: '取消',
      type: 'warning'
    })
    await axios.delete(`/api/logs/task-logs/${log.id}`)
    ElMessage.success('日志已删除')
    await loadLogs()
  } catch (error) {
    if (error !== 'cancel') {
      ElMessage.error('删除失败')
    }
  }
}

const deleteFromDetail = async (log) => {
  try {
    await ElMessageBox.confirm('确定删除该日志吗？', '删除日志', {
      confirmButtonText: '删除',
      cancelButtonText: '取消',
      type: 'warning'
    })
    await axios.delete(`/api/logs/task-logs/${log.id}`)
    ElMessage.success('日志已删除')
    detailVisible.value = false
    await loadLogs()
  } catch (error) {
    if (error !== 'cancel') {
      ElMessage.error('删除失败')
    }
  }
}

const batchDeleteLogs = async () => {
  if (selectedLogIds.value.length === 0) return
  try {
    await ElMessageBox.confirm(
      `确定删除选中的 ${selectedLogIds.value.length} 条日志吗？`,
      '批量删除',
      {
        confirmButtonText: '删除',
        cancelButtonText: '取消',
        type: 'warning'
      }
    )
    await axios.post('/api/logs/task-logs/batch-delete', { ids: selectedLogIds.value })
    ElMessage.success(`已删除 ${selectedLogIds.value.length} 条日志`)
    clearSelection()
    await loadLogs()
  } catch (error) {
    if (error !== 'cancel') {
      ElMessage.error('批量删除失败')
    }
  }
}

const loadConfig = async () => {
  try {
    const response = await axios.get('/api/logs/config')
    logConfig.value = response.data.data
    retentionDays.value = logConfig.value.task_log_retention_days
    chatRetentionDays.value = logConfig.value.chat_log_retention_days ?? 30
  } catch (error) {
    ElMessage.error('加载配置失败')
  }
}

const saveConfig = async () => {
  try {
    await axios.put('/api/logs/config', {
      task_log_retention_days: retentionDays.value,
      chat_log_retention_days: chatRetentionDays.value,
      system_log_retention_days: logConfig.value.system_log_retention_days,
      log_level: logConfig.value.log_level
    })
    ElMessage.success('配置已保存')
  } catch (error) {
    ElMessage.error('保存失败')
  }
}

const cleanupLogs = async () => {
  try {
    const response = await axios.post('/api/logs/cleanup', null, {
      params: { days: retentionDays.value }
    })
    ElMessage.success(response.data.message || '日志清理完成')
    await loadLogs()
  } catch (error) {
    ElMessage.error('日志清理失败')
  }
}

const cleanupChatLogs = async () => {
  try {
    await ElMessageBox.confirm(
      `确定清理 ${chatRetentionDays.value} 天前的聊天日志吗？`,
      '清理聊天日志',
      {
        confirmButtonText: '确定',
        cancelButtonText: '取消',
        type: 'warning'
      }
    )
    const response = await axios.post('/api/logs/chat-logs/cleanup', null, {
      params: { days: chatRetentionDays.value }
    })
    ElMessage.success(response.data.message || '聊天日志清理完成')
    await loadChatLogs()
  } catch (error) {
    if (error !== 'cancel') {
      ElMessage.error('聊天日志清理失败')
    }
  }
}

const loadTasks = async () => {
  try {
    const response = await axios.get('/api/scheduler/tasks')
    tasks.value = response.data.data || []
  } catch (error) {
    ElMessage.error('加载任务失败')
  }
}

// ── 聊天日志操作 ──────────────────────────────────────────────────────────
const loadChatLogs = async () => {
  try {
    const offset = (chatCurrentPage.value - 1) * chatPageSize.value
    const response = await axios.get('/api/logs/chat-logs', {
      params: {
        limit: chatPageSize.value,
        offset
      }
    })
    const data = response.data.data
    let items = data.items || []
    // 本地搜索过滤
    if (chatLogSearch.value.trim()) {
      const kw = chatLogSearch.value.trim().toLowerCase()
      items = items.filter(
        (item) =>
          (item.user_message || '').toLowerCase().includes(kw) ||
          (item.final_reply || '').toLowerCase().includes(kw) ||
          (item.conversation_title || '').toLowerCase().includes(kw)
      )
    }
    chatLogs.value = items
    totalChatLogs.value = data.total || 0
  } catch (error) {
    ElMessage.error('加载聊天日志失败')
  }
}

const handleChatPageChange = (page) => {
  chatCurrentPage.value = page
  loadChatLogs()
}

const handleChatPageSizeChange = (size) => {
  chatPageSize.value = size
  chatCurrentPage.value = 1
  loadChatLogs()
}

const handleChatSelectionChange = (selection) => {
  selectedChatLogIds.value = selection.map(row => String(row.id))
}

const clearChatSelection = () => {
  chatLogTableRef.value?.clearSelection()
  selectedChatLogIds.value = []
}

const viewChatLogDetail = (log) => {
  selectedChatLog.value = log
  chatDetailVisible.value = true
}

const parsedToolEvents = (log) => {
  if (!log || !log.tool_events) return []
  try {
    const parsed = typeof log.tool_events === 'string' ? JSON.parse(log.tool_events) : log.tool_events
    return Array.isArray(parsed) ? parsed : []
  } catch {
    return []
  }
}

const formatToolEvent = (ev) => {
  if (!ev) return ''
  const data = ev.data || ev
  if (typeof data.text === 'string' && data.text.trim()) return data.text.trim().slice(0, 120)
  if (typeof data.name === 'string' && data.name.trim()) return data.name.trim()
  if (typeof data.tool === 'string' && data.tool.trim()) return data.tool.trim()
  if (typeof data.reason === 'string' && data.reason.trim()) return data.reason.trim().slice(0, 120)
  if (typeof data.summary === 'string' && data.summary.trim()) return data.summary.trim().slice(0, 120)
  return JSON.stringify(data).slice(0, 120)
}

const deleteSingleChatLog = async (log) => {
  try {
    await ElMessageBox.confirm('确定删除该聊天日志吗？', '删除日志', {
      confirmButtonText: '删除',
      cancelButtonText: '取消',
      type: 'warning'
    })
    await axios.delete(`/api/logs/chat-logs/${log.id}`)
    ElMessage.success('聊天日志已删除')
    await loadChatLogs()
  } catch (error) {
    if (error !== 'cancel') {
      ElMessage.error('删除失败')
    }
  }
}

const deleteFromChatDetail = async (log) => {
  try {
    await ElMessageBox.confirm('确定删除该聊天日志吗？', '删除日志', {
      confirmButtonText: '删除',
      cancelButtonText: '取消',
      type: 'warning'
    })
    await axios.delete(`/api/logs/chat-logs/${log.id}`)
    ElMessage.success('聊天日志已删除')
    chatDetailVisible.value = false
    await loadChatLogs()
  } catch (error) {
    if (error !== 'cancel') {
      ElMessage.error('删除失败')
    }
  }
}

const batchDeleteChatLogs = async () => {
  if (selectedChatLogIds.value.length === 0) return
  try {
    await ElMessageBox.confirm(
      `确定删除选中的 ${selectedChatLogIds.value.length} 条聊天日志吗？`,
      '批量删除',
      {
        confirmButtonText: '删除',
        cancelButtonText: '取消',
        type: 'warning'
      }
    )
    await axios.post('/api/logs/chat-logs/batch-delete', { ids: selectedChatLogIds.value })
    ElMessage.success(`已删除 ${selectedChatLogIds.value.length} 条聊天日志`)
    clearChatSelection()
    await loadChatLogs()
  } catch (error) {
    if (error !== 'cancel') {
      ElMessage.error('批量删除失败')
    }
  }
}

onMounted(() => {
  loadTasks()
  loadConfig()
  loadLogs()
  loadChatLogs()
})
</script>

<style scoped>
.logs-view {
  padding: 20px;
  height: calc(100vh - 60px);
  display: flex;
  flex-direction: column;
  box-sizing: border-box;
}

.logs-tabs {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-height: 0;
}

.logs-tabs :deep(.el-tabs__content) {
  flex: 1;
  min-height: 0;
  overflow: hidden;
}

.logs-tabs :deep(.el-tab-pane) {
  height: 100%;
}

.tab-content {
  height: 100%;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.tab-header {
  padding: 0 0 12px 0;
  border-bottom: 1px solid #e4e7ed;
  margin-bottom: 12px;
  display: flex;
  justify-content: flex-end;
  align-items: center;
  flex-shrink: 0;
}

.header-actions {
  display: flex;
  align-items: center;
}

.batch-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: #fdf6ec;
  border: 1px solid #faecd8;
  border-radius: 6px;
  padding: 8px 14px;
  margin-bottom: 10px;
  flex-shrink: 0;
}

.batch-info {
  font-size: 13px;
  color: #e6a23c;
  font-weight: 500;
}

.batch-actions {
  display: flex;
  gap: 8px;
}

.table-wrapper {
  flex: 1;
  min-height: 0;
  overflow: hidden;
  border: 1px solid #e4e7ed;
  border-radius: 6px;
}

.pagination-bar {
  flex-shrink: 0;
  padding: 12px 0 4px 0;
  display: flex;
  justify-content: flex-end;
}

.config-form {
  flex-shrink: 0;
  flex-wrap: wrap;
}

.el-divider {
  flex-shrink: 0;
  margin: 12px 0;
}

.chat-log-tip {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  font-size: 12px;
  color: #909399;
  padding: 4px 0;
}

/* 日志详情样式 */
.log-detail {
  max-height: 70vh;
  overflow-y: auto;
  padding-right: 4px;
}

.detail-section {
  margin-top: 16px;
}

.detail-label {
  font-size: 13px;
  font-weight: 600;
  color: #606266;
  margin-bottom: 6px;
}

.error-label {
  color: #f56c6c;
}

.detail-textarea {
  font-family: Consolas, Monaco, monospace;
  font-size: 13px;
}

.detail-textarea :deep(textarea) {
  background: #f8f9fa;
}

.error-textarea :deep(textarea) {
  background: #fff5f5;
  color: #c0392b;
}

.prompt-textarea :deep(textarea) {
  background: #f0f4ff;
  font-size: 12px;
}

.mono {
  font-family: Consolas, Monaco, monospace;
  font-size: 12px;
  word-break: break-all;
}

.tool-events-list {
  max-height: 200px;
  overflow-y: auto;
  border: 1px solid #e4e7ed;
  border-radius: 4px;
  padding: 8px;
  background: #fafafa;
}

.tool-event-item {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 4px 0;
  border-bottom: 1px solid #f0f0f0;
  font-size: 12px;
}

.tool-event-item:last-child {
  border-bottom: none;
}

.event-type-badge {
  display: inline-block;
  background: #ecf5ff;
  color: #409eff;
  border-radius: 3px;
  padding: 1px 6px;
  font-size: 11px;
  white-space: nowrap;
  flex-shrink: 0;
}

.event-summary {
  color: #606266;
  word-break: break-all;
}
</style>
