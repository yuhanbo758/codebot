<template>
  <div class="sandbox-settings">
    <!-- 状态卡片 -->
    <el-card class="status-card" shadow="never">
      <template #header>
        <span>沙箱状态</span>
        <el-button style="float:right" size="small" @click="refreshStatus">刷新</el-button>
      </template>

      <el-descriptions :column="2" border size="small">
        <el-descriptions-item label="模式">
          <el-tag type="success" size="small">工作目录隔离</el-tag>
          <span style="margin-left:8px;font-size:12px;color:#888">参考 LobsterAI 本地执行架构，无需 QEMU</span>
        </el-descriptions-item>
        <el-descriptions-item label="平台">{{ status.platform || '—' }}</el-descriptions-item>
        <el-descriptions-item label="沙箱状态">
          <el-tag :type="sandboxStateTagType" size="small">{{ sandboxStateLabel }}</el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="工作目录就绪">
          <el-tag type="success" size="small">已就绪</el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="工作目录" :span="2">
          <span style="font-size:12px;color:#555;word-break:break-all">{{ status.workspace_dir || '—' }}</span>
        </el-descriptions-item>
        <el-descriptions-item label="执行模式">
          <el-tag size="small">{{ executionModeLabel }}</el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="功能启用">
          <el-tag :type="status.enabled ? 'success' : 'info'" size="small">
            {{ status.enabled ? '已启用' : '未启用' }}
          </el-tag>
        </el-descriptions-item>
      </el-descriptions>

      <el-alert
        v-if="status.mode_description"
        style="margin-top:12px"
        type="info"
        :title="status.mode_description"
        :closable="false"
        show-icon
      />

      <!-- 操作按钮 -->
      <div style="margin-top:16px;display:flex;gap:8px;flex-wrap:wrap">
        <el-button
          size="small"
          @click="prepare"
          :disabled="saving"
        >
          初始化工作目录
        </el-button>
        <el-button
          size="small"
          :loading="testing"
          :disabled="saving || testing || !form.enabled"
          @click="runTest"
        >
          {{ testing ? '测试中...' : '冒烟测试' }}
        </el-button>
      </div>

      <!-- 测试结果 -->
      <div v-if="testing" style="margin-top:12px">
        <el-alert type="info" title="正在测试" description="正在执行沙箱冒烟测试，请稍候…" :closable="false" show-icon />
      </div>
      <div v-else-if="testResult" style="margin-top:12px">
        <el-alert
          :type="testResult.success ? 'success' : 'error'"
          :title="testResult.success ? '测试通过' : '测试失败'"
          :description="testResult.content || testResult.error || testResult.message || '沙箱冒烟测试失败，请查看日志'"
          :closable="false"
          show-icon
        />
      </div>
    </el-card>

    <!-- 配置卡片 -->
    <el-card shadow="never" style="margin-top:16px">
      <template #header><span>沙箱配置</span></template>
      <el-form :model="form" label-width="150px" size="default">

        <el-form-item label="启用沙箱">
          <el-switch v-model="form.enabled" />
          <span style="margin-left:10px;font-size:12px;color:#888">
            启用后聊天任务可在隔离工作目录中执行
          </span>
        </el-form-item>

        <el-form-item label="执行模式">
          <el-select v-model="form.execution_mode" style="width:200px">
            <el-option label="自动（auto）" value="auto" />
            <el-option label="始终本地（local）" value="local" />
            <el-option label="始终沙箱（sandbox）" value="sandbox" />
          </el-select>
          <span style="margin-left:10px;font-size:12px;color:#888">
            auto：包含高风险操作时自动路由到隔离目录
          </span>
        </el-form-item>

        <el-form-item label="执行超时（秒）">
          <el-input-number v-model="form.exec_timeout" :min="30" :max="3600" />
        </el-form-item>

        <el-form-item label="允许网络访问">
          <el-switch v-model="form.network_enabled" />
          <span style="margin-left:10px;font-size:12px;color:#888">
            本地模式下始终允许（此设置预留未来扩展）
          </span>
        </el-form-item>

        <el-form-item label="工作目录">
          <el-input
            v-model="form.workspace_dir"
            placeholder="留空则使用数据目录下的 sandbox_workspace/"
            style="width:420px"
          />
          <div style="font-size:12px;color:#888;margin-top:4px">
            所有沙箱任务在此目录内执行，实现文件操作范围隔离
          </div>
        </el-form-item>

        <el-form-item>
          <el-button
            type="primary"
            @click="save()"
            :loading="saving"
            :disabled="saving || !hasUnsavedChanges"
          >
            保存配置
          </el-button>
          <span v-if="hasUnsavedChanges" style="margin-left:10px;font-size:12px;color:#e6a23c">
            有未保存的更改
          </span>
        </el-form-item>
      </el-form>
    </el-card>

    <!-- 说明卡片 -->
    <el-card shadow="never" style="margin-top:16px">
      <template #header><span>关于工作目录隔离模式</span></template>
      <el-descriptions :column="1" border size="small">
        <el-descriptions-item label="实现方式">
          所有沙箱任务在独立工作目录（sandbox_workspace/）中执行，文件操作限制在该目录内
        </el-descriptions-item>
        <el-descriptions-item label="优点">
          无需安装 QEMU，无需下载镜像，立即可用，兼容所有平台
        </el-descriptions-item>
        <el-descriptions-item label="参考来源">
          基于 LobsterAI（NetEase Youdao）的本地执行架构（CoworkRunner local 模式）
        </el-descriptions-item>
        <el-descriptions-item label="超时控制">
          任务执行超过设定秒数后自动终止，防止卡死
        </el-descriptions-item>
      </el-descriptions>
    </el-card>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import axios from 'axios'

