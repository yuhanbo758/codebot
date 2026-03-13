<template>
  <div class="email-settings">
    <el-form :model="form" label-width="120px">
      <el-form-item label="启用邮箱">
        <el-switch v-model="form.enabled" />
      </el-form-item>
      <el-form-item label="SMTP 主机">
        <el-input v-model="form.smtp_host" placeholder="smtp.gmail.com" />
      </el-form-item>
      <el-form-item label="SMTP 端口">
        <el-input-number v-model="form.smtp_port" :min="1" :max="65535" />
      </el-form-item>
      <el-form-item label="用户名">
        <el-input v-model="form.username" />
      </el-form-item>
      <el-form-item label="密码">
        <el-input v-model="form.password" type="password" show-password />
      </el-form-item>
      <el-form-item label="发件人">
        <el-input v-model="form.email_from" />
      </el-form-item>
      <el-form-item label="收件人">
        <el-input v-model="form.email_to" placeholder="多个邮箱用逗号分隔" />
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
  smtp_host: 'smtp.gmail.com',
  smtp_port: 587,
  username: '',
  password: '',
  email_from: '',
  email_to: ''
})

const loadConfig = async () => {
  try {
    const response = await axios.get('/api/notifications/config')
    const config = response.data.data
    form.value = {
      enabled: config.email_enabled,
      smtp_host: config.email_smtp_host || 'smtp.gmail.com',
      smtp_port: config.email_smtp_port || 587,
      username: config.email_username || '',
      password: config.email_password || '',
      email_from: config.email_from || '',
      email_to: (config.email_to || []).join(',')
    }
  } catch (error) {
    ElMessage.error('加载配置失败')
  }
}

const save = async () => {
  try {
    const emailList = form.value.email_to
      .split(',')
      .map(item => item.trim())
      .filter(item => item.length > 0)
    await axios.put('/api/notifications/config', {
      email_enabled: form.value.enabled,
      email_smtp_host: form.value.smtp_host,
      email_smtp_port: form.value.smtp_port,
      email_username: form.value.username,
      email_password: form.value.password,
      email_from: form.value.email_from,
      email_to: emailList
    })
    ElMessage.success('配置已保存')
  } catch (error) {
    ElMessage.error('保存失败')
  }
}

onMounted(loadConfig)
</script>

<style scoped>
.email-settings {
  padding: 20px;
}
</style>
