<template>
  <div class="mcp-view">

    <!-- 顶部区域 -->
    <div class="mcp-header">
      <div class="header-left">
        <h2 class="page-title">MCP 服务器</h2>
        <span class="page-subtitle">管理外部 MCP 服务器，并通过 Codebot bridge 暴露给 OpenCode。</span>
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
        <el-tooltip v-if="ocSyncStatus" :content="ocSyncTooltip" placement="bottom">
          <el-button
            :type="ocSyncStatus.in_sync ? 'success' : 'warning'"
            plain
            size="default"
            @click="syncToOpencode"
            :loading="ocSyncing"
          >
            <el-icon><Refresh /></el-icon>
            {{ ocSyncButtonLabel }}
          </el-button>
        </el-tooltip>
        <el-button type="primary" @click="openAddDialog">
          <el-icon><Plus /></el-icon>
          添加服务器
        </el-button>
      </div>
    </div>

    <!-- 主体内容 -->
    <div class="mcp-body">
      <div v-if="codebotStatus" class="codebot-bridge-card">
        <div class="bridge-title">Codebot Bridge</div>
        <div class="bridge-desc">OpenCode 会通过下面的 SSE 入口调用 Codebot 暴露的记忆、任务、技能与外部 MCP 代理工具。</div>
        <div class="bridge-meta">{{ codebotStatus.sse_url }}</div>
        <div class="bridge-actions">
          <el-tag size="small" :type="codebotStatus.opencode_connected ? 'success' : 'info'">
            {{ codebotStatus.opencode_connected ? 'OpenCode 在线' : 'OpenCode 未连接' }}
          </el-tag>
          <el-tag size="small" :type="codebotStatus.bridge_status?.registered ? 'success' : 'warning'">
            {{ codebotStatus.bridge_status?.registered ? 'Bridge 已注册' : 'Bridge 未注册' }}
          </el-tag>
          <el-tag size="small" type="info">
            {{ `技能 ${codebotStatus.synced_skill_count || 0}/${codebotStatus.builtin_skill_count || 0} 已同步` }}
          </el-tag>
          <el-button size="small" type="primary" plain @click="registerCodebotBridge" :loading="registeringCodebotBridge">
            写入 OpenCode 配置
          </el-button>
        </div>
      </div>

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
            <el-button type="primary" @click="openMsHubDialog" :loading="msHubLoading">
              <el-icon><Download /></el-icon>
              浏览并导入服务
            </el-button>
            <span class="ms-hint">从 ModelScope MCP Hub 一键导入，API Key 将自动注入</span>
          </div>
          <div class="ms-hint">
            <a href="https://www.modelscope.cn/mcp" target="_blank" class="link">
              在浏览器中查看全部服务 <el-icon style="font-size:11px;vertical-align:-1px"><TopRight /></el-icon>
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
              type="success"
              plain
              @click="testServer(server)"
              class="action-test"
              :loading="server._testing"
              title="测试连接"
            >
              <el-icon v-if="!server._testing"><Connection /></el-icon>
              测试
            </el-button>
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

  <!-- 测试结果对话框 -->
  <el-dialog
    v-model="showTestDialog"
    title="MCP 连接测试"
    width="520px"
    :close-on-click-modal="true"
  >
    <div v-if="testResult" class="test-result">
      <div class="test-status" :class="testResult.success === true ? 'test-ok' : testResult.success === null ? 'test-warn' : 'test-fail'">
        <el-icon class="test-status-icon">
          <component :is="testResult.success === true ? 'CircleCheck' : testResult.success === null ? 'InfoFilled' : 'CircleClose'" />
        </el-icon>
        <span class="test-status-text">{{ testResult.message }}</span>
      </div>
      <div v-if="testResult.tools && testResult.tools.length > 0" class="test-tools">
        <div class="test-tools-title">可用工具（{{ testResult.tools.length }} 个）：</div>
        <div class="test-tools-list">
          <el-tag
            v-for="tool in testResult.tools"
            :key="tool"
            size="small"
            class="test-tool-tag"
          >{{ tool }}</el-tag>
        </div>
      </div>
    </div>
    <template #footer>
      <el-button @click="showTestDialog = false">关闭</el-button>
    </template>
  </el-dialog>

  <!-- ModelScope Hub 导入对话框 -->
  <el-dialog
    v-model="showMsHubDialog"
    title="从 ModelScope MCP Hub 导入服务"
    width="740px"
    :close-on-click-modal="true"
  >
    <div class="ms-hub-dialog">
      <!-- 搜索栏 -->
      <div class="ms-hub-toolbar">
        <el-input
          v-model="msHubSearch"
          placeholder="搜索服务名称或描述..."
          clearable
          class="ms-hub-search"
        >
          <template #prefix><el-icon><Search /></el-icon></template>
        </el-input>
        <el-button @click="loadMsHubServices" :loading="msHubLoading" size="small">
          <el-icon><Refresh /></el-icon>
          刷新
        </el-button>
      </div>

      <!-- 加载中 -->
      <div v-if="msHubLoading" class="ms-hub-loading">
        <el-icon class="is-loading" style="font-size:28px;color:#409eff"><Loading /></el-icon>
        <span>正在获取服务列表...</span>
      </div>

      <!-- 错误 -->
      <div v-else-if="msHubError" class="ms-hub-error">
        <el-icon style="font-size:20px;color:#f56c6c"><CircleClose /></el-icon>
        <span>{{ msHubError }}</span>
      </div>

      <!-- 服务列表 -->
      <div v-else class="ms-hub-list">
        <div v-if="filteredMsServices.length === 0" class="ms-hub-empty">
          暂无匹配的服务
        </div>
        <div
          v-for="svc in filteredMsServices"
          :key="svc.id"
          class="ms-hub-row"
        >
          <div class="ms-hub-info">
            <div class="ms-hub-name">{{ svc.chinese_name || svc.name }}</div>
            <div class="ms-hub-id">{{ svc.name }}</div>
            <div class="ms-hub-desc" :title="svc.description">{{ svc.description || '—' }}</div>
          </div>
          <div class="ms-hub-actions">
            <el-tag
              v-if="isAlreadyImported(svc)"
              size="small"
              type="success"
            >已导入</el-tag>
            <el-button
              v-else
              size="small"
              type="primary"
              @click="importMsService(svc)"
              :loading="svc._importing"
            >
              <el-icon><Download /></el-icon>
              导入
            </el-button>
          </div>
        </div>
      </div>
    </div>

    <template #footer>
      <div style="display:flex;align-items:center;justify-content:space-between;width:100%">
        <span style="font-size:12px;color:#909399">
          共 {{ msHubServices.length }} 个可用服务，Token 将自动注入到每个服务的环境变量
        </span>
        <el-button @click="showMsHubDialog = false">关闭</el-button>
      </div>
    </template>
  </el-dialog>

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
  Connection, TopRight, Edit, MoreFilled, CircleCheck, CircleClose, InfoFilled,
  Download, Refresh, Loading
} from '@element-plus/icons-vue'
import axios from 'axios'