const status = ref({})
const form = ref({
  enabled: false,
  execution_mode: 'auto',
  exec_timeout: 300,
  network_enabled: true,
  workspace_dir: '',
})
const saving = ref(false)
const testing = ref(false)
const testResult = ref(null)
const lastSavedConfig = ref(null)

const sandboxStateLabel = computed(() => {
  const s = status.value.state
  const map = { idle: '空闲', running: '执行中', error: '错误' }
  return map[s] || s || '就绪'
})

const sandboxStateTagType = computed(() => {
  const s = status.value.state
  if (s === 'running') return 'warning'
  if (s === 'error') return 'danger'
  return 'success'
})

const executionModeLabel = computed(() => {
  const mode = form.value.execution_mode || status.value.execution_mode || 'auto'
  const map = { auto: '自动', local: '本地', sandbox: '沙箱' }
  return map[mode] || mode
})

const snapshotConfig = (source = {}) => ({
  enabled: source.enabled ?? false,
  execution_mode: source.execution_mode ?? 'auto',
  exec_timeout: source.exec_timeout ?? 300,
  network_enabled: source.network_enabled ?? true,
  workspace_dir: source.workspace_dir ?? '',
})

const hasUnsavedChanges = computed(() => {
  if (!lastSavedConfig.value) return false
  return JSON.stringify(snapshotConfig(form.value)) !== JSON.stringify(lastSavedConfig.value)
})

const refreshStatus = async () => {
  try {
    const res = await axios.get('/api/sandbox/status')
    status.value = res.data.data || {}
  } catch {
    // ignore
  }
}

const loadConfig = async () => {
  try {
    const res = await axios.get('/api/sandbox/config')
    const cfg = snapshotConfig(res.data.data || {})
    Object.assign(form.value, cfg)
    lastSavedConfig.value = cfg
  } catch {
    ElMessage.error('加载沙箱配置失败')
  }
}

const save = async (options = {}) => {
  const { silent = false } = options
  saving.value = true
  try {
    const payload = snapshotConfig(form.value)
    const res = await axios.patch('/api/sandbox/config', payload)
    const savedConfig = snapshotConfig(res.data.data || payload)
    Object.assign(form.value, savedConfig)
    lastSavedConfig.value = savedConfig
    await refreshStatus()
    if (!silent) ElMessage.success('沙箱配置已保存')
    return true
  } catch (e) {
    ElMessage.error('保存失败：' + (e.response?.data?.detail || e.message))
    return false
  } finally {
    saving.value = false
  }
}

const prepare = async () => {
  try {
    await axios.post('/api/sandbox/prepare')
    await refreshStatus()
    ElMessage.success('沙箱工作目录已就绪')
  } catch (e) {
    ElMessage.error('初始化失败：' + (e.response?.data?.detail || e.message))
  }
}

const runTest = async () => {
  testResult.value = null
  testing.value = true
  ElMessage.info('正在执行沙箱冒烟测试，请稍候…')
  try {
    if (hasUnsavedChanges.value) {
      await save({ silent: true })
    }
    const res = await axios.post('/api/sandbox/test', { prompt: 'echo hello from sandbox' })
    testResult.value = {
      ...(res.data.data || {}),
      success: !!res.data.success,
      message: res.data.message || '',
    }
    await refreshStatus()
  } catch (e) {
    testResult.value = {
      success: false,
      error: e.response?.data?.detail || e.response?.data?.message || e.message || '沙箱冒烟测试失败',
    }
  } finally {
    testing.value = false
  }
}

onMounted(async () => {
  await Promise.all([loadConfig(), refreshStatus()])
})
</script>

<style scoped>
.sandbox-settings {
  padding: 4px;
}
.status-card {
  margin-bottom: 0;
}
</style>
