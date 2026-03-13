<template>
  <div class="sandbox-settings">
    <!-- 状态卡片 -->
    <el-card class="status-card" shadow="never">
      <template #header>
        <span>沙箱状态</span>
        <el-button style="float:right" size="small" @click="refreshStatus">刷新</el-button>
      </template>
      <el-descriptions :column="2" border size="small">
        <el-descriptions-item label="VM 状态">
          <el-tag :type="vmStateTagType">{{ vmStateLabel }}</el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="平台">{{ status.platform || '—' }}</el-descriptions-item>
        <el-descriptions-item label="QEMU">
          <el-tag :type="status.qemu_available ? 'success' : 'danger'" size="small">
            {{ status.qemu_available ? '已检测到' : '未找到' }}
          </el-tag>
          <span v-if="status.qemu_path" style="margin-left:8px;font-size:12px;color:#888">
            {{ status.qemu_path }}
          </span>
          <el-button
            v-if="!status.qemu_available && !status.installing_qemu"
            size="small"
            type="warning"
            style="margin-left:8px"
            @click="installQemu"
          >
            自动安装
          </el-button>
          <span v-if="status.installing_qemu" style="margin-left:8px;font-size:12px;color:#e6a23c">
            安装中…
          </span>
        </el-descriptions-item>
        <el-descriptions-item label="IPC 模式">{{ status.ipc_mode || '—' }}</el-descriptions-item>
        <el-descriptions-item label="镜像">
          <el-tag :type="status.image_available ? 'success' : 'warning'" size="small">
            {{ status.image_available ? '已就绪' : '未下载' }}
          </el-tag>
          <span v-if="status.image_size_bytes" style="margin-left:8px;font-size:12px;color:#888">
            {{ formatBytes(status.image_size_bytes) }}
          </span>
        </el-descriptions-item>
        <el-descriptions-item label="运行时就绪">
          <el-tag :type="status.runtime_ready ? 'success' : 'info'" size="small">
            {{ status.runtime_ready ? '是' : '否' }}
          </el-tag>
        </el-descriptions-item>
      </el-descriptions>

      <!-- 下载进度 -->
      <div v-if="status.downloading" style="margin-top:12px">
        <div style="margin-bottom:4px;font-size:13px">正在下载沙箱镜像…</div>
        <el-progress :percentage="Math.round((status.download_progress || 0) * 100)" />
      </div>
      <div v-if="status.download_error" style="margin-top:8px">
        <el-alert type="error" :title="status.download_error" :closable="false" show-icon />
      </div>

      <!-- QEMU 安装进度 -->
      <div v-if="status.installing_qemu" style="margin-top:12px">
        <div style="margin-bottom:4px;font-size:13px">
          正在安装 QEMU…
          <span style="color:#888;font-size:12px">
            <template v-if="status.install_qemu_progress < 0.5">（下载中）</template>
            <template v-else>（安装中，请在弹出的用户账户控制对话框中点击「是」）</template>
          </span>
        </div>
        <el-progress :percentage="Math.round((status.install_qemu_progress || 0) * 100)" status="warning" />
      </div>
      <div v-if="status.install_qemu_error" style="margin-top:8px">
        <el-alert type="error" :closable="false" show-icon>
          <template #title>
            {{ status.install_qemu_error }}
          </template>
          <template #default>
            <div style="margin-top:6px;font-size:12px">
              如自动安装失败，可
              <a href="https://qemu.weilnetz.de/w64/" target="_blank" rel="noopener"
                 style="color:#409eff;text-decoration:underline">
                手动下载 QEMU 安装包
              </a>
              安装后，在下方「QEMU 路径」填写 qemu-system-x86_64.exe 的完整路径并保存，再点击刷新即可。
            </div>
          </template>
        </el-alert>
      </div>

      <!-- 操作按钮 -->
      <div style="margin-top:16px;display:flex;gap:8px">
        <el-button type="primary" size="small" :disabled="!form.enabled || status.vm_running" @click="startVM">
          启动 VM
        </el-button>
        <el-button type="danger" size="small" :disabled="!status.vm_running" @click="stopVM">
          停止 VM
        </el-button>
        <el-button size="small" :disabled="status.downloading" @click="prepare">
          检测 / 下载运行时
        </el-button>
        <el-button size="small" :disabled="!form.enabled || !status.runtime_ready" @click="runTest">
          冒烟测试
        </el-button>
      </div>

      <!-- 测试结果 -->
      <div v-if="testResult" style="margin-top:12px">
        <el-alert
          :type="testResult.success ? 'success' : 'error'"
          :title="testResult.success ? '测试通过' : '测试失败'"
          :description="testResult.content || testResult.error"
          :closable="false"
          show-icon
        />
      </div>
    </el-card>

    <!-- 配置表单 -->
    <el-card shadow="never" style="margin-top:16px">
      <template #header><span>沙箱配置</span></template>
      <el-form :model="form" label-width="150px" size="default">

        <el-form-item label="启用沙箱">
          <el-switch v-model="form.enabled" />
          <span style="margin-left:10px;font-size:12px;color:#888">
            启用后聊天任务可在隔离 VM 中执行
          </span>
        </el-form-item>

        <el-form-item label="执行模式">
          <el-select v-model="form.execution_mode" style="width:200px">
            <el-option label="自动（auto）" value="auto" />
            <el-option label="始终本地（local）" value="local" />
            <el-option label="始终沙箱（sandbox）" value="sandbox" />
          </el-select>
          <span style="margin-left:10px;font-size:12px;color:#888">
            auto：高风险操作自动路由到沙箱
          </span>
        </el-form-item>

        <el-form-item label="VM 内存（MB）">
          <el-input-number v-model="form.memory_mb" :min="512" :max="16384" :step="512" />
        </el-form-item>

        <el-form-item label="启动超时（秒）">
          <el-input-number v-model="form.startup_timeout" :min="10" :max="300" />
        </el-form-item>

        <el-form-item label="执行超时（秒）">
          <el-input-number v-model="form.exec_timeout" :min="30" :max="3600" />
        </el-form-item>

        <el-form-item label="快照模式">
          <el-switch v-model="form.snapshot_mode" />
          <span style="margin-left:10px;font-size:12px;color:#888">
            每次启动使用全新快照，更安全
          </span>
        </el-form-item>

        <el-form-item label="允许网络访问">
          <el-switch v-model="form.network_enabled" />
        </el-form-item>

        <el-form-item label="自动下载镜像">
          <el-switch v-model="form.auto_download" />
        </el-form-item>

        <el-form-item label="QEMU 路径">
          <el-input v-model="form.runtime_binary" placeholder="留空自动检测" style="width:400px" />
        </el-form-item>

        <el-form-item label="镜像路径">
          <el-input v-model="form.image_path" placeholder="留空使用默认路径" style="width:400px" />
        </el-form-item>

        <el-form-item label="镜像下载 URL">
          <el-input v-model="form.image_url" placeholder="留空使用默认 CDN" style="width:400px" />
        </el-form-item>

        <el-form-item label="工作目录">
          <el-input v-model="form.workspace_dir" placeholder="留空使用默认目录" style="width:400px" />
        </el-form-item>

        <el-form-item>
          <el-button type="primary" @click="save" :loading="saving">保存配置</el-button>
        </el-form-item>

      </el-form>
    </el-card>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { ElMessage } from 'element-plus'
