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
          <el-tag :type="backendStatusType" size="small">
            {{ backendStatusLabel }}
          </el-tag>
          <span style="margin-left:8px;font-size:12px;color:#888">Windows Sandbox 为可选后端；未选择时不会探测或启动</span>
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
      <el-alert
        v-if="!status.backend_selected"
        style="margin-top:12px"
        type="info"
        title="当前未选择强隔离后端"
        description="这是默认配置，不要求安装 Windows Sandbox，也不影响未启用沙箱路由的普通可信任务。启用沙箱路由后，需要强隔离的任务会失败关闭，不会偷降级到宿主机。"
        :closable="false"
        show-icon
      />
      <el-alert
        v-else
        style="margin-top:12px"
        :type="status.runtime_ready ? 'success' : 'warning'"
        :title="status.runtime_ready ? 'Windows Sandbox 已提供虚拟化隔离' : '已选择 Windows Sandbox，但系统运行时不可用'"
        :description="status.runtime_ready ? '默认禁用网络、剪贴板、打印机、音视频输入，仅映射专用工作目录。' : '如需使用该可选后端，请在 Windows 可选功能中启用 Windows Sandbox 并重启；在此之前需要隔离的任务会被拒绝。'"
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
          :disabled="saving || testing || !form.enabled || form.isolation_backend !== 'windows_sandbox'"
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
            启用后高风险任务要求系统级隔离；无法隔离时拒绝执行
          </span>
        </el-form-item>

        <el-form-item label="强隔离后端">
          <el-select v-model="form.isolation_backend" style="width:260px">
            <el-option label="不启用强隔离（默认）" value="none" />
            <el-option label="Windows Sandbox（可选）" value="windows_sandbox" />
          </el-select>
          <span style="margin-left:10px;font-size:12px;color:#888">
            只有主动选择 Windows Sandbox 后，Codebot 才会探测并使用它
          </span>
        </el-form-item>

        <el-form-item label="执行模式">
          <el-select v-model="form.execution_mode" style="width:200px">
            <el-option label="自动隔离高风险任务（auto）" value="auto" />
            <el-option label="始终本地，仅可信任务（local）" value="local" />
            <el-option label="强制所选隔离后端（sandbox）" value="sandbox" />
          </el-select>
          <span style="margin-left:10px;font-size:12px;color:#888">
            auto：高风险任务必须隔离；当前 OpenCode Agent 尚不能装入 Sandbox 时会安全拒绝
          </span>
        </el-form-item>

        <el-form-item label="执行超时（秒）">
          <el-input-number v-model="form.exec_timeout" :min="30" :max="3600" />
        </el-form-item>

        <el-form-item label="允许网络访问">
          <el-switch v-model="form.network_enabled" :disabled="form.isolation_backend !== 'windows_sandbox'" />
          <span style="margin-left:10px;font-size:12px;color:#888">
            仅影响 Windows Sandbox；建议保持关闭
          </span>
        </el-form-item>

        <el-form-item label="工作目录">
          <el-input
            v-model="form.workspace_dir"
            placeholder="留空则使用数据目录下的 sandbox_workspace/"
            style="width:420px"
          />
          <div style="font-size:12px;color:#888;margin-top:4px">
            该目录会作为唯一可写工作目录映射到 Windows Sandbox
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
      <template #header><span>关于 Windows Sandbox 隔离模式</span></template>
      <el-descriptions :column="1" border size="small">
        <el-descriptions-item label="实现方式">
          每次命令启动一次性 Windows Sandbox，仅映射工作目录和临时结果目录
        </el-descriptions-item>
        <el-descriptions-item label="优点">
          提供虚拟化文件系统边界，可关闭网络、剪贴板、打印机和音视频输入
        </el-descriptions-item>
        <el-descriptions-item label="参考来源">
          使用 Windows 官方 Windows Sandbox 可选功能；Codebot 不会自动安装或要求启用
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
  isolation_backend: 'none',
  execution_mode: 'auto',
  exec_timeout: 300,
  network_enabled: false,
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

const backendStatusLabel = computed(() => {
  if (!status.value.backend_selected) return '未选择强隔离'
  return status.value.runtime_ready ? 'Windows Sandbox' : '所选后端不可用'
})

const backendStatusType = computed(() => {
  if (!status.value.backend_selected) return 'info'
  return status.value.runtime_ready ? 'success' : 'danger'
})

const executionModeLabel = computed(() => {
  const mode = form.value.execution_mode || status.value.execution_mode || 'auto'
  const map = { auto: '自动', local: '本地', sandbox: '沙箱' }
  return map[mode] || mode
})

const snapshotConfig = (source = {}) => ({
  enabled: source.enabled ?? false,
  isolation_backend: source.isolation_backend ?? 'none',
  execution_mode: source.execution_mode ?? 'auto',
  exec_timeout: source.exec_timeout ?? 300,
  network_enabled: source.network_enabled ?? false,
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
