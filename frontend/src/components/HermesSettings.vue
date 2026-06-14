<template>
  <div class="hermes-settings">
    <el-form :model="form" label-width="150px" v-loading="loading">
      <el-form-item label="启用 Hermes">
        <el-switch v-model="form.enabled" />
      </el-form-item>

      <el-form-item label="随 Codebot 预热">
        <el-switch v-model="form.auto_start" />
      </el-form-item>

      <el-form-item label="安装目录">
        <el-input v-model="form.install_dir" placeholder="留空则安装到 Codebot 数据目录/hermes-agent" clearable />
      </el-form-item>

      <el-form-item label="Hermes 命令">
        <el-input v-model="form.cli_path" placeholder="hermes" clearable />
      </el-form-item>

      <el-form-item label="仓库地址">
        <el-input v-model="form.repo_url" placeholder="https://github.com/NousResearch/hermes-agent" clearable />
      </el-form-item>

      <el-form-item label="共享能力">
        <el-checkbox v-model="form.share_models">共享 Codebot 模型</el-checkbox>
        <el-checkbox v-model="form.share_memory">共享记忆</el-checkbox>
        <el-checkbox v-model="form.share_scheduler">共享定时任务</el-checkbox>
      </el-form-item>

      <el-form-item label="Hermes Skill 目录">
        <div class="dir-list">
          <div v-for="(dir, index) in form.skill_dirs" :key="index" class="dir-row">
            <el-input v-model="form.skill_dirs[index]" placeholder="Hermes skill 目录绝对路径" clearable />
            <el-button :icon="Delete" circle type="danger" @click="form.skill_dirs.splice(index, 1)" />
          </div>
          <el-button plain type="primary" @click="form.skill_dirs.push('')">
            <el-icon><Plus /></el-icon>
            添加目录
          </el-button>
          <div v-if="sharedSkillCandidates.length" class="dir-tip">
            以下目录由 Codebot 自动并入 Hermes 的有效 skill 根目录。默认会自动共享 Hermes 运行时 `HERMES_HOME/skills`、Hermes 仓库内 `skills/` 和 `optional-skills/`、Codebot skills 以及 OpenCode skills；如果你不需要其中某个目录，可以把它加入排除列表。
          </div>
          <div v-for="dir in effectiveSharedSkillDirs" :key="`shared-${dir}`" class="dir-row dir-row--readonly">
            <el-input :model-value="dir" readonly />
            <el-tag type="info">自动共享</el-tag>
            <el-button type="warning" plain @click="excludeAutoSharedDir(dir)">排除</el-button>
          </div>
          <div v-if="excludedSharedSkillDirs.length" class="dir-tip">
            以下自动共享目录已被排除，不会再并入 Hermes 的有效 skill 根目录。
          </div>
          <div v-for="dir in excludedSharedSkillDirs" :key="`excluded-${dir}`" class="dir-row dir-row--readonly">
            <el-input :model-value="dir" readonly />
            <el-tag type="warning">已排除</el-tag>
            <el-button type="primary" plain @click="restoreAutoSharedDir(dir)">恢复</el-button>
          </div>
        </div>
      </el-form-item>

      <el-form-item>
        <el-button type="primary" :loading="saving" @click="save">保存 Hermes 设置</el-button>
        <el-button :loading="syncing" @click="syncBridge">同步共享配置</el-button>
      </el-form-item>
    </el-form>

    <el-divider />

    <div class="action-row">
      <el-button type="success" :loading="actionLoading === 'install'" @click="runAction('install')">
        一键安装 Hermes Agent
      </el-button>
      <el-button type="warning" :loading="actionLoading === 'repair'" @click="runAction('repair')">
        一键修复
      </el-button>
      <el-button type="primary" :loading="actionLoading === 'update'" @click="runAction('update')">
        一键更新
      </el-button>
    </div>

    <el-alert
      v-if="statusText"
      class="status-alert"
      :title="statusText"
      :description="bridgeText"
      type="info"
      show-icon
      :closable="false"
    />

    <div v-if="status" class="status-panel">
      <div class="status-title">Codebot 服务访问地址</div>
      <div class="status-row">
        <span>本机访问</span>
        <el-link :href="codebotApp.local_url" target="_blank" type="primary">{{ codebotApp.local_url }}</el-link>
      </div>
      <div class="status-row">
        <span>局域网访问</span>
        <el-link :href="codebotApp.lan_url" target="_blank" type="primary">{{ codebotApp.lan_url }}</el-link>
      </div>

      <div class="status-title">Hermes CLI</div>
      <div class="status-row">
        <span>运行模式</span>
        <code>{{ status.mode || 'cli' }}</code>
      </div>
      <div class="status-row">
        <span>Python</span>
        <code>{{ status.runtime_python || '-' }}</code>
      </div>
      <div class="status-row">
        <span>聊天模型</span>
        <code>{{ status.active_chat_model || '-' }}</code>
      </div>
      <div class="status-row">
        <span>后台模型</span>
        <code>{{ status.background_model || '-' }}</code>
      </div>
      <div class="status-row status-row--top">
        <span>生效 Skill 目录</span>
        <div class="skill-dir-preview">
          <code v-for="dir in effectiveSkillDirs" :key="`effective-${dir}`">{{ dir }}</code>
          <code v-if="!effectiveSkillDirs.length">-</code>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Delete, Plus } from '@element-plus/icons-vue'