import axios from 'axios'

const status = ref({})
const form = ref({
  enabled: false,
  execution_mode: 'auto',
  memory_mb: 2048,
  startup_timeout: 60,
  exec_timeout: 300,
  snapshot_mode: true,
  network_enabled: true,
  auto_download: true,
  runtime_binary: '',
  image_path: '',
  image_url: '',
  workspace_dir: '',
})
const saving = ref(false)
const testResult = ref(null)
let pollTimer = null

// ── 状态显示 ──────────────────────────────────────────────────────────────────

const vmStateLabel = computed(() => {
  const s = status.value.state
  const map = { idle: '空闲', starting: '启动中', running: '运行中', stopping: '停止中', error: '错误' }
  return map[s] || s || '未知'
})

const vmStateTagType = computed(() => {
  const s = status.value.state
  if (s === 'running') return 'success'
  if (s === 'error') return 'danger'
  if (s === 'starting' || s === 'stopping') return 'warning'
  return 'info'
})

// ── API 调用 ──────────────────────────────────────────────────────────────────

const refreshStatus = async () => {
  try {
    const res = await axios.get('/api/sandbox/status')
    status.value = res.data.data || {}
  } catch {
    // 忽略
  }
}

const loadConfig = async () => {
  try {
    const res = await axios.get('/api/sandbox/config')
    const cfg = res.data.data || {}
    Object.assign(form.value, {
      enabled: cfg.enabled ?? false,
      execution_mode: cfg.execution_mode ?? 'auto',
      memory_mb: cfg.memory_mb ?? 2048,
      startup_timeout: cfg.startup_timeout ?? 60,
      exec_timeout: cfg.exec_timeout ?? 300,
      snapshot_mode: cfg.snapshot_mode ?? true,
      network_enabled: cfg.network_enabled ?? true,
      auto_download: cfg.auto_download ?? true,
      runtime_binary: cfg.runtime_binary ?? '',
      image_path: cfg.image_path ?? '',
      image_url: cfg.image_url ?? '',
      workspace_dir: cfg.workspace_dir ?? '',
    })
  } catch {
    ElMessage.error('加载沙箱配置失败')
  }
}

