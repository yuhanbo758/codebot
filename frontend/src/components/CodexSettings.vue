<template>
  <div class="codex-settings">
    <el-form :model="form" label-width="150px" v-loading="loading">
      <el-form-item label="启用 Codex">
        <el-switch v-model="form.enabled" />
      </el-form-item>
      <el-form-item label="随 Codebot 预热">
        <el-switch v-model="form.auto_start" />
      </el-form-item>
      <el-form-item label="Runtime 来源">
        <el-radio-group v-model="form.runtime_source">
          <el-radio-button label="bundled">SDK 内置（推荐）</el-radio-button>
          <el-radio-button label="custom">本机自定义</el-radio-button>
        </el-radio-group>
      </el-form-item>
      <el-form-item v-if="form.runtime_source === 'custom'" label="Codex 可执行文件">
        <el-input v-model="form.codex_bin" placeholder="codex.exe 的绝对路径" clearable />
      </el-form-item>
      <el-form-item label="审批策略">
        <el-select v-model="form.approval_policy" style="width: 280px">
          <el-option label="交互审批" value="interactive" />
          <el-option label="Codex 自动审查" value="auto_review" />
          <el-option label="全部拒绝提权" value="deny_all" />
        </el-select>
        <div class="field-tip">定时任务始终使用 deny_all，不会等待人工审批。</div>
      </el-form-item>
      <el-form-item label="共享能力">
        <el-checkbox v-model="form.share_memory">共享记忆</el-checkbox>
        <el-checkbox v-model="form.share_scheduler">共享定时任务</el-checkbox>
      </el-form-item>
      <el-form-item label="额外 Skill 目录">
        <div class="dir-list">
          <div v-for="(_dir, index) in form.skill_dirs" :key="index" class="dir-row">
            <el-input v-model="form.skill_dirs[index]" placeholder="Codex skill 根目录绝对路径" clearable />
            <el-button :icon="Delete" circle type="danger" @click="form.skill_dirs.splice(index, 1)" />
          </div>
          <el-button plain type="primary" @click="form.skill_dirs.push('')">
            <el-icon><Plus /></el-icon>添加目录
          </el-button>
        </div>
      </el-form-item>
      <el-form-item>
        <el-button type="primary" :loading="saving" @click="save">保存 Codex 设置</el-button>
        <el-button :loading="restarting" @click="restart">重启 App Server</el-button>
        <el-button :loading="loading" @click="load">刷新状态</el-button>
      </el-form-item>
    </el-form>

    <el-divider content-position="left">账号</el-divider>
    <div class="action-row">
      <el-button type="primary" :loading="loginLoading" @click="login('chatgpt')">ChatGPT 浏览器登录</el-button>
      <el-button :loading="loginLoading" @click="login('device_code')">设备码登录</el-button>
      <el-button :loading="loginLoading" @click="apiKeyDialog = true">使用 API Key</el-button>
      <el-button type="danger" plain :loading="logoutLoading" @click="logout">退出账号</el-button>
    </div>
    <el-alert
      v-if="loginHint"
      class="status-alert"
      :title="loginHint.title"
      :description="loginHint.description"
      type="info"
      show-icon
      :closable="false"
    />

    <el-divider content-position="left">运行状态</el-divider>
    <div class="status-grid">
      <div><span>App Server</span><el-tag :type="status?.running ? 'success' : 'danger'">{{ status?.running ? '运行中' : '未运行' }}</el-tag></div>
      <div><span>SDK 版本</span><code>{{ status?.metadata?.sdkVersion || '-' }}</code></div>
      <div><span>Runtime</span><code>{{ status?.runtimeSource || form.runtime_source }}</code></div>
      <div><span>可用模型</span><code>{{ models.length }}</code></div>
      <div><span>OpenCode 可兼容</span><code>{{ status?.openCodeCompatibleModels ?? status?.openCodeResponsesModels ?? 0 }}</code></div>
      <div><span>本机协议桥</span><code>{{ status?.openCodeBridgedModels ?? 0 }}</code></div>
      <div><span>OpenCode 不兼容</span><code>{{ status?.openCodeIncompatibleModels ?? 0 }}</code></div>
      <div><span>当前账号</span><code>{{ accountLabel }}</code></div>
      <div><span>订阅计划</span><code>{{ planLabel }}</code></div>
      <div><span>累计用量</span><code>{{ usageLabel }}</code></div>
      <div><span>速率限制</span><code>{{ rateLimitLabel }}</code></div>
      <div><span>API 额度</span><code>{{ creditsLabel }}</code></div>
      <div><span>待审批</span><code>{{ status?.pendingRequests ?? 0 }}</code></div>
      <div><span>活跃对话</span><code>{{ status?.activeConversations?.length ?? 0 }}</code></div>
    </div>
    <el-alert v-if="status?.lastError" class="status-alert" :title="status.lastError" type="error" show-icon :closable="false" />
    <el-collapse v-if="account || models.length" class="raw-detail">
      <el-collapse-item title="账号、用量与速率限制详情" name="account">
        <pre>{{ JSON.stringify(account, null, 2) }}</pre>
      </el-collapse-item>
      <el-collapse-item title="Codex 可用模型（含 OpenCode 直连/兼容桥）" name="models">
        <pre>{{ JSON.stringify(models, null, 2) }}</pre>
      </el-collapse-item>
      <el-collapse-item v-if="status?.openCodeProtocolCoverage?.length" title="OpenCode 协议中间层覆盖" name="protocols">
        <pre>{{ JSON.stringify(status.openCodeProtocolCoverage, null, 2) }}</pre>
      </el-collapse-item>
    </el-collapse>

    <el-dialog v-model="apiKeyDialog" title="本次使用 API Key 登录" width="520px" @closed="apiKey = ''">
      <el-alert title="API Key 只发送一次给 Codex App Server，不写入 Codebot 配置，也不会回传前端。" type="warning" show-icon :closable="false" />
      <el-input v-model="apiKey" class="api-key-input" type="password" show-password autocomplete="off" placeholder="sk-..." />
      <template #footer>
        <el-button @click="apiKeyDialog = false">取消</el-button>
        <el-button type="primary" :disabled="!apiKey.trim()" :loading="loginLoading" @click="login('api_key')">登录</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Delete, Plus } from '@element-plus/icons-vue'
