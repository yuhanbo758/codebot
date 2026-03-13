<template>
  <div class="mcp-view">

    <!-- 顶部区域 -->
    <div class="mcp-header">
      <div class="header-left">
        <h2 class="page-title">MCP 服务器</h2>
        <span class="page-subtitle">通过 Model Context Protocol 为 AI 扩展工具能力</span>
      </div>
      <div class="header-right">
        <el-input
          v-model="searchQuery"
          placeholder="搜索..."
          clearable
          class="search-input"
        >
          <template #prefix><el-icon><Search /></el-icon></template>
        </el-input>
        <el-button type="primary" @click="openAddDialog">
          <el-icon><Plus /></el-icon>
          添加服务器
        </el-button>
      </div>
    </div>

    <!-- 主体内容 -->
    <div class="mcp-body">

      <!-- ModelScope MCP Hub 卡片 -->
      <div class="ms-card" :class="{ 'ms-card--no-key': !msApiKey }">
        <div class="ms-card-header">
          <div class="ms-brand">
            <div class="ms-brand-dot"></div>
            <span class="ms-brand-name">ModelScope MCP Hub</span>
            <el-tag size="small" class="ms-badge">远程 · 免安装</el-tag>
          </div>
          <el-tooltip
            content="只需填写 API Key，即可接入 ModelScope 托管的所有 MCP 服务，无需本地安装任何程序。"
            placement="top"
          >
            <el-icon class="ms-help"><QuestionFilled /></el-icon>
          </el-tooltip>
        </div>

        <!-- 未配置 key -->
        <div v-if="!msApiKey" class="ms-no-key-body">
          <el-icon class="ms-warn-icon"><Warning /></el-icon>
          <span>
            尚未配置 API Key，请前往
            <router-link to="/settings" class="link">设置 → 集成配置</router-link>
            填写 ModelScope API Key 后使用。
          </span>
        </div>

        <!-- 已配置 key -->
        <div v-else class="ms-add-body">
          <div class="ms-input-row">
            <el-input
              v-model="msServiceName"
              placeholder="服务名称，例如：mcp-server-time"
              clearable
              @keyup.enter="addMsServer"
              class="ms-svc-input"
            >
              <template #prefix><el-icon style="color:#909399"><Connection /></el-icon></template>
            </el-input>
            <el-input
              v-model="msDisplayName"
              placeholder="显示名称（可选）"
              clearable
              class="ms-name-input"
            />
            <el-button type="primary" @click="addMsServer" :loading="msAdding" class="ms-add-btn">
              <el-icon><Plus /></el-icon> 添加
            </el-button>
          </div>
          <div class="ms-hint">
            服务 slug 即 Hub 上的服务标识，API Key 将自动注入。
            <a href="https://www.modelscope.cn/mcp" target="_blank" class="link">
              浏览全部可用服务 <el-icon style="font-size:11px;vertical-align:-1px"><TopRight /></el-icon>
            </a>
          </div>
        </div>
      </div>

      <!-- 服务器列表 -->
      <div class="server-list" v-loading="loading">

        <!-- 有数据时的表头 -->
        <div v-if="filteredServers.length > 0" class="list-header">
          <span class="col-name">名称</span>
          <span class="col-desc">描述</span>
          <span class="col-type">类型</span>
          <span class="col-cmd">命令 / URL</span>
          <span class="col-status">状态</span>
          <span class="col-actions">操作</span>
        </div>

        <!-- 服务器行 -->
        <div
          v-for="server in filteredServers"
          :key="server.id"
          class="server-row"
          :class="{ 'server-row--disabled': !server.enabled }"
        >
          <!-- 名称 + 标识符 -->
          <div class="col-name">
            <div class="server-name">{{ server.name }}</div>
            <div v-if="server.transport === 'sse' && server.url?.includes('modelscope')" class="server-badge-ms">
              ModelScope
            </div>
          </div>

          <!-- 描述 -->
          <div class="col-desc">
            <span class="desc-text" :title="server.description">{{ server.description || '—' }}</span>
          </div>

          <!-- 类型 -->
          <div class="col-type">
            <span class="type-badge" :class="server.transport === 'sse' ? 'type-sse' : 'type-stdio'">
              {{ server.transport === 'sse' ? 'SSE' : 'stdio' }}
            </span>
          </div>

          <!-- 命令 / URL -->
          <div class="col-cmd">
            <code v-if="server.transport === 'stdio' && server.command" class="cmd-text">
              {{ server.command }} {{ (server.args || []).join(' ') }}
            </code>
            <code v-else-if="server.transport === 'sse' && server.url" class="cmd-text">
              {{ server.url }}
            </code>
            <span v-else class="text-muted">—</span>
          </div>

          <!-- 状态开关 -->
          <div class="col-status">
            <el-switch
              :model-value="server.enabled"
              @change="toggleServer(server)"
              :loading="server._toggling"
              size="small"
            />
          </div>

          <!-- 操作：编辑 + 更多下拉 -->
          <div class="col-actions">
            <el-button
              size="small"
              type="primary"
              plain
              @click="openEditDialog(server)"
              class="action-edit"
            >
              <el-icon><Edit /></el-icon>
            </el-button>
            <el-dropdown trigger="click" @command="(cmd) => handleRowAction(cmd, server)">
              <el-button size="small" class="action-more">
                <el-icon><MoreFilled /></el-icon>
              </el-button>
              <template #dropdown>
                <el-dropdown-menu>
                  <el-dropdown-item command="toggle">
                    {{ server.enabled ? '禁用' : '启用' }}
                  </el-dropdown-item>
                  <el-dropdown-item command="delete" divided style="color:#f56c6c">
                    删除
                  </el-dropdown-item>
                </el-dropdown-menu>
              </template>
            </el-dropdown>
          </div>
        </div>

        <!-- 空状态 -->
        <div v-if="!loading && filteredServers.length === 0" class="empty-state">
          <div class="empty-icon">
            <el-icon><Connection /></el-icon>
          </div>
          <div class="empty-title">尚无 MCP 服务器</div>
          <div class="empty-desc">
            点击右上角「添加服务器」手动配置，或直接在
            <router-link to="/" class="link">聊天</router-link>
            中说"添加 MCP 服务器，命令=npx ..."
          </div>
        </div>
      </div>

    </div>

    <!-- 添加/编辑对话框 -->
    <el-dialog
      v-model="showDialog"
      :title="editingServer ? '编辑 MCP 服务器' : '添加 MCP 服务器'"
      width="580px"
      :close-on-click-modal="false"
    >
      <el-form :model="form" label-width="90px" label-position="left" :rules="rules" ref="formRef">

        <el-form-item label="名称" prop="name">
          <el-input v-model="form.name" placeholder="例如：filesystem" clearable />
        </el-form-item>

        <el-form-item label="描述">
          <el-input v-model="form.description" placeholder="简短描述此 MCP 服务器的功能（可选）" clearable />
        </el-form-item>

        <el-form-item label="连接类型" prop="transport">
          <el-radio-group v-model="form.transport">
            <el-radio value="stdio">stdio（本地进程）</el-radio>
            <el-radio value="sse">SSE（HTTP 远程）</el-radio>
          </el-radio-group>
        </el-form-item>

        <template v-if="form.transport === 'stdio'">
          <el-form-item label="命令" prop="command">
            <el-input v-model="form.command" placeholder="例如：npx 或 uvx" clearable />
          </el-form-item>
          <el-form-item label="参数">
            <el-input
              v-model="form.argsText"
              placeholder="-y @modelcontextprotocol/server-filesystem /path"
              clearable
            />
            <div class="form-hint">多个参数用空格隔开</div>
          </el-form-item>
        </template>

        <template v-else>
          <el-form-item label="URL" prop="url">
            <el-input v-model="form.url" placeholder="例如：http://localhost:3000/sse" clearable>
              <template #prefix><el-icon><Link /></el-icon></template>
            </el-input>
          </el-form-item>
        </template>

        <el-form-item label="环境变量">
          <div class="env-editor">
            <div v-for="(pair, idx) in envPairs" :key="idx" class="env-row">
              <el-input v-model="pair.key" placeholder="KEY" style="width:150px" size="small" />
              <span class="env-eq">=</span>
              <el-input v-model="pair.value" placeholder="VALUE" style="flex:1" size="small" />
              <el-button size="small" type="danger" plain :icon="Delete" @click="removeEnvPair(idx)" style="margin-left:4px" />
            </div>
            <el-button size="small" @click="addEnvPair" style="margin-top:6px">
              <el-icon><Plus /></el-icon> 添加变量
            </el-button>
          </div>
          <div class="form-hint">为进程注入额外的环境变量（可选）</div>
        </el-form-item>

        <el-form-item label="启用">
          <el-switch v-model="form.enabled" />
        </el-form-item>
      </el-form>

      <template #footer>
        <el-button @click="showDialog = false">取消</el-button>
        <el-button type="primary" @click="submitForm" :loading="saving">
          {{ editingServer ? '保存更改' : '添加服务器' }}
        </el-button>
      </template>
    </el-dialog>

  </div>