// ── 数据 ──────────────────────────────────────────────────────────────────
const servers = ref([])
const loading = ref(false)
const saving = ref(false)
const searchQuery = ref('')

// ModelScope
const msApiKey = ref('')
const msHubLoading = ref(false)
const msHubError = ref('')
const msHubServices = ref([])
const msHubSearch = ref('')
const showMsHubDialog = ref(false)

// OpenCode sync
const ocSyncStatus = ref(null)
const ocSyncing = ref(false)
const codebotStatus = ref(null)
const registeringCodebotBridge = ref(false)

// 对话框
const showDialog = ref(false)
const editingServer = ref(null)
const formRef = ref(null)

// 测试对话框
const showTestDialog = ref(false)
const testResult = ref(null)

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

const ocSyncButtonLabel = computed(() => {
  if (!ocSyncStatus.value) return '同步到 OpenCode'
  if (ocSyncStatus.value.in_sync) return 'OpenCode 已接管'
  const directCount = ocSyncStatus.value.direct_entries_in_opencode?.length || 0
  if (!ocSyncStatus.value.bridge_registered) return '注册 Codebot Bridge'
  if (directCount > 0) return `清理直连项 (${directCount})`
  return '同步到 OpenCode'
})

// ── 加载 ──────────────────────────────────────────────────────────────────
const loadServers = async () => {
  loading.value = true
  try {
    const res = await axios.get('/api/mcp')
    servers.value = (res.data.data.items || []).map(s => ({ ...s, _toggling: false, _deleting: false, _testing: false }))
  } catch {
    ElMessage.error('加载 MCP 服务器列表失败')
  } finally {
    loading.value = false
  }
  // 顺便刷新 opencode 同步状态
  loadOcSyncStatus()
}

