<template>
  <div class="active-memories">
    <div class="memory-actions">
      <el-select
        v-model="filterCategory"
        placeholder="全部类别"
        clearable
        size="small"
        style="width: 140px; margin-right: 8px;"
        @change="loadMemories"
      >
        <el-option
          v-for="cat in CATEGORIES"
          :key="cat.value"
          :label="cat.label"
          :value="cat.value"
        />
      </el-select>
      <el-button type="primary" @click="showCreateDialog = true">
        新增记忆
      </el-button>
      <el-button
        type="warning"
        plain
        :loading="organizing"
        style="margin-left: 8px"
        @click="triggerOrganize"
      >
        整理记忆
      </el-button>
      <el-button
        type="info"
        plain
        :loading="selfChecking"
        style="margin-left: 8px"
        @click="runMemorySelfCheck"
      >
        一键自检
      </el-button>
      <span v-if="organizeStatus" class="organize-status-text">{{ organizeStatus }}</span>
    </div>
    <el-alert
      v-if="storageStatus"
      class="storage-alert"
      type="info"
      :closable="false"
      show-icon
      :title="storageStatus"
    />
    <el-table v-if="memories.length > 0" :data="memories" style="width: 100%">
      <el-table-column prop="category" label="类别" width="120">
        <template #default="{ row }">
          <el-tag :type="categoryTagType(row.category)" size="small">
            {{ categoryLabel(row.category) }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="content" label="内容" />
      <el-table-column prop="created_at" label="创建时间" width="180" />
      <el-table-column label="操作" width="200">
        <template #default="{ row }">
          <el-button size="small" type="primary" @click="openEditDialog(row)">编辑</el-button>
          <el-button size="small" @click="archiveMemory(row.id)">归档</el-button>
          <el-button size="small" type="danger" @click="deleteMemory(row.id)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>
    <el-empty v-else description="暂无记忆" />
    <el-dialog v-model="showCreateDialog" title="新增记忆" width="520px">
      <el-form label-width="80px">
        <el-form-item label="类别">
          <el-select v-model="newMemory.category" placeholder="选择类别" style="width: 100%;">
            <el-option
              v-for="cat in CATEGORIES"
              :key="cat.value"
              :label="cat.label"
              :value="cat.value"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="内容">
          <el-input v-model="newMemory.content" type="textarea" :rows="4" placeholder="请输入记忆内容" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showCreateDialog = false">取消</el-button>
        <el-button type="primary" @click="createMemory" :loading="creating">保存</el-button>
      </template>
    </el-dialog>
    <el-dialog v-model="showSelfCheckDialog" title="记忆自检结果" width="560px">
      <el-descriptions :column="1" border>
        <el-descriptions-item label="检查结论">
          <el-tag :type="selfCheckResult.statusType">{{ selfCheckResult.statusText }}</el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="数据库路径">
          <span class="selfcheck-value">{{ selfCheckResult.dbPath || '-' }}</span>
        </el-descriptions-item>
        <el-descriptions-item label="长期记忆(活跃/总数)">
          <span class="selfcheck-value">
            {{ selfCheckResult.activeLongTermCount }} / {{ selfCheckResult.longTermCount }}
          </span>
        </el-descriptions-item>
        <el-descriptions-item label="事实记忆(活跃/总数)">
          <span class="selfcheck-value">
            {{ selfCheckResult.activeFactsCount }} / {{ selfCheckResult.factsCount }}
          </span>
        </el-descriptions-item>
        <el-descriptions-item label="接口读链路">
          <span class="selfcheck-value">{{ selfCheckResult.listApiReadable ? '正常' : '异常' }}</span>
        </el-descriptions-item>
        <el-descriptions-item label="建议">
          <span class="selfcheck-value">{{ selfCheckResult.advice }}</span>
        </el-descriptions-item>
      </el-descriptions>
      <template #footer>
        <el-button type="primary" @click="showSelfCheckDialog = false">关闭</el-button>
      </template>
    </el-dialog>
    <el-dialog v-model="showEditDialog" title="编辑记忆" width="520px">
      <el-form label-width="80px">
        <el-form-item label="类别">
          <el-select v-model="editMemory.category" placeholder="选择类别" style="width: 100%;">
            <el-option
              v-for="cat in CATEGORIES"
              :key="cat.value"
              :label="cat.label"
              :value="cat.value"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="内容">
          <el-input v-model="editMemory.content" type="textarea" :rows="5" placeholder="请输入记忆内容" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showEditDialog = false">取消</el-button>
        <el-button type="primary" @click="saveEditMemory" :loading="savingEdit">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import axios from 'axios'

const CATEGORIES = [
  { value: 'habit',      label: '习惯' },
  { value: 'preference', label: '偏好' },
  { value: 'profile',    label: '个人信息' },
  { value: 'note',       label: '笔记' },
  { value: 'contact',    label: '联系人' },
  { value: 'address',    label: '地址' },
]

const CATEGORY_TAG_TYPE = {
  habit:      'success',
  preference: 'warning',
  profile:    'info',
  note:       '',
  contact:    'danger',
  address:    'danger',
}

const categoryLabel = (cat) => CATEGORIES.find(c => c.value === cat)?.label ?? cat
const categoryTagType = (cat) => CATEGORY_TAG_TYPE[cat] ?? 'info'

const memories = ref([])
const filterCategory = ref('')
const showCreateDialog = ref(false)
const creating = ref(false)
const organizing = ref(false)
const organizeStatus = ref('')
const storageStatus = ref('')
const selfChecking = ref(false)
const showSelfCheckDialog = ref(false)
const showEditDialog = ref(false)
const savingEdit = ref(false)
const editMemory = ref({
  id: null,
  category: 'habit',
  content: ''
})
const selfCheckResult = ref({
  statusText: '未检查',
  statusType: 'info',
  dbPath: '',
  longTermCount: 0,
  activeLongTermCount: 0,
  factsCount: 0,
  activeFactsCount: 0,
  listApiReadable: false,
  advice: '',
})
const newMemory = ref({
  category: 'habit',
  content: ''
})

const loadMemories = async () => {
  try {
    const params = {}
    if (filterCategory.value) params.category = filterCategory.value
    params.with_stats = true
    const response = await axios.get('/api/memory/memories', { params })
    memories.value = response.data.data.items || []
    const meta = response.data.meta || {}
    const counts = meta.storage_counts || {}
    const activeCount = Number(counts.active_long_term_memories || 0)
    const factCount = Number(counts.active_facts || 0)
    const syncedCount = Number(meta.synced_from_facts || 0)
    if (memories.value.length === 0) {
      const base = `当前数据库活跃长期记忆 ${activeCount} 条，活跃事实记忆 ${factCount} 条`
      storageStatus.value = syncedCount > 0 ? `${base}，已自动同步 ${syncedCount} 条到长期记忆。` : `${base}。`
    } else {
      storageStatus.value = ''
    }
  } catch (error) {
    ElMessage.error('加载记忆失败')
    storageStatus.value = ''
  }
}

const archiveMemory = async (id) => {
  try {
    await axios.post(`/api/memory/memories/${id}/archive`)
    ElMessage.success('已归档')
    loadMemories()
  } catch (error) {
    ElMessage.error('归档失败')
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

const openEditDialog = (row) => {
  editMemory.value = {
    id: row.id,
    category: row.category || 'note',
    content: row.content || ''
  }
  showEditDialog.value = true
}

const saveEditMemory = async () => {
  if (!String(editMemory.value.content || '').trim()) {
    ElMessage.warning('请输入记忆内容')
    return
  }
  savingEdit.value = true
  try {
    await axios.patch(`/api/memory/memories/${editMemory.value.id}`, {
      category: editMemory.value.category || 'note',
      content: editMemory.value.content
    })
    ElMessage.success('记忆已更新')
    showEditDialog.value = false
    await loadMemories()
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || '更新记忆失败')
  } finally {
    savingEdit.value = false
  }
}

const createMemory = async () => {
  if (!newMemory.value.content.trim()) {
    ElMessage.warning('请输入记忆内容')
    return
  }
  creating.value = true
  try {
    await axios.post('/api/memory/memories', {
      category: newMemory.value.category || 'habit',
      content: newMemory.value.content
    })
    ElMessage.success('记忆已保存')
    showCreateDialog.value = false
    newMemory.value = { category: 'habit', content: '' }
    loadMemories()
  } catch (error) {
    ElMessage.error('保存记忆失败')
  } finally {
    creating.value = false
  }
}

const triggerOrganize = async () => {
  try {
    await ElMessageBox.confirm(
      'AI 将对当前所有活跃记忆进行一次整理（合并重复、补全描述、修正矛盾）。整理在后台运行，不影响正常使用。确认继续？',
      '整理记忆',
      { type: 'info', confirmButtonText: '开始整理', cancelButtonText: '取消' }
    )
  } catch {
    return
  }

  organizing.value = true
  organizeStatus.value = ''
  try {
    const resp = await axios.post('/api/memory/organize')
    if (resp.data.success) {
      organizeStatus.value = '整理任务已在后台运行...'
      ElMessage.success('整理任务已启动，完成后列表将自动更新')
      // 轮询状态，完成后刷新列表
      pollOrganizeStatus()
    } else {
      organizeStatus.value = ''
      ElMessage.warning(resp.data.message || '整理任务启动失败')
    }
  } catch (error) {
    organizeStatus.value = ''
    ElMessage.error(error.response?.data?.detail || '触发整理失败')
  } finally {
    organizing.value = false
  }
}

const runMemorySelfCheck = async () => {
  selfChecking.value = true
  try {
    const [statusResp, listResp] = await Promise.all([
      axios.get('/api/memory/storage-status'),
      axios.get('/api/memory/memories', {
        params: { with_stats: true, limit: 1, offset: 0 }
      })
    ])
    const counts = statusResp.data?.data?.counts || {}
    const dbPath = String(statusResp.data?.data?.db_path || '')
    const listApiReadable = Boolean(listResp.data?.success)
    const activeLongTermCount = Number(counts.active_long_term_memories || 0)
    const activeFactsCount = Number(counts.active_facts || 0)
    const longTermCount = Number(counts.long_term_memories || 0)
    const factsCount = Number(counts.facts || 0)
    let statusText = '通过'
    let statusType = 'success'
    let advice = '记忆存储与读取链路正常。'

    if (!dbPath || !listApiReadable) {
      statusText = '失败'
      statusType = 'danger'
      advice = '接口读链路异常，请检查后端日志与数据库文件权限。'
    } else if (activeLongTermCount === 0 && activeFactsCount > 0) {
      statusText = '警告'
      statusType = 'warning'
      advice = '仅存在事实记忆，建议先打开活跃记忆页触发自动同步。'
    } else if (activeLongTermCount === 0 && activeFactsCount === 0) {
      statusText = '提示'
      statusType = 'info'
      advice = '当前暂无记忆数据，请先在聊天中触发记忆保存。'
    }

    selfCheckResult.value = {
      statusText,
      statusType,
      dbPath,
      longTermCount,
      activeLongTermCount,
      factsCount,
      activeFactsCount,
      listApiReadable,
      advice,
    }
    showSelfCheckDialog.value = true
  } catch (error) {
    selfCheckResult.value = {
      statusText: '失败',
      statusType: 'danger',
      dbPath: '',
      longTermCount: 0,
      activeLongTermCount: 0,
      factsCount: 0,
      activeFactsCount: 0,
      listApiReadable: false,
      advice: error.response?.data?.detail || '自检失败，请检查后端服务状态。',
    }
    showSelfCheckDialog.value = true
  } finally {
    selfChecking.value = false
  }
}

const pollOrganizeStatus = async () => {
  const maxAttempts = 60  // 最多轮询 5 分钟（每 5 秒一次）
  let attempts = 0
  const timer = setInterval(async () => {
    attempts++
    try {
      const resp = await axios.get('/api/memory/organize/status')
      const data = resp.data.data || {}
      if (!data.running) {
        clearInterval(timer)
        organizeStatus.value = data.organize_last_run
          ? `上次整理：${new Date(data.organize_last_run).toLocaleString('zh-CN')}`
          : ''
        loadMemories()
      }
    } catch {
      // 忽略轮询中的网络错误
    }
    if (attempts >= maxAttempts) {
      clearInterval(timer)
      organizeStatus.value = ''
    }
  }, 5000)
}

onMounted(loadMemories)
</script>

<style scoped>
.memory-actions {
  margin-bottom: 16px;
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 4px;
}
.organize-status-text {
  font-size: 12px;
  color: #909399;
  margin-left: 8px;
}
.storage-alert {
  margin-bottom: 12px;
}
.selfcheck-value {
  word-break: break-all;
}
</style>
