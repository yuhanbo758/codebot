<template>
  <div class="security-settings">
    <el-alert
      type="info"
      title="本机访问保持免认证；其他局域网设备必须先配对或携带 API Token。"
      :closable="false"
      show-icon
    />

    <el-form :model="form" label-width="160px" style="margin-top:18px;max-width:760px">
      <el-form-item label="启用局域网认证">
        <el-switch v-model="form.lan_auth_enabled" />
      </el-form-item>
      <el-form-item label="配对码有效期">
        <el-input-number v-model="form.pairing_code_ttl_seconds" :min="60" :max="1800" :step="60" />
        <span class="hint">秒</span>
      </el-form-item>
      <el-form-item label="设备会话有效期">
        <el-input-number v-model="form.session_hours" :min="1" :max="8760" />
        <span class="hint">小时；重启后签名会话仍有效，重新生成 Token 会让旧会话立即失效</span>
      </el-form-item>
      <el-form-item>
        <el-button type="primary" :loading="saving" @click="saveConfig">保存安全设置</el-button>
      </el-form-item>
    </el-form>

    <el-divider content-position="left">设备配对</el-divider>
    <div class="action-row">
      <el-button type="primary" :loading="pairingLoading" @click="createPairingCode">生成一次性配对码</el-button>
      <el-tag v-if="pairingCode" size="large" type="success" class="pair-code">{{ pairingCode }}</el-tag>
      <span v-if="pairingCode" class="hint">{{ pairingExpiresIn }} 秒内有效，仅可使用一次</span>
    </div>

    <el-divider content-position="left">API 客户端 Token</el-divider>
    <el-alert
      type="warning"
      title="Token 等同于完整 Codebot API 权限，请勿发送到聊天、日志或公开仓库。"
      :closable="false"
      show-icon
    />
    <div class="action-row" style="margin-top:12px">
      <el-button @click="loadToken">{{ apiToken ? '隐藏 Token' : '显示 Token' }}</el-button>
      <el-button type="danger" plain @click="regenerateToken">重新生成</el-button>
    </div>
    <el-input v-if="apiToken" v-model="apiToken" readonly type="password" show-password style="margin-top:12px;max-width:760px" />
    <p class="hint">API 客户端可发送 <code>Authorization: Bearer &lt;token&gt;</code> 或 <code>X-Codebot-Token</code>。</p>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import axios from 'axios'
import { ElMessage, ElMessageBox } from 'element-plus'

const form = ref({ lan_auth_enabled: true, pairing_code_ttl_seconds: 300, session_hours: 720 })
const saving = ref(false)
const pairingLoading = ref(false)
const pairingCode = ref('')
const pairingExpiresIn = ref(0)
const apiToken = ref('')

const loadConfig = async () => {
  const response = await axios.get('/api/security/config')
  Object.assign(form.value, response.data?.data || {})
}

const saveConfig = async () => {
  saving.value = true
  try {
    await axios.patch('/api/security/config', form.value)
    ElMessage.success('访问安全设置已保存')
  } catch (error) {
    ElMessage.error(error?.response?.data?.detail || '保存失败')
  } finally {
    saving.value = false
  }
}

const createPairingCode = async () => {
  pairingLoading.value = true
  try {
    const response = await axios.post('/api/security/pair-code')
    pairingCode.value = response.data?.data?.code || ''
    pairingExpiresIn.value = response.data?.data?.expires_in || 0
  } catch (error) {
    ElMessage.error(error?.response?.data?.detail || '生成配对码失败')
  } finally {
    pairingLoading.value = false
  }
}

const loadToken = async () => {
  if (apiToken.value) {
    apiToken.value = ''
    return
  }
  try {
    const response = await axios.get('/api/security/token')
    apiToken.value = response.data?.data?.token || ''
  } catch (error) {
    ElMessage.error(error?.response?.data?.detail || '读取 Token 失败')
  }
}

const regenerateToken = async () => {
  try {
    await ElMessageBox.confirm('重新生成后，所有已配对设备和旧 API Token 都会立即失效。是否继续？', '确认重新生成', { type: 'warning' })
    const response = await axios.post('/api/security/token/regenerate')
    apiToken.value = response.data?.data?.token || ''
    ElMessage.success('Token 已重新生成')
  } catch (error) {
    if (error !== 'cancel') ElMessage.error(error?.response?.data?.detail || '重新生成失败')
  }
}

onMounted(() => loadConfig().catch(() => ElMessage.error('加载访问安全设置失败')))
</script>

<style scoped>
.security-settings { padding: 4px; }
.action-row { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.pair-code { font-size: 24px; letter-spacing: 5px; padding: 20px 18px; }
.hint { margin-left: 8px; color: #909399; font-size: 13px; }
</style>
