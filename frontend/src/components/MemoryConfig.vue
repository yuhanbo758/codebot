<template>
  <div class="memory-config">
    <el-form label-width="180px">

      <!-- ── 自动清理 ── -->
      <el-divider content-position="left">自动清理</el-divider>
      <el-form-item label="自动清理">
        <el-switch v-model="config.auto_cleanup_enabled" />
      </el-form-item>
      <template v-if="config.auto_cleanup_enabled">
        <el-form-item label="保留天数">
          <el-slider v-model="config.cleanup_days" :min="0" :max="365"
            :marks="{0: '永久', 30: '1 个月', 90: '3 个月', 180: '6 个月', 365: '1 年'}"
            show-input />
        </el-form-item>
        <el-form-item label="清理已归档记忆">
          <el-switch v-model="config.cleanup_archived_memories" />
          <span class="hint">开启后，自动清理时会同时删除超期的已归档长期记忆（活跃记忆不受影响）</span>
        </el-form-item>
      </template>

      <!-- ── 自动归档 ── -->
      <el-divider content-position="left">自动归档</el-divider>
      <el-form-item label="自动归档">
        <el-switch v-model="config.archive_enabled" />
      </el-form-item>
      <el-form-item label="归档天数" v-if="config.archive_enabled">
        <el-slider v-model="config.archive_days" :min="0" :max="180" show-input />
      </el-form-item>

      <!-- ── 搜索行为 ── -->
      <el-divider content-position="left">搜索行为</el-divider>
      <el-form-item label="归档记忆参与手动搜索">
        <el-switch v-model="config.show_archived_in_search" />
        <span class="hint">开启后，在“搜索记忆”标签页可检索到已归档记忆（AI 聊天自动调用不受此开关影响）</span>
      </el-form-item>

      <!-- ── 自动整理 ── -->
      <el-divider content-position="left">自动整理</el-divider>
      <el-form-item label="每日自动整理">
        <el-switch v-model="config.organize_enabled" />
        <span class="hint">开启后，每天在指定时间自动对记忆进行 AI 优化（合并重复、补全描述、修正矛盾）</span>
      </el-form-item>
      <template v-if="config.organize_enabled">
        <el-form-item label="整理聊天记录">
          <el-switch v-model="config.organize_chat_enabled" />
          <span class="hint">开启后，自动整理会额外扫描新增聊天记录，并联动补记忆/任务/技能沉淀</span>
        </el-form-item>
        <el-form-item label="整理时间">
          <el-time-picker
            v-model="organizeTimeDate"
            format="HH:mm"
            value-format="HH:mm"
            placeholder="选择每日整理时间"
            :clearable="false"
            style="width: 160px"
            @change="onOrganizeTimeChange"
          />
          <span class="hint">默认凌晨 03:00，建议选择空闲时段</span>
        </el-form-item>
        <el-form-item label="整理使用模型">
          <el-select
            v-model="config.organize_model"
            placeholder="使用当前聊天模型（默认）"
            clearable
            style="width: 320px"
            :loading="modelsLoading"
            @focus="loadModels"
          >
            <el-option label="使用当前聊天模型（默认）" value="" />
            <el-option
              v-for="m in availableModels"
              :key="m.id"
              :label="m.name"
              :value="m.id"
            />
          </el-select>
          <span class="hint">自动整理的聊天内容可能较多，可选择其他模型以规避主模型的 Token 消耗</span>
        </el-form-item>
        <el-form-item label="上次整理时间">
          <span class="last-run-text">
            {{ lastRunText }}
          </span>
          <el-button
            size="small"
            type="primary"
            plain
            style="margin-left: 12px"
            :loading="organizing"
            @click="triggerOrganize"
          >
            立即整理
          </el-button>
        </el-form-item>
      </template>

      <!-- ── 保存 ── -->
      <el-form-item>
        <el-button type="primary" @click="saveConfig">保存配置</el-button>
      </el-form-item>
    </el-form>

    <!-- 整理结果弹窗 -->
    <el-dialog v-model="showOrganizeResult" title="整理已启动" width="400px">
      <p>记忆整理任务已在后台运行。</p>
      <p class="hint">整理完成后，活跃记忆列表将自动更新。你可以在"活跃记忆"页面查看最新结果。</p>
      <template #footer>
        <el-button type="primary" @click="showOrganizeResult = false">知道了</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import axios from 'axios'

const config = ref({
  auto_cleanup_enabled: false,
  cleanup_days: 180,
  cleanup_archived_memories: true,
  archive_enabled: true,
  archive_days: 90,
  vector_search_top_k: 5,
  similarity_threshold: 0.7,
  show_archived_in_search: true,
  organize_enabled: false,
  organize_chat_enabled: true,
  organize_time: '03:00',
  organize_last_run: null,
  organize_model: '',
})

// el-time-picker 需要 Date 对象或字符串 "HH:mm"
const organizeTimeDate = ref('03:00')

const organizing = ref(false)
const showOrganizeResult = ref(false)

// 可用模型列表
const availableModels = ref([])
const modelsLoading = ref(false)

const lastRunText = computed(() => {
  const t = config.value.organize_last_run
  if (!t) return '从未运行'
  try {
    return new Date(t).toLocaleString('zh-CN')
  } catch {
    return t
  }
})

const onOrganizeTimeChange = (val) => {
  config.value.organize_time = val || '03:00'
}

const loadModels = async () => {
  if (availableModels.value.length > 0) return
  modelsLoading.value = true
  try {
    const resp = await axios.get('/api/chat/models')
    if (resp.data?.success) {
      availableModels.value = resp.data.data?.models || []
    }
  } catch (e) {
    console.warn('加载模型列表失败:', e)
  } finally {
    modelsLoading.value = false
  }
}

const loadConfig = async () => {
  try {
    const response = await axios.get('/api/memory/config')
    config.value = { ...config.value, ...response.data.data }
    organizeTimeDate.value = config.value.organize_time || '03:00'
    // 确保 organize_model 为字符串（null → ''）
    if (!config.value.organize_model) config.value.organize_model = ''
  } catch (error) {
    console.error('加载配置失败')
  }
}

const saveConfig = async () => {
  try {
    config.value.organize_time = organizeTimeDate.value || '03:00'
    // 空字符串转为 null，后端以 null 表示使用默认模型
    const payload = {
      ...config.value,
      organize_model: config.value.organize_model || null,
    }
    await axios.put('/api/memory/config', payload)
    ElMessage.success('配置已保存')
  } catch (error) {
    ElMessage.error('保存失败')
  }
}

const triggerOrganize = async () => {
  organizing.value = true
  try {
    const resp = await axios.post('/api/memory/organize')
    if (resp.data.success) {
      showOrganizeResult.value = true
    } else {
      ElMessage.warning(resp.data.message || '整理任务启动失败')
    }
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || '触发整理失败')
  } finally {
    organizing.value = false
  }
}

onMounted(() => {
  loadConfig()
  loadModels()
})
</script>

<style scoped>
.hint {
  margin-left: 10px;
  color: #909399;
  font-size: 12px;
}
.last-run-text {
  color: #606266;
  font-size: 13px;
}
</style>
