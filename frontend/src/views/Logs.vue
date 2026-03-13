<template>
  <div class="logs-view">
    <el-header>
      <h2>任务执行日志</h2>
      <div class="header-actions">
        <el-select v-model="filterTaskId" placeholder="全部任务" style="width: 200px">
          <el-option label="全部任务" value="" />
          <el-option v-for="task in tasks" :key="task.id" :label="task.name" :value="task.id" />
        </el-select>
        <el-button @click="loadLogs" style="margin-left: 10px">查询</el-button>
      </div>
    </el-header>

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
      <el-form-item label="日志保留天数">
        <el-input-number v-model="retentionDays" :min="0" :max="365" />
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

    <!-- 日志详情对话框 -->
    <el-dialog
      v-model="detailVisible"
      title="日志详情"
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
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Delete, View } from '@element-plus/icons-vue'
import axios from 'axios'

const logs = ref([])
const tasks = ref([])
const filterTaskId = ref('')
const retentionDays = ref(30)
const logConfig = ref({
  task_log_retention_days: 30,
  system_log_retention_days: 7,
  log_level: 'INFO'
})

// 分页
const currentPage = ref(1)
const pageSize = ref(50)
const totalLogs = ref(0)

// 批量选择
const selectedLogIds = ref([])
const logTableRef = ref(null)

// 详情对话框
const detailVisible = ref(false)
const selectedLog = ref(null)

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
    await ElMessageBox.confirm(`确定删除该日志吗？`, '删除日志', {
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
  } catch (error) {
    ElMessage.error('加载配置失败')
  }
}

const saveConfig = async () => {
  try {
    await axios.put('/api/logs/config', {
      task_log_retention_days: retentionDays.value,
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

const loadTasks = async () => {
  try {
    const response = await axios.get('/api/scheduler/tasks')
    tasks.value = response.data.data || []
  } catch (error) {
    ElMessage.error('加载任务失败')
  }
}

onMounted(() => {
  loadTasks()
  loadConfig()
  loadLogs()
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

.el-header {
  padding: 0 0 16px 0;
  background: none;
  border-bottom: 1px solid #e4e7ed;
  margin-bottom: 12px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-shrink: 0;
  height: auto;
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

/* 日志详情样式 */
.log-detail {
  max-height: 65vh;
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

.mono {
  font-family: Consolas, Monaco, monospace;
  font-size: 12px;
  word-break: break-all;
}
</style>
