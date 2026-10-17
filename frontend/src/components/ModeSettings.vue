<template>
  <div class="mode-settings">
    <h2>Agent 与回复</h2>
    <p class="hint">适用于 OpenCode 与 Codex 的聊天。设置保存后从下一轮消息生效，Rakazo 继续使用独立执行链。</p>
    <el-form label-width="115px" class="mode-form" v-loading="agentLoading">
      <el-form-item label="Aegis 编程方法">
        <div>
          <el-switch v-model="agentForm.aegis_enabled" :disabled="!agentLoaded" />
          <div class="hint">Agent 模式按需使用项目现状核对、根因诊断、代码审查和完成验证规则。简单问答保持轻量，不增加额外模型调用，也不改变权限设置。</div>
        </div>
      </el-form-item>
      <el-form-item label="回复详略">
        <div class="media-picker">
          <el-select v-model="agentForm.reply_style" :disabled="!agentLoaded" class="model-picker">
            <el-option label="简洁：保留结论、关键依据与验证" value="concise" />
            <el-option label="均衡：按问题复杂度展开（默认）" value="balanced" />
            <el-option label="详细：解释取舍与复现步骤" value="detailed" />
            <el-option label="原有表达：不追加回复风格规则" value="off" />
          </el-select>
          <div class="hint">适用于各聊天模式，用户当轮指定的格式与详略优先。直接约束生成表达，不裁剪或二次改写回复。</div>
        </div>
      </el-form-item>
      <el-form-item class="agent-actions">
        <el-button type="primary" :disabled="!agentLoaded" :loading="agentSaving" @click="saveAgent">保存 Agent 与回复设置</el-button>
        <el-button :loading="agentLoading" @click="loadAgent">重新加载</el-button>
      </el-form-item>
    </el-form>
    <el-divider />
    <h2>JevAI 模式</h2>
    <p class="hint">Jev 负责路由与分类；生成内容和文件操作继续由当前 OpenCode 或 Codex 对话执行。模型只会在任务阶段之间切换。</p>
    <el-form label-width="115px" class="mode-form">
      <el-form-item label="Jev 供应商">
        <el-radio-group v-model="form.provider">
          <el-radio value="openrouter">OpenRouter</el-radio>
          <el-radio value="typesafe">TypeSafe 官方</el-radio>
        </el-radio-group>
      </el-form-item>
      <el-form-item label="API Key">
        <div class="key-row">
          <el-input v-model="keyInput" type="password" show-password autocomplete="off" :placeholder="credentials[form.provider] ? '已安全保存；留空则不更改' : '输入所选供应商的 API Key'" />
          <el-button :disabled="!keyInput || !desktopAvailable" :loading="keySaving" @click="saveKey">保存密钥</el-button>
          <el-button :disabled="!credentials[form.provider] || !desktopAvailable" @click="clearKey">清除</el-button>
        </div>
        <div class="hint">{{ desktopAvailable ? '密钥由桌面安全存储加密，设置接口不会回显。' : '独立 Web 部署请设置 CODEBOT_JEV_OPENROUTER_API_KEY 或 CODEBOT_JEV_TYPESAFE_API_KEY 环境变量。' }}</div>
      </el-form-item>
      <template v-for="executor in executors" :key="executor.id">
        <el-divider content-position="left">{{ executor.label }} 模型路由</el-divider>
        <div class="hint">从此执行器当前可运行的模型中指定轻、中、强三档 LLM；可另选图片理解模型。图片和语音生成服务在“媒体”中配置。</div>
        <el-form-item v-for="tier in tiers" :key="`${executor.id}-${tier.id}`" :label="tier.label">
          <el-select v-model="form[executor.id][tier.id]" filterable clearable class="model-picker" :loading="loading" placeholder="请选择模型">
            <el-option v-for="model in catalogs[executor.id]" :key="model.id" :value="model.id" :label="model.name" :disabled="!model.runnable" />
          </el-select>
        </el-form-item>
        <el-form-item label="图片理解模型">
          <div class="media-picker">
            <el-select v-model="form[executor.id].multimodal" filterable clearable class="model-picker" :loading="loading" placeholder="选择可接收图片附件的模型（可留空）">
              <el-option v-for="model in catalogs[executor.id]" :key="model.id" :value="model.id" :label="model.mediaLabel" :disabled="!model.runnable" />
            </el-select>
            <div class="hint">仅用于 JevAI 的图片附件理解，不处理视频附件。目录没有图片能力标记时仍可选择并自行实测；生成图片或语音请配置“媒体”。</div>
          </div>
        </el-form-item>
      </template>
      <el-form-item>
        <el-button type="primary" :loading="saving" @click="save">保存模式设置</el-button>
        <el-button :loading="loading" @click="load">刷新模型目录</el-button>
      </el-form-item>
    </el-form>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import axios from 'axios'
import { ElMessage } from 'element-plus'

