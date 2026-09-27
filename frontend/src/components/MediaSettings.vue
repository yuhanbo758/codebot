<template>
  <div class="media-settings">
    <h2>媒体模型</h2>
    <p class="hint">看图能力由聊天模型目录和图片附件处理；图片生成、语音合成和语音识别使用各自的服务协议。三个服务可分别配置，Build、Plan、Agent 与 JevAI 共用。密钥按服务和协议分别保存，切换供应商不会沿用原密钥。腾讯 TokenHub 的语音合成选项调用 MiniMax Speech，并非混元原生 TTS。</p>
    <el-form label-width="105px">
      <template v-for="service in services" :key="service.kind">
        <el-divider content-position="left">{{ service.label }}</el-divider>
        <el-form-item label="协议">
          <el-select v-model="form[service.kind].protocol" style="width: 320px" @change="onProtocolChange(service.kind)">
            <el-option v-for="option in service.protocols" :key="option.value" :label="option.label" :value="option.value" />
          </el-select>
        </el-form-item>
        <el-form-item :label="service.kind === 'speech' && form.speech.protocol === 'volc_tts' ? 'Resource ID' : '模型 ID'">
          <el-input v-model="form[service.kind].model" :placeholder="modelHints[form[service.kind].protocol] || service.modelHint" />
          <div v-if="protocolDocs[form[service.kind].protocol]" class="hint">请填写支持该媒体能力的模型 ID；<a :href="protocolDocs[form[service.kind].protocol]" target="_blank" rel="noopener noreferrer">查看模型与接口文档</a>。</div>
        </el-form-item>
        <el-form-item label="接口地址">
          <el-input v-model="form[service.kind].base_url" :placeholder="service.urlHint" />
          <div class="hint">留空使用所选协议的默认地址；MiniMax 默认指向国际站，中国区可填 https://api.minimaxi.com/v1。千问生图和识别可填所属地域的业务空间专属 https://业务空间ID.cn-beijing.maas.aliyuncs.com/compatible-mode/v1。也可填完整接口地址。仅支持 HTTPS 与本机 HTTP。</div>
        </el-form-item>
        <el-form-item v-if="service.kind === 'speech'" label="音色">
          <el-input v-model="form.speech.voice" :placeholder="form.speech.protocol === 'gemini_tts' ? '如 Kore' : form.speech.protocol === 'openrouter_tts' ? '如 nova；以所选模型支持的音色为准' : 'OpenAI 如 alloy；豆包填写 speaker ID'" />
        </el-form-item>
        <el-form-item label="API Key">
          <div class="key-row">
            <el-input v-model="keyInputs[service.kind]" type="password" show-password autocomplete="off" :placeholder="credentialFor(service.kind) ? '当前协议已配置；留空不修改' : '输入当前协议的密钥'" />
            <el-button :disabled="!desktopAvailable || !keyInputs[service.kind]" @click="updateKey(service.kind, keyInputs[service.kind])">保存</el-button>
            <el-button :disabled="!desktopAvailable || !credentialFor(service.kind)" @click="updateKey(service.kind, '')">清除</el-button>
          </div>
          <div v-if="!desktopAvailable" class="hint">Web 部署使用 {{ envName(service.kind) }} 环境变量；旧协议仍兼容原有按服务命名的变量。</div>
        </el-form-item>
      </template>
      <el-form-item><el-button type="primary" :loading="saving" @click="save">保存媒体设置</el-button></el-form-item>
    </el-form>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import axios from 'axios'
import { ElMessage } from 'element-plus'