const loadMsApiKey = async () => {
  try {
    const res = await axios.get('/api/config/integration')
    msApiKey.value = res.data?.data?.modelscope_api_key || ''
  } catch {
    msApiKey.value = ''
  }
}

// ── ModelScope Hub ────────────────────────────────────────────────────────
const filteredMsServices = computed(() => {
  const q = msHubSearch.value.trim().toLowerCase()
  if (!q) return msHubServices.value
  return msHubServices.value.filter(s =>
    (s.name || '').toLowerCase().includes(q) ||
    (s.chinese_name || '').toLowerCase().includes(q) ||
    (s.description || '').toLowerCase().includes(q)
  )
})

const isAlreadyImported = (svc) =>
  servers.value.some(s => s.url === svc.url || s.service_id === svc.id)

const loadMsHubServices = async () => {
  msHubLoading.value = true
  msHubError.value = ''
  try {
    const res = await axios.get('/api/mcp/modelscope/services')
    msHubServices.value = (res.data?.data?.services || []).map(s => ({ ...s, _importing: false }))
  } catch (err) {
    msHubError.value = err?.response?.data?.detail || '获取服务列表失败，请检查 API Key 是否有效'
  } finally {
    msHubLoading.value = false
  }
}

const openMsHubDialog = async () => {
  showMsHubDialog.value = true
  if (msHubServices.value.length === 0) {
    await loadMsHubServices()
  }
}

const importMsService = async (svc) => {
  svc._importing = true
  try {
    const res = await axios.post('/api/mcp/modelscope/import', {
      name: svc.chinese_name || svc.name,
      service_id: svc.id,
      url: svc.url,
      description: svc.description || `ModelScope MCP: ${svc.name}`,
    })
    if (res.data.success) {
      ElMessage.success(`「${svc.chinese_name || svc.name}」已成功导入`)
      await loadServers()
    } else {
      ElMessage.warning(res.data.message || '导入失败')
    }
  } catch (err) {
    ElMessage.error(err?.response?.data?.detail || '导入失败')
  } finally {
    svc._importing = false
  }
}

// ── OpenCode CLI 同步 ─────────────────────────────────────────────────────
const ocSyncTooltip = computed(() => {
  if (!ocSyncStatus.value) return ''
  const { in_sync, bridge_registered, direct_entries_in_opencode, opencode_config_path } = ocSyncStatus.value
  if (in_sync) return `OpenCode 当前仅保留 codebot bridge\n路径: ${opencode_config_path}`
  if (!bridge_registered) return '当前尚未将 codebot bridge 写入 OpenCode 配置'
  const names = (direct_entries_in_opencode || []).map(s => s.name).join(', ')
  return names
    ? `OpenCode 配置中仍存在以下直连 MCP 条目，建议切换为由 Codebot 统一代理：\n${names}`
    : '重新写入 Codebot bridge 到 OpenCode 配置'
})

const loadOcSyncStatus = async () => {
  try {
    const res = await axios.get('/api/mcp/opencode/sync-status')
    const data = res.data?.data
    if (data?.has_opencode) {
      ocSyncStatus.value = data
    } else {
      ocSyncStatus.value = null  // opencode 未安装，不显示按钮
    }
  } catch {
    ocSyncStatus.value = null
  }
}

const loadCodebotStatus = async () => {
  try {
    const res = await axios.get('/api/mcp/codebot/status')
    codebotStatus.value = res.data?.data ?? null
  } catch {
    codebotStatus.value = null
  }
}

const syncToOpencode = async () => {
  ocSyncing.value = true
  try {
    const res = await axios.post('/api/mcp/opencode/sync')
    ElMessage.success(res.data?.message || '同步成功')
    await loadOcSyncStatus()
  } catch (err) {
    ElMessage.error(err?.response?.data?.detail || '同步失败')
  } finally {
    ocSyncing.value = false
  }
}

