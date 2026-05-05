<template>
  <div class="share-page">
    <div class="share-card">
      <div class="share-header">
        <div>
          <div class="share-label">Codebot 只读分享</div>
          <h1>{{ conversation?.title || '对话分享' }}</h1>
        </div>
        <el-tag type="info">{{ messages.length }} 条消息</el-tag>
      </div>

      <el-alert
        v-if="error"
        :title="error"
        type="error"
        show-icon
        :closable="false"
      />

      <el-skeleton v-else-if="loading" :rows="8" animated />

      <div v-else class="message-list">
        <div
          v-for="msg in messages"
          :key="msg.id"
          class="message"
          :class="msg.role"
        >
          <div class="role">{{ roleLabel(msg.role) }}</div>
          <div class="content markdown-body" v-html="renderMarkdown(msg.content)"></div>
          <div class="time">{{ formatDate(msg.created_at) }}</div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import MarkdownIt from 'markdown-it'
import axios from 'axios'

const route = useRoute()
const loading = ref(true)
const error = ref('')
const conversation = ref(null)
const messages = ref([])

const md = new MarkdownIt({ html: false, linkify: true, typographer: true })

const renderMarkdown = (content) => md.render(content || '')

const roleLabel = (role) => {
  if (role === 'user') return '用户'
  if (role === 'assistant') return '助手'
  return role || '消息'
}

const formatDate = (dateStr) => {
  if (!dateStr) return ''
  const normalized = String(dateStr).replace(' ', 'T')
  const date = new Date(normalized.endsWith('Z') ? normalized : `${normalized}Z`)
  if (Number.isNaN(date.getTime())) return dateStr
  return date.toLocaleString('zh-CN')
}

onMounted(async () => {
  try {
    const res = await axios.get(`/api/chat/share/${route.params.shareId}`)
    conversation.value = res.data?.data?.conversation || null
    messages.value = res.data?.data?.messages || []
  } catch (err) {
    error.value = err?.response?.data?.detail || '分享不存在或已失效'
  } finally {
    loading.value = false
  }
})
</script>

<style scoped>
.share-page {
  min-height: calc(100vh - 60px);
  background: linear-gradient(135deg, #eef4ff 0%, #f8fafc 55%, #fff7ed 100%);
  padding: 32px 16px;
  box-sizing: border-box;
}

.share-card {
  max-width: 920px;
  margin: 0 auto;
  background: rgba(255, 255, 255, 0.94);
  border: 1px solid #e5e7eb;
  border-radius: 18px;
  padding: 28px;
  box-shadow: 0 18px 50px rgba(15, 23, 42, 0.10);
}

.share-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 16px;
  margin-bottom: 24px;
}

.share-label {
  color: #64748b;
  font-size: 13px;
  letter-spacing: 0.08em;
}

h1 {
  margin: 6px 0 0;
  font-size: 26px;
  color: #0f172a;
}

.message-list {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.message {
  border-radius: 14px;
  padding: 16px;
  border: 1px solid #e5e7eb;
  background: #fff;
}

.message.user {
  background: #eff6ff;
  border-color: #bfdbfe;
}

.message.assistant {
  background: #f8fafc;
}

.role {
  font-weight: 700;
  color: #334155;
  margin-bottom: 8px;
}

.content {
  color: #1f2937;
  line-height: 1.7;
  word-break: break-word;
}

.time {
  margin-top: 10px;
  font-size: 12px;
  color: #94a3b8;
}

@media (max-width: 640px) {
  .share-card {
    padding: 18px;
  }

  .share-header {
    flex-direction: column;
  }

  h1 {
    font-size: 22px;
  }
}
</style>