const services = [
  { kind: 'image', label: '图片生成', protocols: [{ value: 'openai', label: 'OpenAI Images 兼容' }, { value: 'ark', label: '火山方舟 Seedream' }, { value: 'minimax_image', label: 'MiniMax 图片生成' }, { value: 'qwen_image', label: '阿里云百炼 · 千问生图 3.0' }, { value: 'hunyuan_image', label: '腾讯 TokenHub · 混元生图 3.0' }, { value: 'openrouter_image', label: 'OpenRouter · 图片生成' }, { value: 'gemini_image', label: 'Google Gemini · 图片生成' }], modelHint: '如 qwen-image-3.0、hy-image-v3、gpt-image-2', urlHint: '留空使用所选协议的官方默认地址' },
  { kind: 'speech', label: '语音合成', protocols: [{ value: 'openai', label: 'OpenAI Speech 兼容' }, { value: 'volc_tts', label: '豆包语音 HTTP TTS' }, { value: 'xiaomi_tts', label: '小米 MiMo 语音合成' }, { value: 'minimax_tts', label: 'MiniMax 语音合成' }, { value: 'qwen_tts', label: '阿里云百炼 · 千问语音合成' }, { value: 'tencent_minimax_tts', label: '腾讯 TokenHub · MiniMax Speech' }, { value: 'openrouter_tts', label: 'OpenRouter · 语音合成' }, { value: 'gemini_tts', label: 'Google Gemini · 语音合成' }], modelHint: '如 qwen3-tts-flash、minimax-speech-2.8-hd', urlHint: '留空使用所选协议的官方默认地址' },
  { kind: 'transcription', label: '语音识别', protocols: [{ value: 'openai', label: 'OpenAI Transcriptions 兼容' }, { value: 'ark_chat_audio', label: '火山方舟音频理解' }, { value: 'xiaomi_asr', label: '小米 MiMo 语音识别' }, { value: 'minimax_asr', label: 'MiniMax 语音识别' }, { value: 'qwen_asr', label: '阿里云百炼 · 千问语音识别' }, { value: 'hunyuan_asr', label: '腾讯 TokenHub · 混元语音识别' }, { value: 'openrouter_asr', label: 'OpenRouter · 语音识别' }, { value: 'gemini_asr', label: 'Google Gemini · 音频理解转写' }], modelHint: '如 qwen3-asr-flash、hy-asr-3.0-preview', urlHint: '留空使用所选协议的官方默认地址' },
]
const modelHints = {
  openrouter_image: '如 bytedance-seed/seedream-4.5',
  openrouter_tts: '如 openai/gpt-4o-mini-tts-2025-12-15',
  openrouter_asr: '如 openai/whisper-1',
  gemini_image: '如 gemini-3.1-flash-image',
  gemini_tts: '如 gemini-3.8-flash-tts',
  gemini_asr: '如 gemini-3.8-flash',
}
const protocolDocs = {
  openrouter_image: 'https://openrouter.ai/docs/guides/overview/multimodal/image-generation',
  openrouter_tts: 'https://openrouter.ai/docs/guides/overview/multimodal/tts',
  openrouter_asr: 'https://openrouter.ai/blog/tutorials/transcription-on-openrouter/',
  gemini_image: 'https://ai.google.dev/gemini-api/docs/generate-content/image-generation',
  gemini_tts: 'https://ai.google.dev/gemini-api/docs/generate-content/speech-generation',
  gemini_asr: 'https://ai.google.dev/gemini-api/docs/generate-content/audio',
}
const blank = () => ({ protocol: 'openai', model: '', base_url: '', voice: '' })
const form = reactive({ image: blank(), speech: blank(), transcription: blank() })
const credentialProtocols = reactive({ image: {}, speech: {}, transcription: {} })
const keyInputs = reactive({ image: '', speech: '', transcription: '' })
const desktopAvailable = Boolean(window.electronAPI?.saveMediaKey)
const saving = ref(false)

async function load() {
  try {
    const { data } = await axios.get('/api/media/config')
    for (const service of services) Object.assign(form[service.kind], blank(), data.data?.[service.kind] || {})
    for (const service of services) Object.assign(credentialProtocols[service.kind], data.data?.credential_protocols?.[service.kind] || {})
  } catch (error) { ElMessage.error(error?.response?.data?.detail || '媒体设置加载失败') }
}

async function save() {
  saving.value = true
  try {
    await axios.patch('/api/media/config', { image: form.image, speech: form.speech, transcription: form.transcription })
    ElMessage.success('媒体设置已保存')
  } catch (error) { ElMessage.error(error?.response?.data?.detail || '媒体设置保存失败') }
  finally { saving.value = false }
}

async function updateKey(kind, value) {
  try {
    const protocol = form[kind].protocol
    await window.electronAPI.saveMediaKey(kind, protocol, value.trim())
    credentialProtocols[kind][protocol] = Boolean(value.trim())
    keyInputs[kind] = ''
    ElMessage.success('媒体密钥已更新')
  } catch (error) { ElMessage.error(error?.message || '媒体密钥保存失败') }
}

function credentialFor(kind) { return Boolean(credentialProtocols[kind]?.[form[kind].protocol]) }
function envName(kind) { return `CODEBOT_MEDIA_${kind.toUpperCase()}_${form[kind].protocol.toUpperCase()}_API_KEY` }
function onProtocolChange(kind) {
  form[kind].model = ''
  form[kind].base_url = ''
  if (kind === 'speech') form.speech.voice = ''
  keyInputs[kind] = ''
}
onMounted(load)
</script>

<style scoped>
.media-settings { max-width: 860px; }
.media-settings h2 { margin-top: 0; }
.hint { color: #909399; font-size: 13px; line-height: 1.5; width: 100%; }
.key-row { display: flex; gap: 8px; width: 100%; }
.key-row .el-input { max-width: 480px; }
@media (max-width: 640px) { .key-row { flex-wrap: wrap; } }
</style>
