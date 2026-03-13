<template>
  <div class="backup-restore">
    <el-row :gutter="24">
      <!-- Export -->
      <el-col :span="12">
        <el-card shadow="never">
          <template #header>
            <span>导出备份</span>
          </template>
          <p class="card-desc">将当前所有记忆（长期记忆 + 对话历史 + 向量索引）打包为 ZIP 文件保存到服务器的 <code>data/backups/</code> 目录。</p>
          <el-button
            type="primary"
            :loading="exporting"
            @click="handleExport"
          >
            立即导出
          </el-button>
          <div v-if="exportResult" class="result-box">
            <el-alert
              :title="exportResult.message"
              :description="exportResult.path ? `保存路径：${exportResult.path}` : ''"
              :type="exportResult.success ? 'success' : 'error'"
              show-icon
              :closable="false"
            />
          </div>
        </el-card>
      </el-col>

      <!-- Import -->
      <el-col :span="12">
        <el-card shadow="never">
          <template #header>
            <span>导入备份</span>
          </template>
          <p class="card-desc">上传之前导出的 ZIP 备份文件。导入前会自动备份当前数据，然后覆盖恢复。</p>
          <el-upload
            ref="uploadRef"
            action="#"
            accept=".zip"
            :auto-upload="false"
            :limit="1"
            :on-change="handleFileChange"
            :on-exceed="handleExceed"
            :file-list="fileList"
          >
            <el-button>选择备份文件</el-button>
            <template #tip>
              <div class="el-upload__tip">仅支持 .zip 格式</div>
            </template>
          </el-upload>

          <el-button
            type="warning"
            :loading="importing"
            :disabled="!selectedFile"
            style="margin-top: 12px"
            @click="handleImport"
          >
            导入并恢复
          </el-button>

          <div v-if="importResult" class="result-box">
            <el-alert
              :title="importResult.message"
              :type="importResult.success ? 'success' : 'error'"
              show-icon
              :closable="false"
            />
          </div>
        </el-card>
      </el-col>
    </el-row>

    <!-- 说明 -->
    <el-card shadow="never" style="margin-top: 16px">
      <template #header><span>注意事项</span></template>
      <ul class="notes">
        <li>导出文件包含：SQLite 数据库（对话 + 长期记忆）和 ChromaDB 向量索引。</li>
        <li>导入时会先自动创建当前数据的备份，再执行覆盖恢复，不会丢失原数据。</li>
        <li>导入后需要<strong>重启后端服务</strong>，以确保新数据正确加载。</li>
        <li>备份文件存放于服务端 <code>data/backups/</code> 目录，可手动复制到其他位置。</li>
      </ul>
    </el-card>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import axios from 'axios'

const exporting = ref(false)
const importing = ref(false)
const exportResult = ref(null)
const importResult = ref(null)
const selectedFile = ref(null)
const fileList = ref([])
const uploadRef = ref(null)

const handleExport = async () => {
  exporting.value = true
  exportResult.value = null
  try {
    const response = await axios.post('/api/memory/export')
    const data = response.data
    exportResult.value = {
      success: data.success,
      message: data.message || '导出成功',
      path: data.data?.path || ''
    }
    if (data.success) {
      ElMessage.success('备份导出成功')
    }
  } catch (error) {
    const msg = error.response?.data?.detail || error.message || '导出失败'
    exportResult.value = { success: false, message: msg, path: '' }
    ElMessage.error(msg)
  } finally {
    exporting.value = false
  }
}

const handleFileChange = (file) => {
  selectedFile.value = file.raw
}

const handleExceed = () => {
  ElMessage.warning('每次只能选择一个备份文件')
}

const handleImport = async () => {
  if (!selectedFile.value) return

  try {
    await ElMessageBox.confirm(
      '导入将覆盖当前所有记忆数据（系统会先自动备份当前数据）。确认继续？',
      '确认导入',
      { type: 'warning', confirmButtonText: '确认导入', cancelButtonText: '取消' }
    )
  } catch {
    return
  }

  importing.value = true
  importResult.value = null
  try {
    const formData = new FormData()
    formData.append('file', selectedFile.value)

    const response = await axios.post('/api/memory/import', formData, {
      headers: { 'Content-Type': 'multipart/form-data' }
    })
    const data = response.data
    importResult.value = {
      success: data.success,
      message: data.message || '导入成功，请重启后端服务'
    }
    if (data.success) {
      ElMessage.success('备份导入成功，请重启后端服务')
      fileList.value = []
      selectedFile.value = null
    }
  } catch (error) {
    const msg = error.response?.data?.detail || error.message || '导入失败'
    importResult.value = { success: false, message: msg }
    ElMessage.error(msg)
  } finally {
    importing.value = false
  }
}
</script>

<style scoped>
.backup-restore {
  padding: 20px;
}
.card-desc {
  color: #606266;
  font-size: 13px;
  margin-bottom: 16px;
  line-height: 1.6;
}
.result-box {
  margin-top: 12px;
}
.notes {
  color: #606266;
  font-size: 13px;
  padding-left: 20px;
  line-height: 1.8;
}
code {
  background: #f0f2f5;
  padding: 1px 4px;
  border-radius: 3px;
  font-size: 12px;
}
</style>