const registerCodebotBridge = async () => {
  registeringCodebotBridge.value = true
  try {
    const res = await axios.post('/api/mcp/codebot/register')
    ElMessage.success(res.data?.message || '写入成功')
    await Promise.all([loadCodebotStatus(), loadOcSyncStatus(), loadServers()])
  } catch (err) {
    ElMessage.error(err?.response?.data?.detail || '写入失败')
  } finally {
    registeringCodebotBridge.value = false
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

// ── 测试 MCP 连接 ──────────────────────────────────────────────────────────
const testServer = async (server) => {
  server._testing = true
  testResult.value = null
  try {
    const res = await axios.post(`/api/mcp/${server.id}/test`)
    testResult.value = res.data
    showTestDialog.value = true
  } catch (err) {
    testResult.value = {
      success: false,
      message: err?.response?.data?.detail || err?.response?.data?.message || '测试请求失败',
      tools: []
    }
    showTestDialog.value = true
  } finally {
    server._testing = false
  }
}

onMounted(() => {
  loadServers()
  loadMsApiKey()
  loadOcSyncStatus()
  loadCodebotStatus()
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

.codebot-bridge-card {
  background: linear-gradient(135deg, #f4f8ff 0%, #f8fbff 100%);
  border: 1px solid #d9e6ff;
  border-radius: 14px;
  padding: 16px 18px;
}

.bridge-title {
  font-size: 15px;
  font-weight: 600;
  color: #1f2d3d;
}

.bridge-desc {
  margin-top: 6px;
  font-size: 13px;
  color: #5f6b7a;
}

.bridge-meta {
  margin-top: 10px;
  font-size: 12px;
  color: #409eff;
  word-break: break-all;
}

.bridge-actions {
  margin-top: 12px;
  display: flex;
  align-items: center;
  gap: 10px;
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
.col-cmd     { width: 220px; flex-shrink: 0; padding-right: 12px; min-width: 0; overflow: hidden; }
.col-status  { width: 70px; flex-shrink: 0; padding-right: 12px; }
.col-actions { width: 130px; flex-shrink: 0; display: flex; gap: 6px; justify-content: flex-end; align-items: center; }

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
.action-more,
.action-test {
  padding: 5px 8px;
  border-color: #e4e7ed;
}
.action-edit:hover { border-color: #409eff; color: #409eff; }
.action-test { font-size: 12px; }
.action-test:hover { border-color: #67c23a; color: #67c23a; }
.action-more:hover { border-color: #c0c4cc; background: #f5f7fa; }

/* ── 测试结果 ────────────────────────────────────────────────────────── */
.test-result {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.test-status {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 14px 16px;
  border-radius: 8px;
  font-size: 14px;
  line-height: 1.5;
}

.test-ok {
  background: #f0f9eb;
  color: #67c23a;
  border: 1px solid #b3e19d;
}

.test-fail {
  background: #fef0f0;
  color: #f56c6c;
  border: 1px solid #fbc4c4;
}

.test-warn {
  background: #fdf6ec;
  color: #e6a23c;
  border: 1px solid #f5dab1;
}

.test-status-icon {
  font-size: 18px;
  flex-shrink: 0;
  margin-top: 1px;
}

.test-status-text {
  flex: 1;
  word-break: break-all;
}

.test-tools-title {
  font-size: 13px;
  font-weight: 600;
  color: #303133;
  margin-bottom: 8px;
}

.test-tools-list {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.test-tool-tag {
  font-family: 'Consolas', 'Monaco', monospace;
  font-size: 12px;
}

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

/* ── ModelScope Hub 对话框 ───────────────────────────────────────────── */
.ms-hub-dialog {
  display: flex;
  flex-direction: column;
  gap: 12px;
  max-height: 520px;
}

.ms-hub-toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
}

.ms-hub-search {
  flex: 1;
}

.ms-hub-loading,
.ms-hub-error,
.ms-hub-empty {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  padding: 40px 20px;
  color: #909399;
  font-size: 14px;
}

.ms-hub-error {
  color: #f56c6c;
}

.ms-hub-list {
  overflow-y: auto;
  max-height: 420px;
  display: flex;
  flex-direction: column;
  gap: 0;
  border: 1px solid #ebeef5;
  border-radius: 8px;
}

.ms-hub-row {
  display: flex;
  align-items: center;
  padding: 12px 16px;
  gap: 12px;
  border-bottom: 1px solid #f2f4f7;
  transition: background 0.15s;
}

.ms-hub-row:last-child {
  border-bottom: none;
}

.ms-hub-row:hover {
  background: #fafbff;
}

.ms-hub-info {
  flex: 1;
  min-width: 0;
}

.ms-hub-name {
  font-size: 13px;
  font-weight: 600;
  color: #303133;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.ms-hub-id {
  font-size: 11px;
  color: #909399;
  font-family: 'Consolas', 'Monaco', monospace;
  margin-top: 1px;
}

.ms-hub-desc {
  font-size: 12px;
  color: #606266;
  margin-top: 3px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.ms-hub-actions {
  flex-shrink: 0;
  width: 80px;
  display: flex;
  justify-content: flex-end;
}
</style>
