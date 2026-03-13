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
      <span v-if="organizeStatus" class="organize-status-text">{{ organizeStatus }}</span>
    </div>
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
const newMemory = ref({
  category: 'habit',
  content: ''
})

const loadMemories = async () => {
  try {
    const params = {}
    if (filterCategory.value) params.category = filterCategory.value
    const response = await axios.get('/api/memory/memories', { params })
    memories.value = response.data.data.items || []
  } catch (error) {
    ElMessage.error('加载记忆失败')
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
</style>

