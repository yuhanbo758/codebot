<template>
  <el-form label-width="150px" v-loading="loading">
    <el-form-item label="OpenCode CLI">
      <div class="runtime-controls">
        <el-tag :type="runtime?.serverConnected ? 'success' : 'info'">{{ runtime?.serverConnected ? '服务已连接' : runtime?.cli ? 'CLI 已找到' : 'CLI 未安装' }}</el-tag>
        <span v-if="runtime?.cli">{{ runtime.cli.version }} · {{ runtime.cli.source === 'managed' ? 'Codebot 下载' : runtime.cli.source === 'bundled' ? '随包运行时' : '本机安装' }}</span>
        <el-button @click="loadRuntime" :disabled="installing">刷新状态</el-button>
        <el-button
          v-if="!runtime?.serverConnected && (runtime?.cli || runtime?.downloadSupported)"
          type="primary"
          :loading="installing"
          @click="installRuntime"
        >{{ runtime?.cli ? '启动 OpenCode' : '一键下载并启动 OpenCode CLI' }}</el-button>
      </div>
      <el-progress v-if="runtime?.download?.installing && runtime.download.totalBytes" class="download-progress" :percentage="Math.min(100, Math.round(runtime.download.downloadedBytes / runtime.download.totalBytes * 100))" />
      <div class="access-tip">优先复用本机 CLI；Windows 安装包不内置 OpenCode。仅点击一键下载时，从官方 npm 源校验并安装到 Codebot 用户目录，不修改系统全局 CLI。</div>
      <el-alert v-if="runtime && !runtime.cli && !runtime.serverConnected && !runtime.downloadSupported" title="未找到 OpenCode CLI" description="请先在本机安装 OpenCode CLI，或连接已有的 OpenCode Serve。" type="info" show-icon :closable="false" />
    </el-form-item>
    <el-form-item label="完全访问">
      <el-switch v-model="fullAccess" />
      <div class="access-tip">保存后从下一条消息生效：允许工具、文件、命令及联网权限，权限请求自动允许。普通问题仍需回答，计划模式保留原限制。</div>
    </el-form-item>
    <el-form-item><el-button type="primary" :loading="saving" @click="save">保存 OpenCode 设置</el-button></el-form-item>
  </el-form>
</template>

<script setup>
import { onMounted, onUnmounted, ref } from 'vue'
import axios from 'axios'
import { ElMessage } from 'element-plus'

const fullAccess = ref(false)
const loading = ref(false)
const saving = ref(false)
const installing = ref(false)
const runtime = ref(null)
let progressTimer = null
onUnmounted(() => { if (progressTimer) clearInterval(progressTimer) })
const loadRuntime = async () => {
  try {
    runtime.value = (await axios.get('/api/config/opencode/runtime/status')).data?.data || null
  } catch (error) {
    ElMessage.error(error?.response?.data?.detail || '读取 OpenCode CLI 状态失败')
  }
}
onMounted(async () => {
  loading.value = true
  try {
    const { data } = await axios.get('/api/config/opencode/access')
    fullAccess.value = Boolean(data.data.full_access)
    await loadRuntime()
  } catch {
    ElMessage.error('加载 OpenCode 设置失败')
  } finally {
    loading.value = false
  }
})
const installRuntime = async () => {
  installing.value = true
  progressTimer = setInterval(loadRuntime, 1000)
  try {
    await axios.post('/api/config/opencode/runtime/install')
    ElMessage.success('OpenCode 服务已启动')
  } catch (error) {
    ElMessage.error(error?.response?.data?.detail || 'OpenCode 启动或下载失败')
  } finally {
    clearInterval(progressTimer)
    progressTimer = null
    installing.value = false
    await loadRuntime()
  }
}
// 只发送权限字段，避免覆盖已有 OpenCode 地址和启动配置。
const save = async () => {
  saving.value = true
  try {
    await axios.patch('/api/config/opencode/access', { full_access: fullAccess.value })
    ElMessage.success('OpenCode 设置已保存，从下一条消息生效')
  } catch {
    ElMessage.error('保存 OpenCode 设置失败')
  } finally {
    saving.value = false
  }
}
</script>

<style scoped>
.access-tip { color: var(--el-text-color-secondary); font-size: 12px; line-height: 1.6; margin-left: 12px; }
.runtime-controls { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; }
.download-progress { width: min(100%, 420px); margin-top: 8px; }
</style>
