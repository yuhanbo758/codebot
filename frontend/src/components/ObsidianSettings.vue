<template>
  <div class="obsidian-settings">
    <el-alert
      class="settings-alert"
      title="Obsidian 兼容说明"
      type="info"
      :closable="false"
      show-icon
      description="Codebot 会把 Obsidian 库和知识库路径当作原生 Markdown / wiki 结构直接处理，优先调用 obsidian-cli 与相关 Obsidian skill；不会为这些知识库生成向量库，避免内容丢失。"
    />
    <el-form :model="form" label-width="150px" v-loading="loading">
      <el-form-item label="启用 Obsidian">
        <el-switch v-model="form.enabled" />
      </el-form-item>
      <el-form-item label="obsidian-cli">
        <el-input v-model="form.cli_path" placeholder="obsidian-cli" clearable />
      </el-form-item>
      <el-form-item label="默认库路径">
        <el-input v-model="form.vault_path" placeholder="Obsidian vault 绝对路径" clearable />
      </el-form-item>

      <el-form-item label="知识库路径">
        <div class="kb-list">
          <div v-for="(kb, index) in form.knowledge_bases" :key="index" class="kb-card">
            <div class="kb-row">
              <el-input v-model="kb.name" placeholder="名称" />
              <el-switch v-model="kb.enabled" active-text="启用" />
              <el-button :icon="Delete" circle type="danger" @click="form.knowledge_bases.splice(index, 1)" />
            </div>
            <el-input v-model="kb.path" placeholder="Markdown/Obsidian 知识库绝对路径" clearable />
            <el-input v-model="kb.description" placeholder="描述，可被 # 搜索匹配" clearable />
          </div>
          <el-button plain type="primary" @click="addKnowledgeBase">
            <el-icon><Plus /></el-icon>
            添加知识库
          </el-button>
        </div>
      </el-form-item>

      <el-form-item>
        <el-button type="primary" :loading="saving" @click="save">保存 Obsidian 设置</el-button>
      </el-form-item>
    </el-form>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Delete, Plus } from '@element-plus/icons-vue'
import axios from 'axios'

const loading = ref(false)
const saving = ref(false)

const form = ref({
  enabled: true,
  cli_path: 'obsidian-cli',
  vault_path: '',
  knowledge_bases: []
})

const addKnowledgeBase = () => {
  form.value.knowledge_bases.push({
    id: `kb-${Date.now()}`,
    name: '',
    path: '',
    description: '',
    enabled: true
  })
}

const load = async () => {
  loading.value = true
  try {
    const res = await axios.get('/api/config/obsidian')
    form.value = { ...form.value, ...(res.data?.data || {}) }
    form.value.knowledge_bases = form.value.knowledge_bases || []
  } catch (error) {
    ElMessage.error('加载 Obsidian 设置失败')
  } finally {
    loading.value = false
  }
}

const save = async () => {
  saving.value = true
  try {
    const payload = {
      ...form.value,
      knowledge_bases: (form.value.knowledge_bases || [])
        .filter((kb) => (kb.path || '').trim())
        .map((kb, index) => ({
          id: kb.id || `kb-${index + 1}`,
          name: kb.name || '',
          path: kb.path.trim(),
          description: kb.description || '',
          enabled: kb.enabled !== false
        }))
    }
    const res = await axios.patch('/api/config/obsidian', payload)
    form.value = { ...form.value, ...(res.data?.data || {}) }
    ElMessage.success('Obsidian 设置已保存')
  } catch (error) {
    ElMessage.error(error?.response?.data?.detail || '保存 Obsidian 设置失败')
  } finally {
    saving.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.obsidian-settings {
  padding: 20px;
}

.settings-alert {
  margin-bottom: 16px;
}

.kb-list {
  width: 100%;
}

.kb-card {
  border: 1px solid #e4e7ed;
  border-radius: 6px;
  padding: 12px;
  margin-bottom: 10px;
  display: grid;
  gap: 8px;
}

.kb-row {
  display: grid;
  grid-template-columns: 1fr auto auto;
  gap: 8px;
  align-items: center;
}
</style>