</template>

<script setup>
import { ref, computed, onMounted, reactive } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  Search, Plus, Link, Delete, QuestionFilled, Warning,
  Connection, TopRight, Edit, MoreFilled
} from '@element-plus/icons-vue'
import axios from 'axios'

// ── 数据 ──────────────────────────────────────────────────────────────────
const servers = ref([])
const loading = ref(false)
const saving = ref(false)
const searchQuery = ref('')

// ModelScope
const msApiKey = ref('')
const msServiceName = ref('')
const msDisplayName = ref('')
const msAdding = ref(false)

// 对话框
const showDialog = ref(false)
const editingServer = ref(null)
const formRef = ref(null)

// ── 表单 ──────────────────────────────────────────────────────────────────
const defaultForm = () => ({
  name: '', description: '', transport: 'stdio',
  command: '', argsText: '', url: '', enabled: true,
})
const form = reactive(defaultForm())
const envPairs = ref([])

const rules = {
  name: [{ required: true, message: '名称不能为空', trigger: 'blur' }],
  command: [{
    validator: (rule, value, cb) => {
      if (form.transport === 'stdio' && !value?.trim()) cb(new Error('stdio 模式下命令不能为空'))
      else cb()
    }, trigger: 'blur',
  }],
  url: [{
    validator: (rule, value, cb) => {
      if (form.transport === 'sse' && !value?.trim()) cb(new Error('SSE 模式下 URL 不能为空'))
      else cb()
    }, trigger: 'blur',
  }],
}