const executors = [{ id: 'opencode', label: 'OpenCode' }, { id: 'codex', label: 'Codex' }]
const tiers = [{ id: 'fast', label: '轻量模型' }, { id: 'balanced', label: '均衡模型' }, { id: 'strong', label: '强模型' }]
const emptyTiers = () => ({ fast: '', balanced: '', strong: '', multimodal: '' })
const form = reactive({ provider: 'openrouter', opencode: emptyTiers(), codex: emptyTiers() })
const credentials = reactive({ openrouter: false, typesafe: false })
const catalogs = reactive({ opencode: [], codex: [] })
const desktopAvailable = Boolean(window.electronAPI?.saveJevAIKey)
const loading = ref(false)
const saving = ref(false)
const keySaving = ref(false)
const keyInput = ref('')
const agentForm = reactive({ aegis_enabled: true, reply_style: 'balanced' })
const agentLoading = ref(false)
const agentLoaded = ref(false)
const agentSaving = ref(false)

async function loadAgent() {
  agentLoading.value = true
  try {
    const response = await axios.get('/api/config/agent')
    Object.assign(agentForm, response.data.data)
    agentLoaded.value = true
  } catch (error) {
    ElMessage.error(error?.response?.data?.detail || '加载 Agent 与回复设置失败')
  } finally {
    agentLoading.value = false
  }
}

async function saveAgent() {
  agentSaving.value = true
  try {
    const response = await axios.patch('/api/config/agent', { ...agentForm })
    Object.assign(agentForm, response.data.data)
    ElMessage.success('Agent 与回复设置已保存，下一轮消息生效')
  } catch (error) {
    ElMessage.error(error?.response?.data?.detail || '保存 Agent 与回复设置失败')
  } finally {
    agentSaving.value = false
  }
}

async function load() {
  loading.value = true
  try {
    const settings = await axios.get('/api/config/jevai')
    const data = settings.data?.data || {}
    form.provider = data.provider || 'openrouter'
    for (const executor of executors) Object.assign(form[executor.id], emptyTiers(), data[executor.id] || {})
    Object.assign(credentials, data.credentials || {})
    const results = await Promise.allSettled([axios.get('/api/chat/models'), axios.get('/api/codex/models')])
    for (const [index, executor] of executors.entries()) {
      if (results[index].status !== 'fulfilled') {
        catalogs[executor.id] = []
        continue
      }
      const response = results[index].value
      const value = response.data?.data
      const models = Array.isArray(value) ? value : value?.models || []
      catalogs[executor.id] = models.map((model) => {
        const name = model.displayName || model.name || model.id
        const modalities = model.modalities || model.capabilities || {}
        const imageInput = Array.isArray(modalities.input) && modalities.input.includes('image')
        return {
          id: model.id, name,
          runnable: model.runnable !== false,
          mediaLabel: `${name} · ${imageInput ? '目录标注可输入图片' : '图片输入能力未确认'}`,
        }
      }).filter((model) => model.id)
    }
    if (results.some((result) => result.status === 'rejected')) ElMessage.warning('部分执行器的模型目录暂不可用，请检查连接后刷新')
  } catch (error) {
    ElMessage.error(error?.response?.data?.detail || '加载模式设置或模型目录失败')
  } finally {
    loading.value = false
  }
}

async function save() {
  for (const executor of executors) {
    const values = tiers.map((tier) => form[executor.id][tier.id]).filter(Boolean)
    if (new Set(values).size !== values.length) {
      ElMessage.warning(`${executor.label} 的轻、中、强模型不能重复`)
      return
    }
  }
  saving.value = true
  try {
    await axios.patch('/api/config/jevai', {
      provider: form.provider, opencode: form.opencode, codex: form.codex,
    })
    ElMessage.success('模式设置已保存')
  } catch (error) {
    ElMessage.error(error?.response?.data?.detail || '保存模式设置失败')
  } finally {
    saving.value = false
  }
}

async function updateKey(key) {
  keySaving.value = true
  try {
    await window.electronAPI.saveJevAIKey(form.provider, key)
    credentials[form.provider] = Boolean(key)
    keyInput.value = ''
    ElMessage.success(key ? '供应商密钥已保存' : '供应商密钥已清除')
  } catch (error) {
    ElMessage.error(error?.message || '保存密钥失败')
  } finally {
    keySaving.value = false
  }
}
const saveKey = () => updateKey(keyInput.value.trim())
const clearKey = () => updateKey('')
onMounted(() => { loadAgent(); load() })
</script>

<style scoped>
.mode-settings { max-width: 820px; }
.mode-settings h2 { margin-top: 0; }
.hint { color: #909399; font-size: 13px; line-height: 1.6; margin: 0 0 10px; overflow-wrap: anywhere; }
.agent-actions :deep(.el-form-item__content) { gap: 8px; }
.agent-actions :deep(.el-button + .el-button) { margin-left: 0; }
.key-row { display: flex; gap: 8px; width: 100%; }
.key-row .el-input { max-width: 430px; }
.model-picker { width: min(100%, 520px); }
.media-picker { width: 100%; }
@media (max-width: 640px) { .key-row { flex-wrap: wrap; } }
</style>
