<template>
  <div class="backup-settings">
    <el-card class="backup-card">
      <template #header>记忆导出</template>
      <el-button type="primary" @click="exportMemories">导出记忆</el-button>
      <div v-if="exportPath" class="export-path">导出文件：{{ exportPath }}</div>
    </el-card>

    <el-card class="backup-card">
      <template #header>记忆导入</template>
      <el-button @click="triggerImport">选择备份文件</el-button>
      <span v-if="importFileName" class="file-name">{{ importFileName }}</span>
      <el-button type="primary" :disabled="!importFile" @click="importMemories">开始导入</el-button>
      <input ref="fileInputRef" type="file" accept=".zip" class="file-input" @change="handleFileChange" />
    </el-card>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import axios from 'axios'
import { ElMessage } from 'element-plus'

const exportPath = ref('')
const importFile = ref(null)
const importFileName = ref('')
const fileInputRef = ref(null)

const exportMemories = async () => {
  try {
    const response = await axios.post('/api/memory/export')
    exportPath.value = response.data.data?.path || ''
    ElMessage.success('导出完成')
  } catch (error) {
    ElMessage.error('导出失败')
  }
}

const triggerImport = () => {
  if (fileInputRef.value) {
    fileInputRef.value.click()
  }
}

const handleFileChange = (e) => {
  const file = e.target.files?.[0]
  if (file) {
    importFile.value = file
    importFileName.value = file.name
  }
}

const importMemories = async () => {
  if (!importFile.value) return
  try {
    const formData = new FormData()
    formData.append('file', importFile.value)
    await axios.post('/api/memory/import', formData, {
      headers: {
        'Content-Type': 'multipart/form-data'
      }
    })
    ElMessage.success('导入完成')
  } catch (error) {
    ElMessage.error('导入失败')
  }
}
</script>

<style scoped>
.backup-settings {
  padding: 20px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.backup-card {
  width: 100%;
}

.export-path {
  margin-top: 12px;
  color: #606266;
}

.file-name {
  margin: 0 12px;
  color: #606266;
}

.file-input {
  display: none;
}
</style>