// ── 计算 ──────────────────────────────────────────────────────────────────
const filteredServers = computed(() => {
  const q = searchQuery.value.trim().toLowerCase()
  if (!q) return servers.value
  return servers.value.filter(s =>
    (s.name || '').toLowerCase().includes(q) ||
    (s.description || '').toLowerCase().includes(q) ||
    (s.command || '').toLowerCase().includes(q) ||
    (s.url || '').toLowerCase().includes(q)
  )
})

// ── 加载 ──────────────────────────────────────────────────────────────────
const loadServers = async () => {
  loading.value = true
  try {
    const res = await axios.get('/api/mcp')
    servers.value = (res.data.data.items || []).map(s => ({ ...s, _toggling: false, _deleting: false }))
  } catch {
    ElMessage.error('加载 MCP 服务器列表失败')
  } finally {
    loading.value = false
  }
}

const loadMsApiKey = async () => {
  try {
    const res = await axios.get('/api/config/integration')
    msApiKey.value = res.data?.data?.modelscope_api_key || ''
  } catch {
    msApiKey.value = ''
  }
}

// ── ModelScope ────────────────────────────────────────────────────────────
const addMsServer = async () => {
  const svc = msServiceName.value.trim()
  if (!svc) { ElMessage.warning('请输入服务名称'); return }
  msAdding.value = true
  try {
    const url = `https://mcp.api-inference.modelscope.cn/sse/${svc}`
    const name = msDisplayName.value.trim() || svc
    await axios.post('/api/mcp', {
      name,
      description: `ModelScope MCP: ${svc}`,
      transport: 'sse',
      url,
      env: { MODELSCOPE_API_KEY: msApiKey.value },
      enabled: true,
    })
    ElMessage.success(`ModelScope MCP「${name}」已添加`)
    msServiceName.value = ''
    msDisplayName.value = ''
    await loadServers()
  } catch (err) {
    ElMessage.error(err?.response?.data?.detail || '添加失败')
  } finally {
    msAdding.value = false
  }
}

// ── 表格行操作 ─────────────────────────────────────────────────────────────
const handleRowAction = (cmd, server) => {
  if (cmd === 'toggle') toggleServer(server)
  if (cmd === 'delete') deleteServer(server)
}

// ── 对话框 ────────────────────────────────────────────────────────────────
const openAddDialog = () => {
  editingServer.value = null
  Object.assign(form, defaultForm())
  envPairs.value = []
  showDialog.value = true
}

const openEditDialog = (server) => {
  editingServer.value = server
  form.name = server.name || ''
  form.description = server.description || ''
  form.transport = server.transport || 'stdio'
  form.command = server.command || ''
  form.argsText = (server.args || []).join(' ')
  form.url = server.url || ''
  form.enabled = server.enabled !== false
  const env = server.env || {}
  envPairs.value = Object.entries(env).map(([key, value]) => ({ key, value }))
  showDialog.value = true
}

const buildEnvObj = () => {
  const env = {}
  for (const pair of envPairs.value) {
    const k = (pair.key || '').trim()
    if (k) env[k] = pair.value || ''
  }
  return env
}

