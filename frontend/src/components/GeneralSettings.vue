<template>
  <div class="general-settings">
    <el-form :model="form" label-width="140px">
      <el-form-item label="应用名称">
        <el-input v-model="form.app_name" />
      </el-form-item>
      <el-form-item label="语言">
        <el-select v-model="form.language" style="width: 200px">
          <el-option label="中文" value="zh-CN" />
          <el-option label="English" value="en-US" />
        </el-select>
      </el-form-item>
      <el-form-item label="自动启动">
        <el-switch v-model="form.auto_start" />
      </el-form-item>
      <el-form-item label="紧凑模式">
        <el-switch v-model="form.compact_mode" />
      </el-form-item>
      <el-form-item>
        <el-button type="primary" @click="save">保存设置</el-button>
      </el-form-item>
    </el-form>

    <el-divider />

    <!-- 链接打开方式 & 文件存储路径 -->
    <div class="section-block">
      <div class="section-title">浏览器与文件设置</div>
      <el-form :model="generalForm" label-width="140px">
        <el-form-item label="链接打开方式">
          <el-radio-group v-model="generalForm.link_open_mode">
            <el-radio value="system">系统默认浏览器</el-radio>
            <el-radio value="builtin">内置浏览器</el-radio>
          </el-radio-group>
          <div class="form-item-tip">聊天窗口中的网址链接使用哪种方式打开</div>
        </el-form-item>
        <el-form-item label="文件存储路径">
          <div class="config-path-row">
            <el-input
              v-model="generalForm.file_storage_path"
              placeholder="留空则使用系统默认位置"
              clearable
            />
          </div>
          <div class="form-item-tip">AI 生成的文件（如导出的 MD、CSV 等）将保存到此目录</div>
        </el-form-item>
        <el-form-item>
          <el-button type="primary" @click="saveGeneralConfig" :loading="generalSaving">保存浏览器与文件设置</el-button>
        </el-form-item>
      </el-form>
    </div>

    <el-divider />

    <!-- 访问地址信息 -->
    <div class="network-info-section">
      <div class="section-title">服务访问地址</div>
      <div v-if="networkLoading" class="network-loading">
        <el-icon class="is-loading"><Loading /></el-icon>
        <span>正在获取网络信息...</span>
      </div>
      <div v-else-if="networkError" class="network-error">
        <el-alert type="warning" :description="networkError" show-icon :closable="false" />
      </div>
      <div v-else class="network-cards">
        <div class="network-card">
          <div class="card-label">本地访问</div>
          <div class="card-url">
            <span class="url-text">{{ networkInfo.local_url }}</span>
            <el-button
              text
              size="small"
              class="copy-btn"
              @click="copyUrl(networkInfo.local_url)"
            >
              <el-icon><CopyDocument /></el-icon>
              复制
            </el-button>
          </div>
          <div class="card-desc">仅本机可访问</div>
        </div>

        <div class="network-card">
          <div class="card-label">局域网访问</div>
          <div class="card-url">
            <span class="url-text">{{ networkInfo.lan_url }}</span>
            <el-button
              text
              size="small"
              class="copy-btn"
              @click="copyUrl(networkInfo.lan_url)"
            >
              <el-icon><CopyDocument /></el-icon>
              复制
            </el-button>
          </div>
          <div class="card-desc">同一局域网内的设备（手机、平板等）均可访问</div>
        </div>
      </div>

      <el-button
        text
        size="small"
        class="refresh-btn"
        :loading="networkLoading"
        @click="loadNetworkInfo"
      >
        刷新地址
      </el-button>
    </div>

    <el-divider />

    <div class="config-file-section">
      <div class="section-title">配置文件</div>
      <el-form label-width="140px">
        <el-form-item label="当前配置路径">
          <div class="config-path-row">
            <el-input :model-value="activeConfigPath" readonly />
            <el-button text @click="copyUrl(activeConfigPath)">复制</el-button>
          </div>
        </el-form-item>
        <el-form-item label="导入配置文件">
          <div class="config-path-row">
            <el-input
              v-model="importConfigPath"
              placeholder="请输入历史 config.json 的绝对路径"
            />
            <el-button type="primary" :loading="importLoading" @click="loadConfigFromPath">
              加载并应用
            </el-button>
          </div>
        </el-form-item>
      </el-form>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { Loading, CopyDocument } from '@element-plus/icons-vue'

const form = ref({
  app_name: 'Codebot',
  language: 'zh-CN',
  auto_start: false,
  compact_mode: false
})

const generalForm = ref({
  link_open_mode: 'system',
  file_storage_path: ''
})
const generalSaving = ref(false)