import axios from 'axios'

const loading = ref(false)
const saving = ref(false)
const restarting = ref(false)
const loginLoading = ref(false)
const logoutLoading = ref(false)
const status = ref(null)
const account = ref(null)
const models = ref([])
const loginHint = ref(null)
const apiKeyDialog = ref(false)
const apiKey = ref('')
const form = ref({
  enabled: true,
  auto_start: true,
  runtime_source: 'bundled',
  codex_bin: '',
  approval_policy: 'interactive',
  share_memory: true,
  share_scheduler: true,
  skill_dirs: [],
})

const uniqueDirs = (dirs = []) => [...new Set(dirs.map((item) => String(item || '').trim()).filter(Boolean))]
const accountLabel = computed(() => account.value?.account?.email || account.value?.email || account.value?.account?.type || account.value?.type || '未登录/未知')
const planLabel = computed(() => account.value?.account?.planType || account.value?.planType || account.value?.plan || '-')
const usageLabel = computed(() => {
  const tokens = account.value?.usage?.summary?.lifetimeTokens
  return Number.isFinite(Number(tokens)) ? `${Number(tokens).toLocaleString()} tokens` : '-'
})
const rateLimitLabel = computed(() => {
  const primary = account.value?.rateLimits?.rateLimits?.primary
  if (!primary || !Number.isFinite(Number(primary.usedPercent))) return '-'
  const reset = Number(primary.resetsAt)
  const suffix = Number.isFinite(reset) && reset > 0 ? `，${new Date(reset * 1000).toLocaleString()} 重置` : ''
  return `已用 ${primary.usedPercent}%${suffix}`
})
const creditsLabel = computed(() => {
  const credits = account.value?.rateLimits?.rateLimits?.credits
  if (!credits) return '-'
  if (credits.unlimited) return '无限制'
  return credits.hasCredits ? String(credits.balance ?? '-') : '无额外额度'
})

const firstValue = (data, keys) => {
  if (!data || typeof data !== 'object') return ''
  for (const key of keys) if (typeof data[key] === 'string' && data[key]) return data[key]
  for (const value of Object.values(data)) {
    const found = firstValue(value, keys)
    if (found) return found
  }
  return ''
}

