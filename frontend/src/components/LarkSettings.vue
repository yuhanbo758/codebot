<template>
  <div class="lark-settings">
    <el-form :model="form" label-width="120px">
      <el-form-item label="启用飞书通知">
        <el-switch v-model="form.enabled" />
      </el-form-item>
      <el-form-item label="Webhook URL">
        <el-input v-model="form.webhook_url" placeholder="https://open.feishu.cn/open-apis/bot/v2/hook/..." />
      </el-form-item>
      <el-form-item label="签名密钥">
        <el-input v-model="form.secret" />
      </el-form-item>
      <el-form-item label="启用飞书机器人">
        <el-switch v-model="form.bot_enabled" />
      </el-form-item>
      <el-form-item label="接入方式">
        <el-select v-model="form.connection_mode" style="width: 100%">
          <el-option label="长连接（推荐）" value="ws" />
          <el-option label="Webhook 回调" value="webhook" />
        </el-select>
      </el-form-item>
      <el-form-item label="App ID">
        <el-input v-model="form.app_id" />
      </el-form-item>
      <el-form-item label="App Secret">
        <el-input v-model="form.app_secret" />
      </el-form-item>
      <el-form-item label="Verify Token">
        <el-input v-model="form.verify_token" />
      </el-form-item>
      <el-form-item label="Encrypt Key">
        <el-input v-model="form.encrypt_key" />
      </el-form-item>
      <el-form-item label="接收类型">
        <el-select v-model="form.receive_id_type" style="width: 100%">
          <el-option label="chat_id" value="chat_id" />
          <el-option label="open_id" value="open_id" />
          <el-option label="user_id" value="user_id" />
        </el-select>
      </el-form-item>
      <el-form-item>
        <el-button type="primary" @click="save">保存配置</el-button>
      </el-form-item>
    </el-form>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import axios from 'axios'

const form = ref({
  enabled: false,
  webhook_url: '',
  secret: '',
  bot_enabled: false,
  connection_mode: 'ws',
  app_id: '',
  app_secret: '',
  verify_token: '',
  encrypt_key: '',
  receive_id_type: 'chat_id'
})

const loadConfig = async () => {
  try {
    const [notifyResponse, botResponse] = await Promise.all([
      axios.get('/api/notifications/config'),
      axios.get('/api/lark/config')
    ])
    const notifyConfig = notifyResponse.data.data
    const botConfig = botResponse.data.data
    form.value = {
      enabled: notifyConfig.lark_enabled,
      webhook_url: notifyConfig.lark_webhook_url || '',
      secret: notifyConfig.lark_secret || '',
      bot_enabled: botConfig.enabled,
      connection_mode: botConfig.connection_mode || 'ws',
      app_id: botConfig.app_id || '',
      app_secret: botConfig.app_secret || '',
      verify_token: botConfig.verify_token || '',
      encrypt_key: botConfig.encrypt_key || '',
      receive_id_type: botConfig.receive_id_type || 'chat_id'
    }
  } catch (error) {
    ElMessage.error('加载配置失败')
  }
}

const save = async () => {
  try {
    await Promise.all([
      axios.put('/api/notifications/config', {
        lark_enabled: form.value.enabled,
        lark_webhook_url: form.value.webhook_url,
        lark_secret: form.value.secret
      }),
      axios.put('/api/lark/config', {
        enabled: form.value.bot_enabled,
        connection_mode: form.value.connection_mode,
        app_id: form.value.app_id,
        app_secret: form.value.app_secret,
        verify_token: form.value.verify_token,
        encrypt_key: form.value.encrypt_key,
        receive_id_type: form.value.receive_id_type
      })
    ])
    ElMessage.success('配置已保存')
  } catch (error) {
    ElMessage.error('保存失败')
  }
}

onMounted(loadConfig)
</script>

<style scoped>
.lark-settings {
  padding: 20px;
}
</style>
