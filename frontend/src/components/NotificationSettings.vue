<template>
  <div class="notification-settings">
    <el-form :model="form" label-width="140px">
      <el-form-item label="应用内通知">
        <el-switch v-model="form.app_enabled" />
      </el-form-item>
      <el-form-item label="系统桌面通知">
        <el-switch v-model="form.desktop_enabled" />
        <span class="form-hint">任务执行完成时推送到操作系统通知中心</span>
      </el-form-item>
      <el-form-item label="飞书通知">
        <el-switch v-model="form.lark_enabled" />
      </el-form-item>
      <el-form-item label="邮箱通知">
        <el-switch v-model="form.email_enabled" />
      </el-form-item>
      <el-form-item label="轮询间隔(秒)">
        <el-input-number v-model="form.poll_interval" :min="5" :max="120" />
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
  app_enabled: true,
  desktop_enabled: false,
  lark_enabled: false,
  email_enabled: false,
  poll_interval: 30
})

const loadConfig = async () => {
  try {
    const response = await axios.get('/api/notifications/config')
    const config = response.data.data
    form.value = {
      app_enabled: config.app_enabled,
      desktop_enabled: config.desktop_enabled ?? false,
      lark_enabled: config.lark_enabled,
      email_enabled: config.email_enabled,
      poll_interval: config.poll_interval ?? 30
    }
  } catch (error) {
    ElMessage.error('加载配置失败')
  }
}

const save = async () => {
  try {
    await axios.put('/api/notifications/config', form.value)
    ElMessage.success('配置已保存')
  } catch (error) {
    ElMessage.error('保存失败')
  }
}

onMounted(loadConfig)
</script>

<style scoped>
.notification-settings {
  padding: 20px;
}
.form-hint {
  margin-left: 12px;
  font-size: 12px;
  color: #909399;
}
</style>