import axios from 'axios'

const loading = ref(false)
const saving = ref(false)
const syncing = ref(false)
const actionLoading = ref('')
const status = ref(null)

const form = ref({
  enabled: true,
  auto_start: true,
  repo_url: 'https://github.com/NousResearch/hermes-agent',
  install_dir: '',
  cli_path: 'hermes',
  share_models: true,
  share_memory: true,
  share_scheduler: true,
  skill_dirs: [],
  excluded_auto_skill_dirs: []
})

const statusText = computed(() => {
  const cfg = status.value?.config
  if (!cfg) return ''
  const action = cfg.last_status ? `上次 ${cfg.last_action}: ${cfg.last_status}` : 'Hermes CLI 已接入 Codebot'
  return `${action} · 模式 ${status.value?.mode || 'cli'} · 安装目录 ${status.value?.install_dir || ''}`
})

const bridgeText = computed(() => {
  const bridge = status.value?.bridge_config_path ? `共享配置: ${status.value.bridge_config_path}` : ''
  const runtime = status.value?.runtime_python ? `\n运行环境: ${status.value.runtime_python}` : ''
  return `${bridge}${runtime}`
})

const codebotApp = computed(() => status.value?.codebot_app || {})
const normalizeDirKey = (value = '') => value.trim().replace(/\//g, '\\').replace(/[\\\/]+$/, '').toLowerCase()
const uniqueDirs = (dirs = []) => {
  const seen = new Set()
  return dirs
    .map((item) => String(item || '').trim())
    .filter(Boolean)
    .filter((item) => {
      const key = normalizeDirKey(item)
      if (!key || seen.has(key)) return false
      seen.add(key)
      return true
    })
}

const sharedSkillCandidates = computed(() => uniqueDirs(status.value?.shared_skill_candidates || status.value?.shared_skill_dirs || []))
const excludedAutoSkillDirs = computed(() => uniqueDirs(form.value.excluded_auto_skill_dirs || []))
const excludedAutoSkillDirKeys = computed(() => new Set(excludedAutoSkillDirs.value.map((item) => normalizeDirKey(item))))
const effectiveSharedSkillDirs = computed(() => sharedSkillCandidates.value.filter((dir) => !excludedAutoSkillDirKeys.value.has(normalizeDirKey(dir))))
const excludedSharedSkillDirs = computed(() => sharedSkillCandidates.value.filter((dir) => excludedAutoSkillDirKeys.value.has(normalizeDirKey(dir))))
const effectiveSkillDirs = computed(() => uniqueDirs([...(form.value.skill_dirs || []), ...effectiveSharedSkillDirs.value]))

const normalizedPayload = () => ({
  ...form.value,
  skill_dirs: uniqueDirs(form.value.skill_dirs || []),
  excluded_auto_skill_dirs: uniqueDirs(form.value.excluded_auto_skill_dirs || [])
})

const load = async () => {
  loading.value = true
  try {
    const [cfgRes, statusRes] = await Promise.all([
      axios.get('/api/config/hermes'),
      axios.get('/api/hermes/status')
    ])
    form.value = { ...form.value, ...(cfgRes.data?.data || {}) }
    form.value.skill_dirs = form.value.skill_dirs || []
    form.value.excluded_auto_skill_dirs = form.value.excluded_auto_skill_dirs || []
    status.value = statusRes.data?.data || null
  } catch (error) {
    ElMessage.error('加载 Hermes 设置失败')
  } finally {
    loading.value = false
  }
}

const save = async () => {
  saving.value = true
  try {
    const res = await axios.patch('/api/config/hermes', normalizedPayload())
    form.value = { ...form.value, ...(res.data?.data || {}) }
    ElMessage.success('Hermes 设置已保存')
    await syncBridge()
  } catch (error) {
    ElMessage.error(error?.response?.data?.detail || '保存 Hermes 设置失败')
  } finally {
    saving.value = false
  }
}

const syncBridge = async () => {
  syncing.value = true
  try {
    const res = await axios.post('/api/hermes/sync')
    const statusRes = await axios.get('/api/hermes/status')
    status.value = {
      ...(statusRes.data?.data || {}),
      bridge_config_path: res.data?.data?.bridge_config_path || statusRes.data?.data?.bridge_config_path
    }
    ElMessage.success('Hermes 共享配置已同步')
  } catch (error) {
    ElMessage.error(error?.response?.data?.detail || '同步失败')
  } finally {
    syncing.value = false
  }
}

const runAction = async (action) => {
  actionLoading.value = action
  try {
    const res = await axios.post(`/api/hermes/${action}`)
    ElMessage.success(res.data?.message || '操作完成')
    await load()
  } catch (error) {
    ElMessage.error(error?.response?.data?.detail || 'Hermes 操作失败')
  } finally {
    actionLoading.value = ''
  }
}

const excludeAutoSharedDir = (dir) => {
  const current = uniqueDirs(form.value.excluded_auto_skill_dirs || [])
  if (!current.some((item) => normalizeDirKey(item) === normalizeDirKey(dir))) {
    form.value.excluded_auto_skill_dirs = [...current, dir]
  }
}

const restoreAutoSharedDir = (dir) => {
  form.value.excluded_auto_skill_dirs = uniqueDirs(form.value.excluded_auto_skill_dirs || [])
    .filter((item) => normalizeDirKey(item) !== normalizeDirKey(dir))
}

onMounted(load)
</script>

<style scoped>
.hermes-settings {
  padding: 20px;
}

.dir-list {
  width: 100%;
}

.dir-row {
  display: flex;
  gap: 8px;
  width: 100%;
  margin-bottom: 8px;
}

.dir-row--readonly :deep(.el-input__wrapper) {
  background: var(--el-fill-color-light);
}

.dir-tip {
  margin: 8px 0;
  color: var(--el-text-color-secondary);
  font-size: 13px;
  line-height: 1.5;
}

.action-row {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
}

.status-alert {
  margin-top: 16px;
  white-space: pre-wrap;
}

.status-panel {
  margin-top: 16px;
  border: 1px solid var(--el-border-color-light);
  border-radius: 8px;
  padding: 14px 16px;
  background: var(--el-fill-color-lighter);
}

.status-title {
  margin: 8px 0;
  font-weight: 600;
}

.status-title:first-child {
  margin-top: 0;
}

.status-row {
  display: grid;
  grid-template-columns: 90px minmax(0, 1fr);
  gap: 10px;
  align-items: center;
  min-height: 28px;
}

.status-row--top {
  align-items: flex-start;
}

.status-row .el-link {
  justify-content: flex-start;
  word-break: break-all;
}

.status-row code {
  word-break: break-all;
}

.skill-dir-preview {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
</style>
