<template>
  <el-form label-width="150px" v-loading="loading">
    <el-form-item label="完全访问">
      <el-switch v-model="fullAccess" />
      <div class="access-tip">保存后从下一条消息生效：允许工具、文件、命令及联网权限，权限请求自动允许。普通问题仍需回答，计划模式保留原限制。</div>
    </el-form-item>
    <el-form-item><el-button type="primary" :loading="saving" @click="save">保存 OpenCode 设置</el-button></el-form-item>
  </el-form>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import axios from 'axios'
import { ElMessage } from 'element-plus'

const fullAccess = ref(false)
const loading = ref(false)
const saving = ref(false)
onMounted(async () => {
  loading.value = true
  try {
    const { data } = await axios.get('/api/config/opencode/access')
    fullAccess.value = Boolean(data.data.full_access)
  } catch {
    ElMessage.error('加载 OpenCode 设置失败')
  } finally {
    loading.value = false
  }
})
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
</style>