const save = async () => {
  saving.value = true
  try {
    await axios.patch('/api/sandbox/config', form.value)
    ElMessage.success('沙箱配置已保存')
  } catch (e) {
    ElMessage.error('保存失败：' + (e.response?.data?.detail || e.message))
  } finally {
    saving.value = false
  }
}

const prepare = async () => {
  try {
    await axios.post('/api/sandbox/prepare')
    ElMessage.info('已触发运行时检测/下载，请稍候…')
    startPolling()
  } catch (e) {
    ElMessage.error('触发失败：' + (e.response?.data?.detail || e.message))
  }
}

const installQemu = async () => {
  try {
    const res = await axios.post('/api/sandbox/install-qemu')
    ElMessage.info(res.data.message || '已触发 QEMU 安装，请稍候…')
    startPolling()
  } catch (e) {
    ElMessage.error('触发失败：' + (e.response?.data?.detail || e.message))
  }
}

const startVM = async () => {
  try {
    await axios.post('/api/sandbox/start')
    ElMessage.success('沙箱 VM 已启动')
    await refreshStatus()
  } catch (e) {
    ElMessage.error('启动失败：' + (e.response?.data?.detail || e.message))
  }
}

const stopVM = async () => {
  try {
    await axios.post('/api/sandbox/stop')
    ElMessage.success('沙箱 VM 已停止')
    await refreshStatus()
  } catch (e) {
    ElMessage.error('停止失败：' + (e.response?.data?.detail || e.message))
  }
}

const runTest = async () => {
  testResult.value = null
  try {
    const res = await axios.post('/api/sandbox/test', { prompt: 'echo hello from sandbox' })
    testResult.value = res.data.data || {}
    testResult.value.success = res.data.success
  } catch (e) {
    testResult.value = { success: false, error: e.response?.data?.detail || e.message }
  }
}

// ── 下载进度轮询 ──────────────────────────────────────────────────────────────

const startPolling = () => {
  if (pollTimer) return
  pollTimer = setInterval(async () => {
    await refreshStatus()
    if (!status.value.downloading && !status.value.installing_qemu) {
      stopPolling()
    }
  }, 2000)
}

const stopPolling = () => {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

// ── 格式化 ────────────────────────────────────────────────────────────────────

const formatBytes = (bytes) => {
  if (!bytes) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB']
  let i = 0
  let v = bytes
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++ }
  return `${v.toFixed(1)} ${units[i]}`
}

// ── 生命周期 ──────────────────────────────────────────────────────────────────

onMounted(async () => {
  await Promise.all([loadConfig(), refreshStatus()])
  if (status.value.downloading || status.value.installing_qemu) startPolling()
})

onUnmounted(() => stopPolling())
</script>

<style scoped>
.sandbox-settings {
  padding: 4px;
}
.status-card {
  margin-bottom: 0;
}
</style>