const submitForm = async () => {
  if (!formRef.value) return
  const valid = await formRef.value.validate().catch(() => false)
  if (!valid) return
  saving.value = true
  try {
    const payload = {
      name: form.name.trim(),
      description: form.description.trim(),
      transport: form.transport,
      command: form.transport === 'stdio' ? form.command.trim() : null,
      args: form.transport === 'stdio' ? form.argsText.trim().split(/\s+/).filter(Boolean) : [],
      url: form.transport === 'sse' ? form.url.trim() : null,
      env: buildEnvObj(),
      enabled: form.enabled,
    }
    if (editingServer.value) {
      await axios.patch(`/api/mcp/${editingServer.value.id}`, payload)
      ElMessage.success('MCP 服务器已更新')
    } else {
      await axios.post('/api/mcp', payload)
      ElMessage.success('MCP 服务器已添加')
    }
    showDialog.value = false
    await loadServers()
  } catch (err) {
    ElMessage.error(err?.response?.data?.detail || '操作失败')
  } finally {
    saving.value = false
  }
}

const toggleServer = async (server) => {
  server._toggling = true
  try {
    const res = await axios.post(`/api/mcp/${server.id}/toggle`)
    const newEnabled = res.data.data?.enabled ?? !server.enabled
    server.enabled = newEnabled
    ElMessage.success(newEnabled ? '已启用' : '已禁用')
  } catch {
    ElMessage.error('操作失败')
  } finally {
    server._toggling = false
  }
}

const deleteServer = async (server) => {
  try {
    await ElMessageBox.confirm(
      `确定删除「${server.name}」吗？此操作不可恢复。`,
      '删除确认',
      { confirmButtonText: '删除', cancelButtonText: '取消', type: 'warning', confirmButtonClass: 'el-button--danger' }
    )
    server._deleting = true
    await axios.delete(`/api/mcp/${server.id}`)
    ElMessage.success('已删除')
    await loadServers()
  } catch (err) {
    if (err !== 'cancel' && err !== 'close') ElMessage.error('删除失败')
  } finally {
    if (server) server._deleting = false
  }
}

const addEnvPair = () => envPairs.value.push({ key: '', value: '' })
const removeEnvPair = (idx) => envPairs.value.splice(idx, 1)

onMounted(() => {
  loadServers()
  loadMsApiKey()
})
</script>

<style scoped>
/* ── 整体布局 ─────────────────────────────────────────────────────────── */
.mcp-view {
  display: flex;
  flex-direction: column;
  height: 100%;
  overflow: hidden;
  background: #f5f7fa;
}

/* ── 顶部 header ─────────────────────────────────────────────────────── */
.mcp-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 18px 24px 16px;
  background: #fff;
  border-bottom: 1px solid #ebeef5;
  flex-shrink: 0;
  gap: 16px;
}

.header-left {
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.page-title {
  margin: 0;
  font-size: 18px;
  font-weight: 600;
  color: #1a1a2e;
  line-height: 1.3;
}

.page-subtitle {
  font-size: 12px;
  color: #909399;
}

.header-right {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-shrink: 0;
}

.search-input {
  width: 200px;
}

/* ── 主体 ────────────────────────────────────────────────────────────── */
.mcp-body {
  flex: 1;
  overflow-y: auto;
  padding: 20px 24px;
  display: flex;
  flex-direction: column;
  gap: 16px;
  min-height: 0;
}

/* ── ModelScope 卡片 ──────────────────────────────────────────────────── */
.ms-card {
  background: #fff;
  border: 1px solid #e0eaff;
  border-left: 3px solid #4080ff;
  border-radius: 10px;
  padding: 14px 18px;
  flex-shrink: 0;
}

.ms-card--no-key {
  border-left-color: #e6a23c;
  border-color: #faecd8;
  background: #fffdf7;
}

.ms-card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
}

.ms-brand {
  display: flex;
  align-items: center;
  gap: 8px;
}

.ms-brand-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #4080ff;
  flex-shrink: 0;
}

.ms-card--no-key .ms-brand-dot {
  background: #e6a23c;
}

.ms-brand-name {
  font-size: 14px;
  font-weight: 600;
  color: #1a1a2e;
}

.ms-badge {
  background: #ecf5ff;
  color: #409eff;
  border: none;
  font-size: 11px;
}