const openExternal = async (url) => {
  if (!url) return
  if (window.electronAPI?.openExternal) await window.electronAPI.openExternal(url)
  else window.open(url, '_blank', 'noopener,noreferrer')
}

const load = async () => {
  loading.value = true
  try {
    const cfgRes = await axios.get('/api/config/codex')
    form.value = { ...form.value, ...(cfgRes.data?.data || {}), skill_dirs: cfgRes.data?.data?.skill_dirs || [] }
    const statusRes = await axios.get('/api/codex/status')
    status.value = statusRes.data?.data || null
    if (status.value?.running) {
      const [accountRes, modelsRes] = await Promise.allSettled([
        axios.get('/api/codex/account'),
        axios.get('/api/codex/models'),
      ])
      account.value = accountRes.status === 'fulfilled' ? accountRes.value.data?.data || null : null
      models.value = modelsRes.status === 'fulfilled' ? modelsRes.value.data?.data || [] : []
    }
  } catch (error) {
    ElMessage.error(error?.response?.data?.detail || '加载 Codex 设置失败')
  } finally {
    loading.value = false
  }
}

const save = async () => {
  saving.value = true
  try {
    const payload = { ...form.value, skill_dirs: uniqueDirs(form.value.skill_dirs) }
    const res = await axios.patch('/api/config/codex', payload)
    form.value = { ...form.value, ...(res.data?.data || {}) }
    ElMessage.success('Codex 设置已保存')
  } catch (error) {
    ElMessage.error(error?.response?.data?.detail || '保存 Codex 设置失败')
  } finally {
    saving.value = false
  }
}

const restart = async () => {
  restarting.value = true
  try {
    await axios.post('/api/codex/restart')
    ElMessage.success('Codex App Server 已重启')
    await load()
  } catch (error) {
    ElMessage.error(error?.response?.data?.detail || '重启 Codex 失败')
  } finally {
    restarting.value = false
  }
}

const login = async (method) => {
  loginLoading.value = true
  try {
    const payload = { method }
    if (method === 'api_key') payload.api_key = apiKey.value
    const res = await axios.post('/api/codex/account/login', payload)
    const data = res.data?.data || {}
    const authUrl = firstValue(data, ['authUrl', 'auth_url', 'url', 'verificationUri', 'verification_uri'])
    const deviceCode = firstValue(data, ['userCode', 'user_code', 'deviceCode', 'device_code'])
    loginHint.value = {
      title: method === 'device_code' ? '设备码登录已启动' : 'Codex 登录已启动',
      description: [authUrl, deviceCode ? `设备码：${deviceCode}` : ''].filter(Boolean).join('\n') || '请按 Codex 登录流程完成认证，然后刷新状态。',
    }
    if (authUrl) await openExternal(authUrl)
    apiKey.value = ''
    apiKeyDialog.value = false
    ElMessage.success(res.data?.message || '登录流程已启动')
  } catch (error) {
    ElMessage.error(error?.response?.data?.detail || '启动 Codex 登录失败')
  } finally {
    apiKey.value = ''
    loginLoading.value = false
  }
}

const logout = async () => {
  logoutLoading.value = true
  try {
    await axios.post('/api/codex/account/logout')
    account.value = null
    ElMessage.success('已退出 Codex 账号')
  } catch (error) {
    ElMessage.error(error?.response?.data?.detail || '退出 Codex 账号失败')
  } finally {
    logoutLoading.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.codex-settings { padding: 20px; }
.dir-list { width: 100%; }
.dir-row { display: flex; gap: 8px; margin-bottom: 8px; }
.field-tip { width: 100%; margin-top: 4px; color: var(--el-text-color-secondary); font-size: 12px; }
.action-row { display: flex; gap: 10px; flex-wrap: wrap; }
.status-alert { margin-top: 16px; white-space: pre-wrap; }
.status-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 10px 18px; }
.status-grid > div { display: grid; grid-template-columns: 95px minmax(0, 1fr); align-items: center; min-height: 30px; }
.status-grid code { word-break: break-all; }
.raw-detail { margin-top: 16px; }
.raw-detail pre { max-height: 360px; overflow: auto; white-space: pre-wrap; word-break: break-all; }
.api-key-input { margin-top: 18px; }
</style>