const networkInfo = ref({ local_url: '', lan_url: '' })
const networkLoading = ref(false)
const networkError = ref('')
const activeConfigPath = ref('')
const importConfigPath = ref('')
const importLoading = ref(false)

const save = () => {
  ElMessage.success('设置已保存')
}

const loadGeneralConfig = async () => {
  try {
    const res = await fetch('/api/config/general')
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const json = await res.json()
    if (json?.success && json?.data) {
      generalForm.value.link_open_mode = json.data.link_open_mode || 'system'
      generalForm.value.file_storage_path = json.data.file_storage_path || ''
    }
  } catch (e) {
    console.warn('加载通用配置失败:', e)
  }
}

const saveGeneralConfig = async () => {
  generalSaving.value = true
  try {
    const res = await fetch('/api/config/general', {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(generalForm.value)
    })
    const json = await res.json()
    if (!res.ok || !json?.success) {
      throw new Error(json?.detail || json?.message || `HTTP ${res.status}`)
    }
    // 同步链接打开模式给 Electron 主进程，使拦截器立即生效
    if (window.electronAPI?.setLinkOpenMode) {
      window.electronAPI.setLinkOpenMode(generalForm.value.link_open_mode || 'system')
    }
    ElMessage.success('浏览器与文件设置已保存')
  } catch (e) {
    ElMessage.error(`保存失败：${e.message}`)
  } finally {
    generalSaving.value = false
  }
}

const loadNetworkInfo = async () => {
  networkLoading.value = true
  networkError.value = ''
  try {
    const res = await fetch('/api/network-info')
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    networkInfo.value = await res.json()
  } catch (e) {
    networkError.value = `无法获取网络信息：${e.message}`
  } finally {
    networkLoading.value = false
  }
}

const copyUrl = async (url) => {
  try {
    if (window.electronAPI?.copyText) {
      await window.electronAPI.copyText(url)
    } else {
      await navigator.clipboard.writeText(url)
    }
    ElMessage.success('已复制到剪贴板')
  } catch {
    ElMessage.error('复制失败，请手动复制')
  }
}

const loadConfigPathInfo = async () => {
  try {
    const res = await fetch('/api/config/file-info')
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const json = await res.json()
    activeConfigPath.value = json?.data?.active_config_path || ''
  } catch {
    activeConfigPath.value = ''
  }
}

const loadConfigFromPath = async () => {
  if (!importConfigPath.value.trim()) {
    ElMessage.warning('请先输入配置文件路径')
    return
  }
  importLoading.value = true
  try {
    const res = await fetch('/api/config/load-from-path', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: importConfigPath.value.trim() })
    })
    const json = await res.json()
    if (!res.ok || !json?.success) {
      throw new Error(json?.detail || json?.message || `HTTP ${res.status}`)
    }
    ElMessage.success('配置已加载，已应用到当前环境')
    importConfigPath.value = ''
    await loadConfigPathInfo()
    await loadNetworkInfo()
    await loadGeneralConfig()
  } catch (e) {
    ElMessage.error(`加载失败：${e.message}`)
  } finally {
    importLoading.value = false
  }
}

onMounted(() => {
  loadNetworkInfo()
  loadConfigPathInfo()
  loadGeneralConfig()
})
</script>

<style scoped>
.general-settings {
  padding: 20px;
}

.section-block {
  margin-top: 8px;
}

.section-title {
  font-size: 15px;
  font-weight: 600;
  color: #303133;
  margin-bottom: 16px;
}

.form-item-tip {
  font-size: 12px;
  color: #909399;
  margin-top: 4px;
  line-height: 1.4;
}

.network-info-section {
  margin-top: 8px;
}

.network-loading {
  display: flex;
  align-items: center;
  gap: 8px;
  color: #909399;
  font-size: 14px;
}

.network-error {
  margin-bottom: 8px;
}

.network-cards {
  display: flex;
  gap: 16px;
  flex-wrap: wrap;
}

.network-card {
  flex: 1;
  min-width: 260px;
  background: #f5f7fa;
  border: 1px solid #e4e7ed;
  border-radius: 8px;
  padding: 16px 20px;
}

.card-label {
  font-size: 12px;
  color: #909399;
  margin-bottom: 8px;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.card-url {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
}

.url-text {
  font-size: 15px;
  font-weight: 600;
  color: #409eff;
  font-family: monospace;
  word-break: break-all;
}

.copy-btn {
  flex-shrink: 0;
  color: #909399;
}

.copy-btn:hover {
  color: #409eff;
}

.card-desc {
  font-size: 12px;
  color: #c0c4cc;
}

.refresh-btn {
  margin-top: 12px;
  color: #909399;
}

.config-file-section {
  margin-top: 8px;
}

.config-path-row {
  width: 100%;
  display: flex;
  gap: 10px;
}
</style>