.ms-help {
  color: #c0c4cc;
  cursor: help;
  font-size: 15px;
  transition: color 0.2s;
}
.ms-help:hover { color: #909399; }

/* 未配置 key */
.ms-no-key-body {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  color: #b8860b;
}
.ms-warn-icon {
  font-size: 16px;
  color: #e6a23c;
  flex-shrink: 0;
}

/* 已配置 key */
.ms-add-body {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.ms-input-row {
  display: flex;
  gap: 8px;
  align-items: center;
  flex-wrap: wrap;
}

.ms-svc-input {
  flex: 1;
  min-width: 200px;
  max-width: 360px;
}

.ms-name-input {
  width: 160px;
}

.ms-add-btn {
  flex-shrink: 0;
}

.ms-hint {
  font-size: 12px;
  color: #909399;
  display: flex;
  align-items: center;
  gap: 4px;
  flex-wrap: wrap;
}

/* ── 服务器列表 ───────────────────────────────────────────────────────── */
.server-list {
  background: #fff;
  border: 1px solid #ebeef5;
  border-radius: 10px;
  overflow: hidden;
  flex: 1;
  min-height: 0;
}

/* 表头 */
.list-header {
  display: flex;
  align-items: center;
  padding: 10px 16px;
  background: #fafbfc;
  border-bottom: 1px solid #ebeef5;
  font-size: 12px;
  font-weight: 600;
  color: #909399;
  text-transform: uppercase;
  letter-spacing: 0.03em;
}

/* 行 */
.server-row {
  display: flex;
  align-items: center;
  padding: 14px 16px;
  border-bottom: 1px solid #f2f4f7;
  transition: background 0.15s;
  gap: 0;
}

.server-row:last-child {
  border-bottom: none;
}

.server-row:hover {
  background: #fafbff;
}

.server-row--disabled {
  opacity: 0.55;
}

/* 列宽 */
.col-name    { width: 160px; flex-shrink: 0; padding-right: 12px; }
.col-desc    { flex: 1; min-width: 0; padding-right: 12px; }
.col-type    { width: 70px; flex-shrink: 0; padding-right: 12px; }
.col-cmd     { width: 260px; flex-shrink: 0; padding-right: 12px; min-width: 0; overflow: hidden; }
.col-status  { width: 70px; flex-shrink: 0; padding-right: 12px; }
.col-actions { width: 80px; flex-shrink: 0; display: flex; gap: 6px; justify-content: flex-end; }

.server-name {
  font-size: 13px;
  font-weight: 500;
  color: #303133;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.server-badge-ms {
  display: inline-block;
  font-size: 10px;
  color: #409eff;
  background: #ecf5ff;
  border-radius: 3px;
  padding: 1px 5px;
  margin-top: 3px;
  line-height: 1.5;
}

.desc-text {
  font-size: 12px;
  color: #606266;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  display: block;
}

/* 类型标签 */
.type-badge {
  display: inline-block;
  font-size: 11px;
  font-weight: 600;
  padding: 2px 7px;
  border-radius: 4px;
  letter-spacing: 0.02em;
}
.type-sse   { background: #fff7e6; color: #d48806; }
.type-stdio { background: #e8f4ff; color: #1677ff; }

/* 命令文本 */
.cmd-text {
  font-family: 'Consolas', 'Monaco', monospace;
  font-size: 11px;
  background: #f6f8fa;
  color: #476582;
  padding: 2px 7px;
  border-radius: 4px;
  display: block;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.text-muted { color: #c0c4cc; }

/* 操作按钮 */
.action-edit,
.action-more {
  padding: 5px 8px;
  border-color: #e4e7ed;
}
.action-edit:hover { border-color: #409eff; color: #409eff; }
.action-more:hover { border-color: #c0c4cc; background: #f5f7fa; }

/* ── 空状态 ──────────────────────────────────────────────────────────── */
.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 60px 20px;
  gap: 10px;
}

.empty-icon {
  width: 52px;
  height: 52px;
  border-radius: 50%;
  background: #f0f4ff;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 24px;
  color: #b0bfd8;
}

.empty-title {
  font-size: 15px;
  font-weight: 600;
  color: #606266;
}

.empty-desc {
  font-size: 13px;
  color: #909399;
  text-align: center;
  max-width: 380px;
  line-height: 1.6;
}

/* ── 表单内 ──────────────────────────────────────────────────────────── */
.form-hint {
  font-size: 12px;
  color: #909399;
  margin-top: 4px;
}

.env-editor { width: 100%; }

.env-row {
  display: flex;
  align-items: center;
  gap: 4px;
  margin-bottom: 6px;
}

.env-eq {
  color: #606266;
  font-size: 14px;
  padding: 0 4px;
}

/* ── 通用链接 ─────────────────────────────────────────────────────────── */
.link {
  color: #409eff;
  text-decoration: none;
}
.link:hover { text-decoration: underline; }
</style>
