<template>
  <div class="integration-settings">
    <el-form :model="form" label-width="160px" v-loading="loading">
      <el-divider content-position="left">ModelScope MCP</el-divider>

      <el-form-item label="ModelScope API Key">
        <el-input
          v-model="form.modelscope_api_key"
          type="password"
          show-password
          placeholder="请输入 ModelScope API Key（从 modelscope.cn 获取）"
          clearable
          style="max-width: 480px"
        />
        <div class="form-hint">
          用于访问 <a href="https://www.modelscope.cn/mcp" target="_blank">ModelScope MCP Hub</a>
          上托管的所有 MCP 工具，无需本地安装。
        </div>
      </el-form-item>

      <el-form-item>
        <el-button type="primary" @click="save" :loading="saving">保存设置</el-button>
        <el-button @click="load" :loading="loading">重置</el-button>
      </el-form-item>
    </el-form>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import axios from 'axios'

const loading = ref(false)
const saving = ref(false)

const form = ref({
  modelscope_api_key: '',
})

const load = async () => {
  loading.value = true
  try {
    const res = await axios.get('/api/config/integration')
    const data = res.data?.data || {}
    form.value.modelscope_api_key = data.modelscope_api_key || ''
  } catch {
    ElMessage.error('加载集成配置失败')
  } finally {
    loading.value = false
  }
}

const save = async () => {
  saving.value = true
  try {
    await axios.patch('/api/config/integration', {
      modelscope_api_key: form.value.modelscope_api_key,
    })
    ElMessage.success('集成配置已保存')
  } catch {
    ElMessage.error('保存失败')
  } finally {
    saving.value = false
  }
}

onMounted(() => {
  load()
})
</script>

<style scoped>
.integration-settings {
  padding: 20px;
}
.form-hint {
  font-size: 12px;
  color: #909399;
  margin-top: 4px;
}
.form-hint a {
  color: #409eff;
  text-decoration: none;
}
.form-hint a:hover {
  text-decoration: underline;
}
</style>
