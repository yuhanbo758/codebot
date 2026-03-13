<template>
  <div class="skills-settings">
    <el-form label-width="140px">
      <el-form-item label="自定义技能目录">
        <div class="custom-dirs-section">
          <el-alert
            type="info"
            :closable="false"
            style="margin-bottom: 16px;"
            show-icon
          >
            <template #default>
              添加包含 SKILL.md 技能的外部文件夹路径。每个文件夹下的子目录若包含 SKILL.md 文件，将被自动识别为技能。
              支持添加多个不同路径，例如其他 CLI 工具的技能目录。
            </template>
          </el-alert>

          <div
            v-for="(dir, index) in customDirs"
            :key="index"
            class="dir-item"
          >
            <el-input
              v-model="customDirs[index]"
              placeholder="请输入技能文件夹的绝对路径，如 C:\Users\me\.agents\skills"
              clearable
              style="flex: 1"
            >
              <template #prefix>
                <el-icon><Folder /></el-icon>
              </template>
            </el-input>
            <el-button
              type="danger"
              :icon="Delete"
              circle
              @click="removeDir(index)"
            />
          </div>

          <el-button type="primary" plain @click="addDir" style="margin-top: 8px;">
            <el-icon><Plus /></el-icon>
            添加目录
          </el-button>
        </div>
      </el-form-item>

      <el-form-item>
        <el-button type="primary" @click="save" :loading="saving">保存配置</el-button>
      </el-form-item>
    </el-form>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { Folder, Delete, Plus } from '@element-plus/icons-vue'
import axios from 'axios'

const customDirs = ref([])
const saving = ref(false)

const loadConfig = async () => {
  try {
    const response = await axios.get('/api/config/skills')
    const data = response.data.data
    customDirs.value = data.custom_skill_dirs || []
  } catch (error) {
    ElMessage.error('加载技能目录配置失败')
  }
}

const addDir = () => {
  customDirs.value.push('')
}

const removeDir = (index) => {
  customDirs.value.splice(index, 1)
}

const save = async () => {
  // 过滤掉空白路径
  const dirs = customDirs.value
    .map(d => d.trim())
    .filter(d => d.length > 0)
  
  saving.value = true
  try {
    await axios.put('/api/config/skills', {
      custom_skill_dirs: dirs
    })
    customDirs.value = dirs
    ElMessage.success('技能目录配置已保存')
  } catch (error) {
    ElMessage.error('保存失败')
  } finally {
    saving.value = false
  }
}

onMounted(loadConfig)
</script>

<style scoped>
.skills-settings {
  padding: 20px;
}

.custom-dirs-section {
  width: 100%;
}

.dir-item {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}
</style>
