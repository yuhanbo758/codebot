<template>
  <div class="skills-view">
    <!-- 固定头部区域 -->
    <div class="skills-header">
      <div class="header-title-row">
        <h2>技能</h2>
        <el-input
          v-model="searchQuery"
          placeholder="搜索技能..."
          clearable
          style="width: 220px; margin: 0 12px 0 auto"
        >
          <template #prefix>
            <el-icon><Search /></el-icon>
          </template>
        </el-input>
        <div class="header-actions">
          <el-button
            v-if="batchMode"
            type="danger"
            :disabled="selectedIds.length === 0"
            :loading="batchDeleting"
            @click="batchDelete"
          >
            批量卸载 ({{ selectedIds.length }})
          </el-button>
          <el-button @click="toggleBatchMode">
            {{ batchMode ? '退出批量' : '批量处理' }}
          </el-button>
          <el-tooltip v-if="ocSyncStatus !== null" :content="ocSyncTooltip" placement="bottom">
            <el-button
              :type="ocSyncStatus?.in_sync ? 'success' : 'warning'"
              plain
              :loading="ocSyncing"
              @click="syncSkillsToOpencode"
            >
              <el-icon><Refresh /></el-icon>
              {{ ocSyncStatus?.in_sync ? 'OpenCode 已同步' : `同步到 OpenCode (${ocSyncStatus?.not_synced?.length ?? '?'} 待同步)` }}
            </el-button>
          </el-tooltip>
          <el-button type="success" @click="showGenerateDialog = true">
            <el-icon><MagicStick /></el-icon>
            生成技能
          </el-button>
          <el-button type="primary" @click="showInstallDialog = true">
            <el-icon><Download /></el-icon>
            安装技能
          </el-button>
        </div>
      </div>
      <div class="skills-subtitle">管理 Codebot 的技能。内置技能和自动生成技能仅供 Codebot 内部使用；OpenCode 技能由 OpenCode CLI 自身管理（位于 ~/.agents/skills/）。</div>
    </div>

    <!-- 可滚动表格区域 -->
    <div class="skills-table-wrapper">
      <el-table
        v-loading="loading"
        :data="filteredSkills"
        style="width: 100%"
        height="100%"
        @selection-change="onSelectionChange"
      >
        <el-table-column v-if="batchMode" type="selection" width="50" />
        <el-table-column prop="name" label="名称" width="200" />
        <el-table-column prop="description" label="描述" min-width="200" />
        <el-table-column prop="version" label="版本" width="90" />
        <el-table-column label="来源" width="100">
          <template #default="{ row }">
            <el-tag size="small" :type="sourceTagType(row.source)">
              {{ sourceLabel(row.id) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="90">
          <template #default="{ row }">
            <el-tag :type="row.enabled ? 'success' : 'info'" size="small">
              {{ row.enabled ? '已启用' : '已禁用' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="220">
          <template #default="{ row }">
            <!-- builtin: 支持编辑 SKILL.md，不支持卸载 -->
            <template v-if="row.id?.startsWith('builtin:')">
              <el-button size="small" type="primary" plain @click="openEditDialog(row)">编辑</el-button>
              <el-text type="info" size="small" style="margin-left:8px">内置</el-text>
            </template>
            <!-- auto: 自动生成，支持编辑和删除 -->
            <template v-else-if="row.id?.startsWith('auto:')">
              <el-button size="small" type="primary" plain @click="openEditDialog(row)">编辑</el-button>
              <el-button size="small" type="danger" plain @click="deleteSkill(row)" :loading="row.deleting">删除</el-button>
            </template>
            <!-- custom: 外部目录只读 -->
            <el-text v-else-if="row.id?.startsWith('custom:')" type="info" size="small">外部只读</el-text>
            <!-- opencode: / 普通 JSON 技能 -->
            <template v-else>
              <el-button
                v-if="isEditable(row)"
                size="small"
                type="primary"
                plain
                @click="openEditDialog(row)"
              >编辑</el-button>
              <el-button
                v-if="!row.id?.startsWith('opencode:')"
                size="small"
                @click="toggleSkill(row)"
                :loading="row.updating"
              >
                {{ row.enabled ? '禁用' : '启用' }}
              </el-button>
              <el-button size="small" type="danger" @click="deleteSkill(row)" :loading="row.deleting">
                卸载
              </el-button>
            </template>
          </template>
        </el-table-column>
      </el-table>

      <el-empty v-if="!loading && filteredSkills.length === 0" description="暂无匹配技能" />
    </div>

    <!-- 安装技能对话框 -->
    <el-dialog v-model="showInstallDialog" title="安装技能" width="560px">
      <el-alert
        type="info"
        :closable="false"
        style="margin-bottom: 16px;"
        show-icon
      >
        <template #default>
          手动安装自定义技能。技能名称为必填项，其余字段可选填。
        </template>
      </el-alert>
      <el-form :model="installForm" label-width="80px" label-position="left">
        <el-form-item label="名称" required>
          <el-input
            v-model="installForm.name"
            placeholder="请输入技能名称（必填）"
            clearable
          />
        </el-form-item>
        <el-form-item label="描述">
          <el-input
            v-model="installForm.description"
            type="textarea"
            :rows="3"
            placeholder="请输入技能描述，帮助了解技能用途（可选）"
          />
        </el-form-item>
        <el-form-item label="版本">
          <el-input
            v-model="installForm.version"
            placeholder="例如：1.0.0"
            style="width: 160px"
          />
        </el-form-item>
        <el-form-item label="来源">
          <el-input
            v-model="installForm.source"
            placeholder="Git 仓库地址或本地路径（可选）"
            clearable
          >
            <template #prefix>
              <el-icon><Link /></el-icon>
            </template>
          </el-input>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showInstallDialog = false">取消</el-button>
        <el-button type="primary" @click="installSkill" :loading="installing">
          <el-icon><Download /></el-icon>
          安装技能
        </el-button>
      </template>
    </el-dialog>

    <!-- 生成技能对话框 -->
    <el-dialog v-model="showGenerateDialog" title="AI 生成技能" width="560px">
      <p style="margin: 0 0 12px; color: #606266; font-size: 14px;">
        描述你需要的技能功能，AI 将自动生成完整的 SKILL.md 文件并保存。
      </p>
      <el-input
        v-model="generateDescription"
        type="textarea"
        :rows="5"
        placeholder="例如：一个可以读取 Excel 文件并提取指定列数据的技能，支持多种格式转换..."
        :disabled="generating"
      />
      <template #footer>
        <el-button @click="showGenerateDialog = false" :disabled="generating">取消</el-button>
        <el-button type="success" @click="generateSkill" :loading="generating">
          {{ generating ? 'AI 生成中...' : '生成技能' }}
        </el-button>
      </template>
    </el-dialog>
    <!-- 编辑技能对话框（全文 SKILL.md 编辑器） -->
    <el-dialog v-model="showEditDialog" title="编辑 SKILL.md" width="800px" :fullscreen="editFullscreen">
      <div style="display:flex; justify-content:flex-end; margin-bottom:8px; gap:8px">
        <el-button size="small" @click="editFullscreen = !editFullscreen">
          {{ editFullscreen ? '退出全屏' : '全屏编辑' }}
        </el-button>
      </div>
      <el-input
        v-model="editForm.content"
        type="textarea"
        :rows="editFullscreen ? 30 : 20"
        placeholder="SKILL.md 内容..."
        :disabled="editLoading"
        style="font-family: monospace; font-size: 13px"
      />
      <div v-if="editLoadError" style="color:#f56c6c; margin-top:8px; font-size:13px">{{ editLoadError }}</div>
      <template #footer>
        <el-button @click="showEditDialog = false" :disabled="editSaving">取消</el-button>
        <el-button type="primary" @click="saveEditSkill" :loading="editSaving">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Download, Search, MagicStick, Link, Refresh } from '@element-plus/icons-vue'
import axios from 'axios'

const skills = ref([])
const loading = ref(false)
const installing = ref(false)
const generating = ref(false)
const batchDeleting = ref(false)
const batchMode = ref(false)
const selectedIds = ref([])
const searchQuery = ref('')

const showInstallDialog = ref(false)
const showGenerateDialog = ref(false)
const generateDescription = ref('')

// ── 编辑技能 ──────────────────────────────────────────────────────────────
const showEditDialog = ref(false)
const editSaving = ref(false)
const editLoading = ref(false)
const editLoadError = ref('')
const editFullscreen = ref(false)
const editForm = ref({ id: '', content: '' })

/**
 * 支持编辑的技能类型：
 *  - builtin: / auto: / opencode: → SKILL.md 全文编辑
 *  - custom:                      → 外部目录只读
 *  - 普通 JSON（source=chat）     → 也走全文编辑（content 为空时可填写）
 */
const isEditable = (row) => {
  if (row.id?.startsWith('custom:')) return false   // 外部目录只读
  if (row.id?.startsWith('builtin:')) return true
  if (row.id?.startsWith('auto:')) return true
  if (row.id?.startsWith('opencode:')) return true
  return row.source === 'chat'
}

const openEditDialog = async (row) => {
  editForm.value = { id: row.id, content: '' }
  editLoadError.value = ''
  editFullscreen.value = false
  showEditDialog.value = true
  editLoading.value = true
  try {
    const res = await axios.get(`/api/skills/${encodeURIComponent(row.id)}/content`)
    editForm.value.content = res.data?.data?.content || ''
  } catch (err) {
    editLoadError.value = err?.response?.data?.detail || '无法加载 SKILL.md 内容'
  } finally {
    editLoading.value = false
  }
}

const saveEditSkill = async () => {
  editSaving.value = true
  try {
    await axios.put(`/api/skills/${encodeURIComponent(editForm.value.id)}/content`, {
      content: editForm.value.content,
    })
    ElMessage.success('技能已更新')
    showEditDialog.value = false
    await loadSkills()
  } catch (err) {
    ElMessage.error(err?.response?.data?.detail || '更新失败')
  } finally {
    editSaving.value = false
  }
}

const installForm = ref({
  name: '',
  description: '',
  version: '1.0.0',
  source: ''
})

const filteredSkills = computed(() => {
  const q = searchQuery.value.trim().toLowerCase()
  if (!q) return skills.value
  return skills.value.filter(s =>
    (s.name || '').toLowerCase().includes(q) ||
    (s.description || '').toLowerCase().includes(q)
  )
})

const sourceLabel = (id) => {
  if (!id) return '自定义'
  if (id.startsWith('builtin:')) return '内置'
  if (id.startsWith('auto:')) return '自动生成'
  if (id.startsWith('opencode:')) return 'OpenCode'
  if (id.startsWith('custom:')) return '外部目录'
  return '自定义'
}

const sourceTagType = (source) => {
  if (source === 'builtin') return 'warning'
  if (source === 'auto') return 'success'
  if (source === 'opencode') return 'info'
  if (source === 'custom') return ''
  return 'primary'
}

const loadSkills = async () => {
  loading.value = true
  try {
    const response = await axios.get('/api/skills')
    skills.value = response.data.data.items || []
  } catch (error) {
    ElMessage.error('加载技能列表失败')
  } finally {
    loading.value = false
  }
}

const installSkill = async () => {
  if (!installForm.value.name.trim()) {
    ElMessage.warning('请输入技能名称')
    return
  }
  installing.value = true
  try {
    await axios.post('/api/skills', {
      name: installForm.value.name,
      description: installForm.value.description,
      version: installForm.value.version || '1.0.0',
      source: installForm.value.source || null
    })
    ElMessage.success('技能已安装')
    showInstallDialog.value = false
    installForm.value = { name: '', description: '', version: '1.0.0', source: '' }
    await loadSkills()
  } catch (error) {
    ElMessage.error('安装失败')
  } finally {
    installing.value = false
  }
}

const generateSkill = async () => {
  if (!generateDescription.value.trim()) {
    ElMessage.warning('请输入技能描述')
    return
  }
  generating.value = true
  try {
    const response = await axios.post('/api/skills/generate', {
      description: generateDescription.value.trim()
    })
    ElMessage.success(response.data.message || '技能已生成')
    showGenerateDialog.value = false
    generateDescription.value = ''
    await loadSkills()
  } catch (error) {
    const msg = error?.response?.data?.detail || '生成失败，请重试'
    ElMessage.error(msg)
  } finally {
    generating.value = false
  }
}

const toggleSkill = async (skill) => {
  try {
    skill.updating = true
    await axios.patch(`/api/skills/${skill.id}`, {
      enabled: !skill.enabled
    })
    skill.enabled = !skill.enabled
    ElMessage.success(skill.enabled ? '已启用' : '已禁用')
  } catch (error) {
    ElMessage.error('更新状态失败')
  } finally {
    skill.updating = false
  }
}

const deleteSkill = async (skill) => {
  try {
    await ElMessageBox.confirm('确定卸载该技能吗？', '卸载技能', {
      confirmButtonText: '卸载',
      cancelButtonText: '取消',
      type: 'warning'
    })
    skill.deleting = true
    await axios.delete(`/api/skills/${skill.id}`)
    ElMessage.success('技能已卸载')
    await loadSkills()
  } catch (error) {
    if (error !== 'cancel') {
      ElMessage.error('卸载失败')
    }
  } finally {
    skill.deleting = false
  }
}

const toggleBatchMode = () => {
  batchMode.value = !batchMode.value
  selectedIds.value = []
}

const onSelectionChange = (rows) => {
  selectedIds.value = rows.map(r => r.id)
}

const batchDelete = async () => {
  const deletable = selectedIds.value.filter(id => !id.startsWith('builtin:') && !id.startsWith('custom:'))
  const skippedCount = selectedIds.value.length - deletable.length

  if (deletable.length === 0) {
    ElMessage.warning('所选技能均为内置或外部只读技能，无法卸载')
    return
  }

  const tip = skippedCount > 0
    ? `将卸载 ${deletable.length} 个技能（${skippedCount} 个内置/外部技能将被跳过）`
    : `将卸载 ${deletable.length} 个技能`

  try {
    await ElMessageBox.confirm(tip, '批量卸载', {
      confirmButtonText: '确认卸载',
      cancelButtonText: '取消',
      type: 'warning'
    })
    batchDeleting.value = true
    const response = await axios.post('/api/skills/batch-delete', { ids: selectedIds.value })
    ElMessage.success(response.data.message || '批量卸载完成')
    selectedIds.value = []
    batchMode.value = false
    await loadSkills()
  } catch (error) {
    if (error !== 'cancel') {
      ElMessage.error('批量卸载失败')
    }
  } finally {
    batchDeleting.value = false
  }
}

// ── opencode 同步状态 ─────────────────────────────────────────────────────
const ocSyncStatus = ref(null)
const ocSyncing = ref(false)

const ocSyncTooltip = computed(() => {
  if (!ocSyncStatus.value) return ''
  if (ocSyncStatus.value.in_sync) return 'Codebot 提供给 OpenCode 的技能已全部同步'
  const names = (ocSyncStatus.value.not_synced || []).join(', ')
  return `点击将以下技能同步到 OpenCode:\n${names}`
})

const loadOcSyncStatus = async () => {
  try {
    const res = await axios.get('/api/skills/opencode-sync-status')
    ocSyncStatus.value = res.data?.data ?? null
  } catch {
    ocSyncStatus.value = null
  }
}

const syncSkillsToOpencode = async () => {
  ocSyncing.value = true
  try {
    const res = await axios.post('/api/skills/sync-to-opencode')
    ElMessage.success(res.data?.message || '同步成功')
    await loadOcSyncStatus()
  } catch (err) {
    ElMessage.error(err?.response?.data?.detail || '同步失败')
  } finally {
    ocSyncing.value = false
  }
}

onMounted(() => {
  loadSkills()
  loadOcSyncStatus()
})
</script>

<style scoped>
.skills-view {
  display: flex;
  flex-direction: column;
  height: 100%;
  overflow: hidden;
  padding: 0;
}

.skills-header {
  flex-shrink: 0;
  padding: 16px 20px;
  background: #fff;
  border-bottom: 1px solid #e4e7ed;
}

.header-title-row {
  display: flex;
  align-items: center;
  gap: 0;
}

.header-title-row h2 {
  margin: 0;
  font-size: 18px;
  flex-shrink: 0;
}

.header-actions {
  display: flex;
  gap: 8px;
  align-items: center;
  flex-shrink: 0;
}

.skills-subtitle {
  margin-top: 10px;
  font-size: 13px;
  color: #606266;
}

.skills-table-wrapper {
  flex: 1;
  overflow-y: auto;
  padding: 0 20px 20px 20px;
  min-height: 0;
}
</style>
