<template>
  <div class="chat-container">
    <el-container>
      <!-- 侧边栏 - 对话列表 -->
      <el-aside width="280px">
        <div class="conversation-header">
          <el-button type="primary" @click="createNewConversation" class="new-conv-btn">
            <el-icon><Plus /></el-icon>
            新建对话
          </el-button>
          <el-button 
            :type="batchMode ? 'warning' : 'default'" 
            @click="toggleBatchMode"
            class="batch-btn"
          >
            <el-icon><Grid /></el-icon>
            {{ batchMode ? '退出' : '批量处理' }}
          </el-button>
        </div>

        <!-- 批量操作工具栏 -->
        <div v-if="batchMode" class="batch-toolbar">
          <el-checkbox 
            v-model="selectAll" 
            :indeterminate="isIndeterminate"
            @change="handleSelectAll"
          >全选 ({{ selectedConvIds.length }}/{{ conversations.length }})</el-checkbox>
          <div class="batch-actions">
            <el-button 
              size="small" 
              type="danger" 
              :disabled="selectedConvIds.length === 0"
              @click="batchDeleteConversations"
            >
              <el-icon><Delete /></el-icon>
              删除所选 ({{ selectedConvIds.length }})
            </el-button>
          </div>
        </div>
        
        <div class="conversation-list">
          <div
            v-for="conv in conversations"
            :key="conv.id"
            class="conversation-item"
            :class="{ active: currentConversationId === conv.id, 'batch-selected': selectedConvIds.includes(conv.id) }"
            @click="batchMode ? toggleConvSelection(conv.id) : selectConversation(conv.id)"
          >
            <div v-if="batchMode" class="batch-checkbox" @click.stop>
              <el-checkbox 
                :model-value="selectedConvIds.includes(conv.id)"
                @change="toggleConvSelection(conv.id)"
              />
            </div>
            <div class="conversation-info">
              <div class="conversation-title">
                <span class="conversation-title-text">{{ conv.title }}</span>
                <el-tag v-if="conv.is_pinned" size="small" type="info">置顶</el-tag>
                <el-tag v-if="conv.is_group" size="small" type="success">群聊</el-tag>
              </div>
              <div class="conversation-time">{{ formatDate(conv.updated_at) }}</div>
            </div>
            <div v-if="!batchMode" class="conversation-actions">
              <el-dropdown @command="(command) => handleConversationCommand(conv, command)" trigger="click">
                <el-button text size="small">•••</el-button>
                <template #dropdown>
                  <el-dropdown-menu>
                    <el-dropdown-item command="share">分享</el-dropdown-item>
                    <el-dropdown-item command="group">开始群聊</el-dropdown-item>
                    <el-dropdown-item command="rename">重命名</el-dropdown-item>
                    <el-dropdown-item :command="conv.is_pinned ? 'unpin' : 'pin'">
                      {{ conv.is_pinned ? '取消置顶' : '置顶聊天' }}
                    </el-dropdown-item>
                    <el-dropdown-item command="archive">归档</el-dropdown-item>
                    <el-dropdown-item command="delete" divided>删除</el-dropdown-item>
                  </el-dropdown-menu>
                </template>
              </el-dropdown>
            </div>
          </div>
          
          <el-empty v-if="conversations.length === 0" description="暂无对话" />
        </div>
      </el-aside>
      
      <!-- 主聊天区域 -->
      <el-main>
        <div class="chat-main" v-if="currentConversationId">
          <div v-if="thirdPartyStatus" class="third-party-banner">
            <el-tag size="small" :type="thirdPartyStatus.opencode_connected ? 'success' : 'info'">
              {{ thirdPartyStatus.opencode_connected ? 'OpenCode 已连接' : 'OpenCode 未连接' }}
            </el-tag>
            <el-tag size="small" :type="thirdPartyStatus.bridge_status?.registered ? 'success' : 'warning'">
              {{ thirdPartyStatus.bridge_status?.registered ? 'Bridge 已注册' : 'Bridge 待注册' }}
            </el-tag>
            <el-tag size="small" type="info">
              代理 {{ thirdPartyStatus.proxied_server_count || 0 }} 个 MCP
            </el-tag>
          </div>
          <!-- 消息列表 -->
          <div class="message-list" ref="messageListRef" @scroll.passive="onMessageListScroll">
            <template v-for="msg in messages" :key="msg.id">
              <!-- 工具步骤事件：独立气泡展示，不显示头像 -->
              <div v-if="msg.role === 'event'" class="message event-message">
                <div class="tool-event-item">
                  <span class="tool-event-type">{{ toolEventLabel(msg.event) }}</span>
                  <span class="tool-event-summary">{{ toolEventSummary(msg.event) }}</span>
                </div>
              </div>

              <!-- 普通用户/助手消息气泡 -->
              <div v-else class="message" :class="msg.role">
                <div class="message-avatar">
                  <el-avatar v-if="msg.role === 'user'" icon="User" />
                  <el-avatar v-else :src="assistantAvatarUrl" />
                </div>
                <div class="message-content">
                  <div v-if="msg.streaming" class="message-text streaming-text">{{ msg.content }}</div>
                  <div v-else class="message-text markdown-body" v-html="renderMarkdown(msg.content)"></div>
                  <div class="message-footer">
                    <div class="message-time">{{ formatDate(msg.created_at) }}</div>
                    <div class="message-actions">
                      <el-button 
                        class="copy-btn" 
                        link 
                        size="small" 
                        @click="copyMessage(msg.content)"
                        title="复制内容"
                      >
                        <el-icon><CopyDocument /></el-icon>
                      </el-button>
                      <el-button
                        class="undo-btn"
                        link
                        size="small"
                        @click="undoFromMessage(msg)"
                        title="撤销此消息及之后的所有消息"
                      >
                        <el-icon><Delete /></el-icon>
                      </el-button>
                    </div>
                  </div>
                </div>
              </div>
            </template>
            
            <div v-if="currentLoading" class="message assistant">
              <div class="message-avatar">
                <el-avatar :src="assistantAvatarUrl" />
              </div>
              <div class="message-content">
                <el-skeleton :rows="3" animated />
              </div>
            </div>
          </div>
          
          <!-- 记忆提示区 -->
          <div v-if="memoryHints.length > 0" class="memory-hints">
            <span class="memory-hints-label">
              <el-icon style="vertical-align: middle; margin-right: 4px;"><MagicStick /></el-icon>相关记忆
            </span>
            <div class="memory-hints-list">
              <el-tag
                v-for="(hint, idx) in memoryHints"
                :key="idx"
                size="small"
                :type="hintTagType(hint.category)"
                class="memory-hint-tag"
                :title="`[${hint.category_label}] ${hint.content}`"
              >
                <span class="hint-cat">{{ hint.category_label }}</span>{{ hint.content }}
              </el-tag>
            </div>
          </div>

          <!-- 输入区域 -->
          <div
            class="message-input"
            :class="{ 'drag-over': isDragOver }"
            @dragenter.prevent="onDragEnter"
            @dragover.prevent="onDragOver"
            @dragleave.prevent="onDragLeave"
            @drop.prevent="onDrop"
          >
            <!-- 拖拽遮罩 -->
            <div v-if="isDragOver" class="drag-overlay">
              <el-icon class="drag-icon"><Upload /></el-icon>
              <span>释放以上传文件</span>
            </div>

            <!-- 工具栏：agent模式 + 模型选择 -->
            <div class="input-toolbar">
              <div class="toolbar-left">
                <span class="toolbar-label">模式</span>
                <el-select
                  v-model="agentMode"
                  size="small"
                  class="agent-mode-select"
                  popper-class="agent-mode-popper"
                  :placeholder="''"
                >
                  <el-option value="build" label="Build">
                    <span class="agent-option-name">Build</span>
                    <span class="agent-option-desc">构建模式，直接实现代码</span>
                  </el-option>
                  <el-option value="plan" label="Plan">
                    <span class="agent-option-name">Plan</span>
                    <span class="agent-option-desc">规划模式，拆解步骤后输出计划</span>
                  </el-option>
                </el-select>
                <el-divider direction="vertical" />
                <span class="toolbar-label">模型</span>
                <el-select
                  v-model="selectedModel"
                  placeholder="默认模型"
                  size="small"
                  clearable
                  filterable
                  :filter-method="filterModels"
                  class="model-select"
                  :loading="modelsLoading"
                  no-data-text="暂无模型（OpenCode 未连接）"
                  popper-class="model-select-popper"
                  @visible-change="onModelDropdownOpen"
                >
                  <template v-if="modelSearchQuery">
                    <el-option
                      v-for="m in filteredModels"
                      :key="m.id"
                      :label="m.name"
                      :value="m.id"
                    >
                      <span class="model-option-name">{{ m.model }}</span>
                      <span class="model-option-provider">{{ m.provider }}</span>
                    </el-option>
                  </template>
                  <template v-else>
                    <el-option-group
                      v-for="group in groupedModels"
                      :key="group.provider"
                      :label="group.providerLabel"
                    >
                      <el-option
                        v-for="m in group.models"
                        :key="m.id"
                        :label="m.name"
                        :value="m.id"
                      >
                        <span class="model-option-name">{{ m.model }}</span>
                        <span class="model-option-provider">{{ m.provider }}</span>
                      </el-option>
                    </el-option-group>
                  </template>
                </el-select>
                <el-button
                  size="small"
                  text
                  :loading="modelsLoading"
                  @click="loadModels"
                  title="刷新模型列表"
                  class="refresh-btn"
                >
                  <el-icon><Refresh /></el-icon>
                </el-button>
              </div>
            </div>

            <!-- 附件预览区 -->
            <div v-if="attachedFiles.length > 0" class="attached-files">
              <div
                v-for="(f, idx) in attachedFiles"
                :key="idx"
                class="attached-file-chip"
                :title="f.name"
              >
                <el-icon class="file-chip-icon">
                  <component :is="fileIcon(f.name)" />
                </el-icon>
                <span class="file-chip-name">{{ f.name }}</span>
                <el-icon class="file-chip-remove" @click="removeAttachedFile(idx)"><Close /></el-icon>
              </div>
            </div>

            <!-- 命令面板 (/) -->
            <div v-if="showCommandPanel" class="command-panel" ref="commandPanelRef">
              <div class="command-panel-header">
                <span>命令</span>
                <span class="command-search-hint">{{ commandQuery || '输入以过滤' }}</span>
              </div>
              <div class="command-panel-list">
                <div
                  v-for="(cmd, idx) in filteredCommands"
                  :key="cmd.name"
                  class="command-item"
                  :class="{ active: idx === commandActiveIndex }"
                  @mousedown.prevent="selectCommand(cmd)"
                >
                  <span class="command-label">{{ cmd.label }}</span>
                  <span class="command-desc">{{ cmd.description }}</span>
                </div>
                <div v-if="filteredCommands.length === 0" class="command-empty">无匹配命令</div>
              </div>
            </div>

            <!-- @ 文件搜索面板 -->
            <div v-if="showAtPanel" class="command-panel at-panel" ref="atPanelRef">
              <div class="command-panel-header">
                <span>插入文件</span>
                <span class="command-search-hint">{{ atQuery || '输入文件名搜索' }}</span>
              </div>
              <div class="command-panel-list">
                <div
                  v-for="(file, idx) in atFiles"
                  :key="file.path"
                  class="command-item"
                  :class="{ active: idx === atActiveIndex }"
                  @mousedown.prevent="selectAtFile(file)"
                >
                  <el-icon class="command-file-icon"><Document /></el-icon>
                  <span class="command-label">{{ file.name }}</span>
                  <span class="command-desc">{{ file.path }}</span>
                </div>
                <div v-if="atFiles.length === 0 && !atLoading" class="command-empty">未找到文件</div>
                <div v-if="atLoading" class="command-empty">搜索中...</div>
              </div>
            </div>

            <el-input
              ref="inputRef"
              v-model="inputMessage"
              type="textarea"
              :rows="3"
              placeholder="输入消息... (Shift+Enter 换行，/ 命令，@ 插入文件)"
              @keydown="onInputKeydown"
              @input="onInputChange"
              resize="none"
            />
            <div class="input-actions">
              <div class="input-actions-left">
                <el-button @click="triggerFileUpload" :loading="uploadingFile" title="上传文件（支持图片、文档、代码等）">
                  <el-icon><Paperclip /></el-icon>
                  附件
                </el-button>
                <!-- 隐藏的文件输入 -->
                <input
                  ref="fileInputRef"
                  type="file"
                  multiple
                  accept="image/*,.md,.txt,.pdf,.docx,.xlsx,.xls,.pptx,.csv,.py,.js,.ts,.json,.yaml,.yml,.html,.css,.sh,.sql,.vue,.jsx,.tsx"
                  style="display:none"
                  @change="onFileInputChange"
                />
                <el-button @click="showSkillDialog = true">
                  <el-icon><MagicStick /></el-icon>
                  生成技能
                </el-button>
                <span v-if="queuedCount > 0" class="queue-indicator">
                  <el-tag size="small" type="warning">{{ queuedCount }} 条待处理</el-tag>
                </span>
              </div>
              <div class="input-actions-right">
                <el-button
                  v-if="currentLoading"
                  type="danger"
                  plain
                  @click="abortTask"
                  :loading="aborting"
                  title="终止当前任务"
                  class="abort-btn"
                >
                  <el-icon><VideoPause /></el-icon>
                  终止
                </el-button>
                <el-button type="primary" @click="sendMessage" :loading="currentLoading && !aborting">
                  <el-icon><Promotion /></el-icon>
                  {{ currentLoading ? '处理中...' : '发送' }}
                </el-button>
              </div>
            </div>
          </div>

          <!-- 生成技能对话框（对接 /api/skills/generate） -->
          <el-dialog v-model="showSkillDialog" title="AI 生成技能" width="520px">
            <p style="margin: 0 0 12px; color: #606266; font-size: 14px;">
              描述你需要的技能功能，AI 将自动生成完整的技能并保存到技能库。
            </p>
            <el-input
              v-model="skillGenDescription"
              type="textarea"
              :rows="5"
              placeholder="例如：一个可以读取 Excel 文件并提取指定列数据的技能，支持多种格式转换..."
              :disabled="generatingSkill"
            />
            <template #footer>
              <el-button @click="showSkillDialog = false" :disabled="generatingSkill">取消</el-button>
              <el-button type="primary" @click="generateSkill" :loading="generatingSkill">
                {{ generatingSkill ? 'AI 生成中...' : '生成技能' }}
              </el-button>
            </template>
          </el-dialog>
        </div>
        
        <el-empty
          v-else
          description="请选择或创建一个对话"
          :image-size="200"
        />
      </el-main>
    </el-container>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted, nextTick, computed, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Grid, Delete, Refresh, VideoPlay, VideoPause, Upload, Close, Document, Paperclip, Picture } from '@element-plus/icons-vue'
import axios from 'axios'
import MarkdownIt from 'markdown-it'
import hljs from 'highlight.js'
import 'highlight.js/styles/github.css'

const assistantAvatarUrl = '/logo.png'

const md = new MarkdownIt({
  html: false,
  linkify: true,
  typographer: true,
  highlight: function (str, lang) {
    if (lang && hljs.getLanguage(lang)) {
      try {
        return '<pre class="hljs"><code>' +
               hljs.highlight(str, { language: lang, ignoreIllegals: true }).value +
               '</code></pre>';
      } catch (__) {}
    }
    return '<pre class="hljs"><code>' + md.utils.escapeHtml(str) + '</code></pre>';
  }
})

const renderMarkdown = (content) => {
  if (!content) return ''
  return md.render(content)
}

const copyToClipboard = async (text) => {
  if (window?.electronAPI?.copyText) {
    const success = await window.electronAPI.copyText(text)
    if (success) return
  }
  if (navigator.clipboard && window.isSecureContext) {
    await navigator.clipboard.writeText(text)
    return
  }
  const textArea = document.createElement('textarea')
  textArea.value = text
  textArea.setAttribute('readonly', '')
  textArea.style.position = 'fixed'
  textArea.style.top = '-9999px'
  textArea.style.left = '-9999px'
  document.body.appendChild(textArea)
  textArea.select()
  textArea.setSelectionRange(0, textArea.value.length)
  const success = document.execCommand('copy')
  document.body.removeChild(textArea)
  if (!success) throw new Error('copy_failed')
}

const copyMessage = async (content) => {
  try {
    await copyToClipboard(content)
    ElMessage.success('复制成功')
  } catch (err) {
    ElMessage.error('复制失败')
  }
}

const LAST_CONVERSATION_KEY = 'codebot:lastConversationId'

const conversations = ref([])
const messages = ref([])
const currentConversationId = ref(null)
const inputMessage = ref('')
const loadingCounts = ref({})
const messageListRef = ref(null)
const inputRef = ref(null)
const fileInputRef = ref(null)

// 生成技能
const showSkillDialog = ref(false)
const generatingSkill = ref(false)
const skillGenDescription = ref('')

// Agent 模式：'build' = 默认, 'plan', 'build'
const AGENT_MODE_KEY = 'codebot:agentMode'
const agentMode = ref(localStorage.getItem(AGENT_MODE_KEY) || 'build')
watch(agentMode, (val) => { localStorage.setItem(AGENT_MODE_KEY, val) })

// 终止任务
const aborting = ref(false)
const queuedCount = ref(0)
const serverRunningStatus = ref({})
const queueStatusSyncing = ref({})
const runtimeSeqByConversation = ref({})
const runtimeEventSeqSeen = ref({})
const runtimeAssistantIdByConversation = ref({})
const activeStreamByConversation = ref({})
const runtimeReloadDoneByConversation = ref({})
// 每个对话的 event 气泡缓存，用于切换对话后恢复推理过程展示
const perConversationEventMessages = ref({})
let queueStatusTimer = null
const shouldAutoScroll = ref(true)
const nowTick = ref(Date.now())
let timeRefreshTimer = null
const thirdPartyStatus = ref(null)

// ── 附件管理 ─────────────────────────────────────────────────────────────────
const attachedFiles = ref([])  // [{name, type, content, is_text}]
const uploadingFile = ref(false)
const isDragOver = ref(false)
let _dragCounter = 0

const fileIcon = (name) => {
  const ext = (name || '').split('.').pop().toLowerCase()
  if (['jpg', 'jpeg', 'png', 'gif', 'webp', 'svg', 'bmp'].includes(ext)) return Picture
  return Document
}

const removeAttachedFile = (idx) => {
  attachedFiles.value.splice(idx, 1)
}

const processFiles = async (files) => {
  if (!files || files.length === 0) return
  uploadingFile.value = true
  const errors = []
  for (const file of Array.from(files)) {
    try {
      const formData = new FormData()
      formData.append('file', file)
      const res = await axios.post('/api/chat/upload_file', formData, {
        headers: { 'Content-Type': 'multipart/form-data' }
      })
      if (res.data?.success) {
        attachedFiles.value.push(res.data.data)
        ElMessage.success(`已附加：${file.name}`)
      }
    } catch (e) {
      errors.push(file.name)
    }
  }
  if (errors.length > 0) {
    ElMessage.error(`上传失败：${errors.join(', ')}`)
  }
  uploadingFile.value = false
}

const triggerFileUpload = () => {
  fileInputRef.value?.click()
}

const onFileInputChange = async (e) => {
  await processFiles(e.target.files)
  // 重置 input 以允许重复选同一文件
  e.target.value = ''
}

const onDragEnter = (e) => {
  _dragCounter++
  if (e.dataTransfer?.types?.includes('Files')) {
    isDragOver.value = true
  }
}

const onDragOver = (e) => {
  if (e.dataTransfer?.types?.includes('Files')) {
    e.dataTransfer.dropEffect = 'copy'
    isDragOver.value = true
  }
}

const onDragLeave = () => {
  _dragCounter--
  if (_dragCounter <= 0) {
    _dragCounter = 0
    isDragOver.value = false
  }
}

const onDrop = async (e) => {
  _dragCounter = 0
  isDragOver.value = false
  const files = e.dataTransfer?.files
  if (files && files.length > 0) {
    await processFiles(files)
  }
}

// ── / 命令面板 ────────────────────────────────────────────────────────────────
const showCommandPanel = ref(false)
const commandQuery = ref('')
const commandActiveIndex = ref(0)
const commandPanelRef = ref(null)
const allCommands = ref([])
const allSkillCommands = ref([])

const filteredCommands = computed(() => {
  const q = commandQuery.value.toLowerCase()
  const base = allCommands.value.filter(c =>
    !q || c.label.toLowerCase().includes(q) || c.description.toLowerCase().includes(q)
  )
  const skills = allSkillCommands.value.filter(c =>
    !q || c.label.toLowerCase().includes(q) || c.description.toLowerCase().includes(q) || (c.skill_name || '').toLowerCase().includes(q)
  )
  return [...base, ...skills].slice(0, 10)
})

const loadCommands = async () => {
  try {
    const res = await axios.get('/api/chat/commands')
    if (res.data?.success) {
      allCommands.value = res.data.data.commands || []
      allSkillCommands.value = res.data.data.skills || []
    }
  } catch (e) {
    // 静默失败
  }
}

const selectCommand = (cmd) => {
  showCommandPanel.value = false
  commandQuery.value = ''

  // 删除已输入的 /xxx 前缀
  const slashIdx = inputMessage.value.lastIndexOf('/')
  if (slashIdx !== -1) {
    inputMessage.value = inputMessage.value.substring(0, slashIdx)
  }

  if (cmd.type === 'action') {
    if (cmd.name === 'plan') { agentMode.value = 'plan'; ElMessage.success('已切换到 Plan 模式') }
    else if (cmd.name === 'build') { agentMode.value = 'build'; ElMessage.success('已切换到 Build 模式') }
    else if (cmd.name === 'clear') { inputMessage.value = '' }
    else if (cmd.name === 'memory') {
      inputMessage.value += '显示与当前话题相关的记忆'
    }
  } else if (cmd.type === 'skill' || cmd.type === 'category') {
    // 把技能名称插入消息中作为提示
    inputMessage.value += `使用 ${cmd.skill_name || cmd.label} 技能`
  }
  nextTick(() => inputRef.value?.focus())
}

// ── @ 文件面板 ────────────────────────────────────────────────────────────────
const showAtPanel = ref(false)
const atQuery = ref('')
const atActiveIndex = ref(0)
const atFiles = ref([])
const atLoading = ref(false)
const atPanelRef = ref(null)
let _atSearchTimer = null

const searchAtFiles = async (query) => {
  atLoading.value = true
  try {
    const res = await axios.get('/api/chat/files/search', { params: { query, limit: 15 } })
    atFiles.value = res.data?.data?.files || []
  } catch (e) {
    atFiles.value = []
  } finally {
    atLoading.value = false
  }
}

const selectAtFile = async (file) => {
  showAtPanel.value = false
  atQuery.value = ''

  // 删除已输入的 @xxx 前缀
  const atIdx = inputMessage.value.lastIndexOf('@')
  if (atIdx !== -1) {
    inputMessage.value = inputMessage.value.substring(0, atIdx)
  }

  // 读取文件内容并加入附件
  try {
    const res = await axios.post('/api/chat/read_file', { path: file.path })
    if (res.data?.success) {
      // 避免重复附加
      if (!attachedFiles.value.find(f => f.name === res.data.data.name && f.content === res.data.data.content)) {
        attachedFiles.value.push(res.data.data)
        ElMessage.success(`已插入文件：${file.name}`)
      }
    }
  } catch (e) {
    ElMessage.error(`读取文件失败：${file.name}`)
  }
  nextTick(() => inputRef.value?.focus())
}

// ── 键盘事件处理 ─────────────────────────────────────────────────────────────
const onInputKeydown = (e) => {
  // 命令面板导航
  if (showCommandPanel.value) {
    if (e.key === 'ArrowDown') { e.preventDefault(); commandActiveIndex.value = Math.min(commandActiveIndex.value + 1, filteredCommands.value.length - 1) }
    else if (e.key === 'ArrowUp') { e.preventDefault(); commandActiveIndex.value = Math.max(commandActiveIndex.value - 1, 0) }
    else if (e.key === 'Enter') { e.preventDefault(); if (filteredCommands.value[commandActiveIndex.value]) selectCommand(filteredCommands.value[commandActiveIndex.value]) }
    else if (e.key === 'Escape') { e.preventDefault(); showCommandPanel.value = false }
    return
  }

  // @ 面板导航
  if (showAtPanel.value) {
    if (e.key === 'ArrowDown') { e.preventDefault(); atActiveIndex.value = Math.min(atActiveIndex.value + 1, atFiles.value.length - 1) }
    else if (e.key === 'ArrowUp') { e.preventDefault(); atActiveIndex.value = Math.max(atActiveIndex.value - 1, 0) }
    else if (e.key === 'Enter') { e.preventDefault(); if (atFiles.value[atActiveIndex.value]) selectAtFile(atFiles.value[atActiveIndex.value]) }
    else if (e.key === 'Escape') { e.preventDefault(); showAtPanel.value = false }
    return
  }

  // 正常 Enter 发送
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    sendMessage()
  }
}

const onInputChange = (val) => {
  const text = typeof val === 'string' ? val : inputMessage.value

  // 检测最后一次 / 触发命令面板
  const slashIdx = text.lastIndexOf('/')
  if (slashIdx !== -1 && (slashIdx === 0 || text[slashIdx - 1] === '\n' || text[slashIdx - 1] === ' ')) {
    const afterSlash = text.substring(slashIdx + 1)
    if (!afterSlash.includes(' ') && !afterSlash.includes('\n')) {
      commandQuery.value = afterSlash
      commandActiveIndex.value = 0
      showCommandPanel.value = true
      showAtPanel.value = false
      return
    }
  }
  showCommandPanel.value = false

  // 检测最后一次 @ 触发文件面板
  const atIdx = text.lastIndexOf('@')
  if (atIdx !== -1 && (atIdx === 0 || text[atIdx - 1] === '\n' || text[atIdx - 1] === ' ')) {
    const afterAt = text.substring(atIdx + 1)
    if (!afterAt.includes(' ') && !afterAt.includes('\n')) {
      atQuery.value = afterAt
      atActiveIndex.value = 0
      showAtPanel.value = true
      clearTimeout(_atSearchTimer)
      _atSearchTimer = setTimeout(() => searchAtFiles(afterAt), 200)
      return
    }
  }
  showAtPanel.value = false
}

const abortTask = async () => {
  aborting.value = true
  try {
    await axios.post('/api/chat/abort', { conversation_id: currentConversationId.value })
    ElMessage.success('已发送终止信号')
    queuedCount.value = 0
  } catch {
    ElMessage.error('终止失败')
  } finally {
    aborting.value = false
  }
}

// 模型选择
const LAST_MODEL_KEY = 'codebot:selectedModel'
const _savedModel = localStorage.getItem(LAST_MODEL_KEY) || ''
const selectedModel = ref(_savedModel)
// Pre-populate with the saved model so el-select can resolve its label before the API responds
const availableModels = ref(
  _savedModel
    ? [{ id: _savedModel, name: _savedModel, provider: _savedModel.split('/')[0] || '', model: _savedModel.split('/')[1] || _savedModel }]
    : []
)
const modelsLoading = ref(false)
const modelSearchQuery = ref('')

watch(selectedModel, (val) => {
  if (val) {
    localStorage.setItem(LAST_MODEL_KEY, val)
  } else {
    localStorage.removeItem(LAST_MODEL_KEY)
  }
})

// 按 provider 分组
const groupedModels = computed(() => {
  const groups = {}
  for (const m of availableModels.value) {
    const p = m.provider || 'other'
    if (!groups[p]) groups[p] = { provider: p, providerLabel: p, models: [] }
    groups[p].models.push(m)
  }
  return Object.values(groups)
})

// 搜索过滤结果
const filteredModels = computed(() => {
  if (!modelSearchQuery.value) return availableModels.value
  const q = modelSearchQuery.value.toLowerCase()
  return availableModels.value.filter(m =>
    m.id.toLowerCase().includes(q) ||
    m.name.toLowerCase().includes(q) ||
    (m.provider || '').toLowerCase().includes(q) ||
    (m.model || '').toLowerCase().includes(q)
  )
})

const filterModels = (query) => {
  modelSearchQuery.value = query || ''
}

const onModelDropdownOpen = (visible) => {
  if (visible) modelSearchQuery.value = ''
}

// ── 记忆提示 ──────────────────────────────────────────────────────────────
const memoryHints = ref([])
let _hintsTimer = null

const hintTagType = (category) => {
  const map = {
    habit: 'success',
    preference: 'warning',
    profile: 'info',
    fact: '',
    contact: 'danger',
    address: 'danger',
    note: 'info',
  }
  return map[category] ?? 'info'
}

const fetchMemoryHints = async (query) => {
  if (!query || query.trim().length < 3) {
    memoryHints.value = []
    return
  }
  try {
    const res = await axios.get('/api/memory/hints', { params: { query: query.trim(), top_k: 5 } })
    memoryHints.value = res.data?.data ?? []
  } catch {
    memoryHints.value = []
  }
}

watch(inputMessage, (val) => {
  clearTimeout(_hintsTimer)
  if (!val || val.trim().length < 3) {
    memoryHints.value = []
    return
  }
  _hintsTimer = setTimeout(() => fetchMemoryHints(val), 600)
})

// 批量处理
const batchMode = ref(false)
const selectedConvIds = ref([])
const selectAll = ref(false)

const isIndeterminate = computed(() => {
  return selectedConvIds.value.length > 0 && selectedConvIds.value.length < conversations.value.length
})

watch(selectedConvIds, (val) => {
  selectAll.value = val.length === conversations.value.length && conversations.value.length > 0
})

const toggleBatchMode = () => {
  batchMode.value = !batchMode.value
  selectedConvIds.value = []
  selectAll.value = false
}

const toggleConvSelection = (id) => {
  const idx = selectedConvIds.value.indexOf(id)
  if (idx === -1) {
    selectedConvIds.value.push(id)
  } else {
    selectedConvIds.value.splice(idx, 1)
  }
}

const handleSelectAll = (val) => {
  if (val) {
    selectedConvIds.value = conversations.value.map(c => c.id)
  } else {
    selectedConvIds.value = []
  }
}

const batchDeleteConversations = async () => {
  if (selectedConvIds.value.length === 0) return
  try {
    await ElMessageBox.confirm(
      `确定删除选中的 ${selectedConvIds.value.length} 个对话吗？此操作不可恢复。`,
      '批量删除',
      {
        confirmButtonText: '删除',
        cancelButtonText: '取消',
        type: 'warning'
      }
    )
    const ids = [...selectedConvIds.value]
    let successCount = 0
    for (const id of ids) {
      try {
        await axios.delete(`/api/chat/conversations/${id}`)
        successCount++
        if (currentConversationId.value === id) {
          currentConversationId.value = null
          messages.value = []
        }
      } catch {}
    }
    selectedConvIds.value = []
    selectAll.value = false
    await loadConversations()
    ElMessage.success(`已删除 ${successCount} 个对话`)
  } catch (error) {
    if (error !== 'cancel') {
      ElMessage.error('批量删除失败')
    }
  }
}

const getLoadingCount = (conversationId) => {
  if (!conversationId) return 0
  return Number(loadingCounts.value[conversationId] || 0)
}

const isConversationLoading = (conversationId) => getLoadingCount(conversationId) > 0

const currentLoading = computed(() => {
  if (!currentConversationId.value) return false
  return isConversationLoading(currentConversationId.value)
})

const incrementLoading = (conversationId) => {
  const next = getLoadingCount(conversationId) + 1
  loadingCounts.value = { ...loadingCounts.value, [conversationId]: next }
}

const decrementLoading = (conversationId) => {
  const next = getLoadingCount(conversationId) - 1
  const updated = { ...loadingCounts.value }
  if (next > 0) {
    updated[conversationId] = next
  } else {
    delete updated[conversationId]
  }
  loadingCounts.value = updated
}

const setConversationLoadingState = (conversationId, running) => {
  const key = Number(conversationId)
  if (!key) return
  const updated = { ...loadingCounts.value }
  if (running) {
    updated[key] = Math.max(1, Number(updated[key] || 0))
  } else {
    delete updated[key]
  }
  loadingCounts.value = updated
}

const ensureRuntimeAssistant = (conversationId) => {
  if (currentConversationId.value !== conversationId) return null
  const knownId = runtimeAssistantIdByConversation.value[conversationId]
  if (knownId) {
    const found = messages.value.find((m) => m.id === knownId)
    if (found) return found
  }
  const msg = {
    id: `runtime-assistant-${conversationId}`,
    role: 'assistant',
    content: '',
    streaming: true,
    created_at: new Date().toISOString()
  }
  messages.value.push(msg)
  runtimeAssistantIdByConversation.value = {
    ...runtimeAssistantIdByConversation.value,
    [conversationId]: msg.id
  }
  return msg
}

const applyRuntimeEvents = (conversationId, events, runtimeContent, running) => {
  if (currentConversationId.value !== conversationId) return
  const seen = new Set(runtimeEventSeqSeen.value[conversationId] || [])
  let assistantMsg = null
  const newEventMsgs = []
  for (const event of (events || [])) {
    const seq = Number(event?.seq || 0)
    if (seq > 0 && seen.has(seq)) continue
    if (seq > 0) seen.add(seq)
    if (event?.type === 'tool_event' || event?.type === 'meta_event') {
      const eventMsg = {
        id: `runtime-event-${conversationId}-${seq || Date.now()}`,
        role: 'event',
        event: event,
        created_at: event?.created_at || new Date().toISOString()
      }
      if (assistantMsg) {
        const idx = messages.value.findIndex((m) => m.id === assistantMsg.id)
        if (idx >= 0) {
          messages.value.splice(idx, 0, eventMsg)
        } else {
          messages.value.push(eventMsg)
        }
      } else {
        messages.value.push(eventMsg)
      }
      newEventMsgs.push(eventMsg)
      continue
    }
    if (event?.type === 'done' && assistantMsg) {
      assistantMsg.content = event.content || runtimeContent || assistantMsg.content
      assistantMsg.streaming = false
      continue
    }
    if (event?.type === 'error' && assistantMsg) {
      assistantMsg.content = event.message || '执行失败'
      assistantMsg.streaming = false
    }
    if (running && (event?.type === 'done' || event?.type === 'error') && !assistantMsg) {
      assistantMsg = ensureRuntimeAssistant(conversationId)
      if (!assistantMsg) continue
      if (event?.type === 'done') {
        assistantMsg.content = event.content || runtimeContent || assistantMsg.content
        assistantMsg.streaming = false
      } else {
        assistantMsg.content = event.message || '执行失败'
        assistantMsg.streaming = false
      }
    }
  }
  // 缓存新增的 event 消息，确保切换对话后可以恢复
  if (newEventMsgs.length > 0) {
    const existing = perConversationEventMessages.value[conversationId] || []
    const existingIds = new Set(existing.map((e) => e.id))
    const toAdd = newEventMsgs.filter((e) => !existingIds.has(e.id))
    if (toAdd.length > 0) {
      perConversationEventMessages.value = {
        ...perConversationEventMessages.value,
        [conversationId]: [...existing, ...toAdd]
      }
    }
  }
  runtimeEventSeqSeen.value = {
    ...runtimeEventSeqSeen.value,
    [conversationId]: Array.from(seen).slice(-300)
  }
  if (running && ((typeof runtimeContent === 'string' && runtimeContent) || running)) {
    assistantMsg = assistantMsg || ensureRuntimeAssistant(conversationId)
  }
  if (assistantMsg) {
    if (typeof runtimeContent === 'string' && runtimeContent) assistantMsg.content = runtimeContent
    assistantMsg.streaming = Boolean(running)
  }
  nextTick(() => scrollToBottom())
}

const fetchQueueStatus = async (conversationId, options = {}) => {
  const key = Number(conversationId)
  if (!key) return
  if (queueStatusSyncing.value[key]) return
  queueStatusSyncing.value = { ...queueStatusSyncing.value, [key]: true }
  try {
    const sinceSeq = Number(runtimeSeqByConversation.value[key] || 0)
    const response = await axios.get(`/api/chat/queue_status/${key}`, { params: { since_seq: sinceSeq } })
    const data = response.data?.data || {}
    const running = Boolean(data.running)
    const queued = Number(data.queued || 0)
    const runtimeEvents = Array.isArray(data.runtime_events) ? data.runtime_events : []
    const runtimeLastSeq = Number(data.runtime_last_seq || sinceSeq || 0)
    const runtimeContent = typeof data.runtime_content === 'string' ? data.runtime_content : ''
    const hasDoneEvent = runtimeEvents.some((e) => e?.type === 'done' || e?.type === 'error')
    const previousRunning = Boolean(serverRunningStatus.value[key])
    serverRunningStatus.value = { ...serverRunningStatus.value, [key]: running }
    runtimeSeqByConversation.value = { ...runtimeSeqByConversation.value, [key]: runtimeLastSeq }
    if (currentConversationId.value === key) {
      queuedCount.value = queued
    }
    setConversationLoadingState(key, running)
    // 跳过 applyRuntimeEvents：流式传输进行中 OR 流刚结束等待 reload OR reload 已完成
    const skipRuntimeApply = activeStreamByConversation.value[key]
      || runtimeReloadDoneByConversation.value[key] === 'pending'
      || runtimeReloadDoneByConversation.value[key] === true
    if (!skipRuntimeApply) {
      applyRuntimeEvents(key, runtimeEvents, runtimeContent, running)
    }
    const shouldReloadAfterRuntime = options.reloadOnFinish
      && !running
      && currentConversationId.value === key
      && !activeStreamByConversation.value[key]
      && runtimeReloadDoneByConversation.value[key] !== true
      && (previousRunning || hasDoneEvent || Boolean(runtimeAssistantIdByConversation.value[key])
        || runtimeReloadDoneByConversation.value[key] === 'pending')
    if (shouldReloadAfterRuntime) {
      const msgResp = await axios.get(`/api/chat/conversations/${key}/messages`)
      const dbMessages = msgResp.data?.data?.items || []
      // 流结束后从 DB 加载最终消息，不再重新注入缓存的 event 气泡。
      // event 气泡是流式传输期间的临时 UI 元素，流结束后不需要保留。
      // 清除该对话的 event 缓存，防止重复注入。
      messages.value = dbMessages
      const updatedCache = { ...perConversationEventMessages.value }
      delete updatedCache[key]
      perConversationEventMessages.value = updatedCache
      runtimeAssistantIdByConversation.value = { ...runtimeAssistantIdByConversation.value, [key]: null }
      runtimeEventSeqSeen.value = { ...runtimeEventSeqSeen.value, [key]: [] }
      runtimeReloadDoneByConversation.value = { ...runtimeReloadDoneByConversation.value, [key]: true }
      await nextTick()
      scrollToBottom()
    } else if (running) {
      runtimeReloadDoneByConversation.value = { ...runtimeReloadDoneByConversation.value, [key]: false }
    }
  } catch (error) {
  } finally {
    const updated = { ...queueStatusSyncing.value }
    delete updated[key]
    queueStatusSyncing.value = updated
  }
}

// 加载对话列表
const getMostRecentConversationId = (items) => {
  if (!Array.isArray(items) || items.length === 0) return null
  let bestId = items[0]?.id ?? null
  let bestTime = -1
  for (const item of items) {
    const raw = item?.updated_at ?? item?.created_at ?? ''
    const time = Number.isFinite(Date.parse(raw)) ? Date.parse(raw) : -1
    if (time > bestTime) {
      bestTime = time
      bestId = item?.id ?? bestId
    }
  }
  return bestId
}

const loadConversations = async (autoSelect = false) => {
  try {
    const response = await axios.get('/api/chat/conversations')
    conversations.value = response.data.data.items || []
    if (autoSelect && !currentConversationId.value && conversations.value.length > 0) {
      const lastIdRaw = localStorage.getItem(LAST_CONVERSATION_KEY)
      const lastId = lastIdRaw ? Number(lastIdRaw) : null
      const hasLast = lastId && conversations.value.some((c) => c.id === lastId)
      const pickId = hasLast ? lastId : getMostRecentConversationId(conversations.value)
      if (pickId) {
        await selectConversation(pickId)
      }
    }
  } catch (error) {
    ElMessage.error('加载对话列表失败')
  }
}

// 加载可用模型列表
const loadModels = async () => {
  modelsLoading.value = true
  try {
    const res = await axios.get('/api/chat/models')
    const raw = res.data?.data?.models || []
    const newList = raw.map(m => {
      if (typeof m === 'string') return { id: m, name: m, provider: '', model: m }
      const id = m.id || m.modelID || m.name || ''
      const provider = m.provider || id.split('/')[0] || ''
      const model = m.model || id.split('/')[1] || id
      return { id, name: m.name || id, provider, model }
    }).filter(m => m.id)

    // 如果用户有已保存的模型，且新列表中没有对应条目，则保留一个占位条目以避免 el-select 显示空值
    const saved = selectedModel.value
    if (saved && !newList.find(m => m.id === saved)) {
      newList.push({ id: saved, name: saved, provider: saved.split('/')[0] || '', model: saved.split('/')[1] || saved })
    }
    availableModels.value = newList
  } catch {
    // 加载失败时，如果有已保存模型，保留其占位条目
    const saved = selectedModel.value
    if (saved) {
      availableModels.value = [{ id: saved, name: saved, provider: saved.split('/')[0] || '', model: saved.split('/')[1] || saved }]
    } else {
      availableModels.value = []
    }
  } finally {
    modelsLoading.value = false
  }
}

const loadThirdPartyStatus = async () => {
  try {
    const response = await axios.get('/api/mcp/codebot/status')
    thirdPartyStatus.value = response.data?.data ?? null
  } catch {
    thirdPartyStatus.value = null
  }
}

// 创建新对话
const createNewConversation = async () => {
  try {
    const response = await axios.post('/api/chat/conversations')
    const conversation = response.data.data
    conversations.value.unshift(conversation)
    await selectConversation(conversation.id)
  } catch (error) {
    ElMessage.error('创建对话失败')
  }
}

// 选择对话
const selectConversation = async (conversationId) => {
  shouldAutoScroll.value = true
  currentConversationId.value = conversationId
  localStorage.setItem(LAST_CONVERSATION_KEY, String(conversationId))
  try {
    const response = await axios.get(`/api/chat/conversations/${conversationId}/messages`)
    const dbMessages = response.data.data.items || []
    // 恢复该对话已缓存的 event 气泡（推理过程），仅在流式传输仍在进行中时
    // 如果 runtimeReloadDone 已为 true，说明流已结束并已做过 DB reload，不需要再注入缓存
    const cachedEvents = perConversationEventMessages.value[conversationId] || []
    const streamDone = Boolean(runtimeReloadDoneByConversation.value[conversationId])
    if (cachedEvents.length > 0 && !streamDone) {
      messages.value = [...dbMessages, ...cachedEvents].sort((a, b) => {
        const ta = Date.parse(a.created_at) || 0
        const tb = Date.parse(b.created_at) || 0
        return ta - tb
      })
    } else {
      messages.value = dbMessages
    }
    runtimeAssistantIdByConversation.value = { ...runtimeAssistantIdByConversation.value, [conversationId]: null }
    runtimeEventSeqSeen.value = { ...runtimeEventSeqSeen.value, [conversationId]: [] }
    runtimeReloadDoneByConversation.value = { ...runtimeReloadDoneByConversation.value, [conversationId]: false }
    await fetchQueueStatus(conversationId)
    await nextTick()
    scrollToBottom()
  } catch (error) {
    ElMessage.error('加载消息失败')
  }
}

const toolEventLabel = (event) => {
  const eventType = event?.event_type || 'event'
  const map = {
    'step-start': '步骤开始',
    'step-finish': '步骤完成',
    'tool-call': '工具调用',
    'tool-result': '工具结果',
    'reasoning': '推理',
    'plan': '计划'
  }
  return map[eventType] || eventType
}

const toolEventSummary = (event) => {
  const data = event?.data || {}
  if (typeof data?.text === 'string' && data.text.trim()) return data.text.trim()
  if (typeof data?.name === 'string' && data.name.trim()) return data.name.trim()
  if (typeof data?.tool === 'string' && data.tool.trim()) return data.tool.trim()
  if (typeof data?.reason === 'string' && data.reason.trim()) return data.reason.trim()
  if (typeof data?.state === 'string' && data.state.trim()) return data.state.trim()
  if (typeof data?.summary === 'string' && data.summary.trim()) return data.summary.trim()
  if (typeof data?.status === 'string' && data.status.trim()) return data.status.trim()
  return '处理中'
}

let streamScrollScheduled = false
const scheduleStreamScroll = () => {
  if (streamScrollScheduled) return
  streamScrollScheduled = true
  requestAnimationFrame(() => {
    streamScrollScheduled = false
    scrollToBottom()
  })
}

const streamChatResponse = async (payload, onEvent) => {
  const response = await fetch('/api/chat/send_stream', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  })
  if (!response.ok || !response.body) {
    throw new Error(`HTTP ${response.status}`)
  }
  const reader = response.body.getReader()
  const decoder = new TextDecoder('utf-8')
  let buffer = ''
  let eventCountSinceYield = 0
  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const lines = buffer.split('\n')
    buffer = lines.pop() || ''
    for (const line of lines) {
      const text = line.trim()
      if (!text) continue
      await onEvent(JSON.parse(text))
      eventCountSinceYield += 1
      if (eventCountSinceYield >= 25) {
        eventCountSinceYield = 0
        await new Promise((resolve) => requestAnimationFrame(resolve))
      }
    }
  }
  if (buffer.trim()) {
    await onEvent(JSON.parse(buffer.trim()))
  }
}

// 发送消息
const sendMessage = async () => {
  if (!inputMessage.value.trim() && attachedFiles.value.length === 0) return
  if (!currentConversationId.value) return

  const conversationId = currentConversationId.value
  const content = inputMessage.value
  const filesToSend = [...attachedFiles.value]
  const isLoading = isConversationLoading(conversationId)

  inputMessage.value = ''
  attachedFiles.value = []
  memoryHints.value = []
  showCommandPanel.value = false
  showAtPanel.value = false

  // 构建展示用的消息内容（附件 + 文字）
  const displayContent = filesToSend.length > 0
    ? `${filesToSend.map(f => `[附件: ${f.name}]`).join(' ')}\n${content}`.trim()
    : content

  // If already loading, still save locally and send to queue
  if (isLoading) {
    messages.value.push({
      id: Date.now(),
      role: 'user',
      content: displayContent,
      created_at: new Date().toISOString()
    })
    await nextTick()
    scrollToBottom(true)
    try {
      // Save user message to DB
      await axios.post(`/api/chat/conversations/${conversationId}/messages`, { content: displayContent })
      // Send to OpenCode (will be queued on backend)
      const response = await axios.post('/api/chat/send', {
        conversation_id: conversationId,
        message: content,
        model: selectedModel.value || null,
        mode: agentMode.value || null,
        attached_files: filesToSend.length > 0 ? filesToSend : null,
      })
      if (response.data?.data?.queued) {
        queuedCount.value += 1
        ElMessage.info('消息已加入队列，将在当前任务完成后处理')
      }
    } catch {
      ElMessage.error('发送失败')
    }
    return
  }

  incrementLoading(conversationId)

  try {
    await axios.post(`/api/chat/conversations/${conversationId}/messages`, {
      content: displayContent
    })

    if (currentConversationId.value === conversationId) {
      messages.value.push({
        id: Date.now(),
        role: 'user',
        content: displayContent,
        created_at: new Date().toISOString()
      })
    }

    if (currentConversationId.value === conversationId) {
      await nextTick()
      scrollToBottom(true)
    }

    const assistantMessage = {
      id: Date.now() + 1,
      role: 'assistant',
      content: '',
      tool_events: [],
      streaming: true,
      created_at: new Date().toISOString()
    }
    if (currentConversationId.value === conversationId) {
      messages.value.push(assistantMessage)
    }

    let pendingDelta = ''
    let flushScheduled = false
    const flushPendingDelta = () => {
      if (!pendingDelta) return
      assistantMessage.content = `${assistantMessage.content || ''}${pendingDelta}`
      pendingDelta = ''
    }

    const scheduleFlush = () => {
      if (flushScheduled) return
      flushScheduled = true
      requestAnimationFrame(() => {
        flushScheduled = false
        flushPendingDelta()
        if (currentConversationId.value === conversationId) scheduleStreamScroll()
      })
    }

    activeStreamByConversation.value = { ...activeStreamByConversation.value, [conversationId]: true }
    try {
      await streamChatResponse({
        conversation_id: conversationId,
        message: content,
        model: selectedModel.value || null,
        mode: agentMode.value || null,
        attached_files: filesToSend.length > 0 ? filesToSend : null,
      }, async (event) => {
        if (event?.type === 'queued') {
          queuedCount.value += 1
          if (currentConversationId.value === conversationId) {
            messages.value = messages.value.filter((m) => m.id !== assistantMessage.id)
          }
          ElMessage.info('消息已加入队列，将在当前任务完成后处理')
          return
        }
        if (event?.type === 'content_delta') {
          const delta = event.delta || ''
          if (delta) {
            pendingDelta += delta
            scheduleFlush()
          }
          return
        }
        if (event?.type === 'tool_event' || event?.type === 'meta_event') {
          if (currentConversationId.value === conversationId) {
            const idx = messages.value.findIndex((m) => m.id === assistantMessage.id)
            const eventMsg = {
              id: Date.now() + Math.random(),
              role: 'event',
              event: event,
              created_at: new Date().toISOString()
            }
            if (idx >= 0) {
              messages.value.splice(idx, 0, eventMsg)
            } else {
              messages.value.push(eventMsg)
            }
            // 同步缓存到 perConversationEventMessages，确保切换对话后可以恢复
            const convEvents = perConversationEventMessages.value[conversationId] || []
            convEvents.push(eventMsg)
            perConversationEventMessages.value = { ...perConversationEventMessages.value, [conversationId]: convEvents }
            scheduleStreamScroll()
          }
          return
        }
        if (event?.type === 'done') {
          flushPendingDelta()
          assistantMessage.content = event.content || assistantMessage.content
          assistantMessage.streaming = false
          if (currentConversationId.value === conversationId) scheduleStreamScroll()
          return
        }
        if (event?.type === 'error') {
          throw new Error(event.message || '流式回复失败')
        }
      })
    } finally {
      activeStreamByConversation.value = { ...activeStreamByConversation.value, [conversationId]: false }
      // 标记该对话"等待 reload"，阻止 applyRuntimeEvents 在 reload 前重复注入 events
      runtimeReloadDoneByConversation.value = { ...runtimeReloadDoneByConversation.value, [conversationId]: 'pending' }
    }

    queuedCount.value = Math.max(0, queuedCount.value - 1)
    await loadConversations()

  } catch (error) {
    ElMessage.error('发送消息失败')
  } finally {
    decrementLoading(conversationId)
    if (currentConversationId.value === conversationId) {
      await nextTick()
      scrollToBottom(true)
    }
  }
}

// 上传图片（保留兼容，实际已由 triggerFileUpload 替代）
const uploadImage = () => {
  triggerFileUpload()
}

// 生成技能 — 调用 /api/skills/generate（与技能页面一致）
const generateSkill = async () => {
  if (!skillGenDescription.value.trim()) {
    ElMessage.warning('请输入技能描述')
    return
  }
  generatingSkill.value = true
  try {
    const response = await axios.post('/api/skills/generate', {
      description: skillGenDescription.value.trim()
    })
    ElMessage.success(response.data.message || '技能已生成，可在技能页面查看')
    showSkillDialog.value = false
    skillGenDescription.value = ''
  } catch (error) {
    const msg = error?.response?.data?.detail || '生成失败，请重试'
    ElMessage.error(msg)
  } finally {
    generatingSkill.value = false
  }
}

// 撤销消息（删除该条及之后所有消息）
const undoFromMessage = async (msg) => {
  if (!currentConversationId.value) return
  try {
    await ElMessageBox.confirm(
      '确定撤销此消息及之后的所有消息吗？此操作不可恢复。',
      '撤销消息',
      { confirmButtonText: '撤销', cancelButtonText: '取消', type: 'warning' }
    )
    await axios.post(`/api/chat/conversations/${currentConversationId.value}/undo`, {
      message_id: msg.id,
      conversation_id: currentConversationId.value
    })
    // Reload messages to reflect the deletion
    const response = await axios.get(`/api/chat/conversations/${currentConversationId.value}/messages`)
    messages.value = response.data.data.items || []
    ElMessage.success('已撤销')
  } catch (error) {
    if (error !== 'cancel') {
      ElMessage.error('撤销失败：消息可能是本地临时消息，请刷新后重试')
    }
  }
}

const deleteConversation = async (conversationId) => {
  try {
    await ElMessageBox.confirm('确定删除这个对话吗？', '删除对话', {
      confirmButtonText: '删除',
      cancelButtonText: '取消',
      type: 'warning'
    })
    await axios.delete(`/api/chat/conversations/${conversationId}`)
    if (currentConversationId.value === conversationId) {
      currentConversationId.value = null
      messages.value = []
    }
    await loadConversations()
    ElMessage.success('对话已删除')
  } catch (error) {
    if (error !== 'cancel') {
      ElMessage.error('删除对话失败')
    }
  }
}

const renameConversation = async (conversationId, currentTitle) => {
  try {
    const result = await ElMessageBox.prompt('输入新的对话标题', '重命名', {
      confirmButtonText: '保存',
      cancelButtonText: '取消',
      inputValue: currentTitle || ''
    })
    const newTitle = result.value.trim()
    if (!newTitle) {
      ElMessage.warning('标题不能为空')
      return
    }
    await axios.patch(`/api/chat/conversations/${conversationId}/title`, { title: newTitle })
    await loadConversations()
    ElMessage.success('标题已更新')
  } catch (error) {
    if (error !== 'cancel') {
      ElMessage.error('重命名失败')
    }
  }
}

const togglePinConversation = async (conversationId, pinned) => {
  try {
    await axios.post(`/api/chat/conversations/${conversationId}/pin`, { pinned })
    await loadConversations()
    ElMessage.success(pinned ? '已置顶' : '已取消置顶')
  } catch (error) {
    ElMessage.error('更新置顶状态失败')
  }
}

const archiveConversation = async (conversationId) => {
  try {
    await axios.post(`/api/chat/conversations/${conversationId}/archive`, { archived: true })
    if (currentConversationId.value === conversationId) {
      currentConversationId.value = null
      messages.value = []
    }
    await loadConversations()
    ElMessage.success('对话已归档')
  } catch (error) {
    ElMessage.error('归档失败')
  }
}

const startGroupConversation = async (conversationId) => {
  try {
    await axios.post(`/api/chat/conversations/${conversationId}/group`, { is_group: true })
    await loadConversations()
    ElMessage.success('群聊已开启')
  } catch (error) {
    ElMessage.error('开启群聊失败')
  }
}

const shareConversation = async (conversationId) => {
  try {
    const response = await axios.post(`/api/chat/conversations/${conversationId}/share`)
    const sharePath = response.data.data.share_path
    const shareUrl = `${window.location.origin}${sharePath}`
    await copyToClipboard(shareUrl)
    ElMessage.success('分享链接已复制')
  } catch (error) {
    ElMessage.error('生成分享链接失败')
  }
}

const handleConversationCommand = async (conv, command) => {
  if (command === 'share') { await shareConversation(conv.id); return }
  if (command === 'group') { await startGroupConversation(conv.id); return }
  if (command === 'rename') { await renameConversation(conv.id, conv.title); return }
  if (command === 'pin') { await togglePinConversation(conv.id, true); return }
  if (command === 'unpin') { await togglePinConversation(conv.id, false); return }
  if (command === 'archive') { await archiveConversation(conv.id); return }
  if (command === 'delete') { await deleteConversation(conv.id) }
}

const onMessageListScroll = () => {
  const el = messageListRef.value
  if (!el) return
  const distance = el.scrollHeight - el.scrollTop - el.clientHeight
  shouldAutoScroll.value = distance <= 80
}

const scrollToBottom = (force = false) => {
  const el = messageListRef.value
  if (!el) return
  if (!force && !shouldAutoScroll.value) return
  el.scrollTop = el.scrollHeight
  shouldAutoScroll.value = true
}

const formatDate = (dateStr) => {
  nowTick.value
  if (!dateStr) return ''
  const raw = String(dateStr).trim()
  let date = null
  if (/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:\.\d+)?$/.test(raw)) {
    date = new Date(raw.replace(' ', 'T') + 'Z')
  } else if (/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?$/.test(raw)) {
    date = new Date(raw + 'Z')
  } else {
    date = new Date(raw)
  }
  if (!(date instanceof Date) || Number.isNaN(date.getTime())) return ''
  const now = new Date()
  const diff = now - date
  if (diff < 0) return '刚刚'
  if (diff < 60000) return '刚刚'
  if (diff < 3600000) return `${Math.floor(diff / 60000)}分钟前`
  if (diff < 86400000) return `${Math.floor(diff / 3600000)}小时前`
  return date.toLocaleDateString('zh-CN')
}

onMounted(() => {
  loadConversations(true)
  loadModels()
  loadCommands()
  loadThirdPartyStatus()
  queueStatusTimer = setInterval(() => {
    if (currentConversationId.value) {
      fetchQueueStatus(currentConversationId.value, { reloadOnFinish: true })
    }
  }, 2000)
  timeRefreshTimer = setInterval(() => {
    nowTick.value = Date.now()
  }, 30000)
})

onUnmounted(() => {
  if (queueStatusTimer) {
    clearInterval(queueStatusTimer)
    queueStatusTimer = null
  }
  if (timeRefreshTimer) {
    clearInterval(timeRefreshTimer)
    timeRefreshTimer = null
  }
})
</script>

<style scoped>
.chat-container {
  height: 100%;
  overflow: hidden;
}

.el-container {
  height: 100%;
  overflow: hidden;
}

.el-main {
  height: 100%;
  padding: 0;
  overflow: hidden;
}

.el-aside {
  background: #fff;
  border-right: 1px solid #e4e7ed;
  display: flex;
  flex-direction: column;
}

.conversation-header {
  padding: 16px;
  border-bottom: 1px solid #e4e7ed;
  display: flex;
  gap: 8px;
  align-items: center;
}

.new-conv-btn {
  flex: 1;
}

.batch-btn {
  flex-shrink: 0;
}

.batch-toolbar {
  padding: 8px 12px;
  background: #fdf6ec;
  border-bottom: 1px solid #faecd8;
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 13px;
}

.batch-actions {
  display: flex;
  gap: 6px;
}

.batch-checkbox {
  flex-shrink: 0;
  margin-right: 4px;
}

.conversation-item.batch-selected {
  background: #ecf5ff;
  border: 1px solid #b3d8ff;
}

.conversation-list {
  flex: 1;
  overflow-y: auto;
  padding: 8px;
}

.conversation-item {
  padding: 12px;
  border-radius: 8px;
  cursor: pointer;
  margin-bottom: 8px;
  transition: all 0.3s;
  display: flex;
  align-items: center;
  gap: 8px;
}

.conversation-item:hover {
  background: #f5f7fa;
}

.conversation-item.active {
  background: #ecf5ff;
  color: #409EFF;
}

.conversation-info {
  flex: 1;
  min-width: 0;
}

.conversation-actions {
  flex-shrink: 0;
  opacity: 0;
  transition: opacity 0.2s;
}

.conversation-item:hover .conversation-actions {
  opacity: 1;
}

.conversation-title {
  font-weight: 500;
  margin-bottom: 4px;
  display: flex;
  align-items: center;
  gap: 6px;
}

.conversation-title-text {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.conversation-time {
  font-size: 12px;
  color: #909399;
}

.chat-main {
  display: flex;
  flex-direction: column;
  height: 100%;
}

.third-party-banner {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 16px;
  background: #f4f8ff;
  border-bottom: 1px solid #e4ecff;
  color: #3f5873;
  font-size: 13px;
}

.message-list {
  flex: 1;
  overflow-y: auto;
  padding: 20px;
}

.message {
  display: flex;
  gap: 12px;
  margin-bottom: 20px;
}

.message.user {
  flex-direction: row-reverse;
}

.message-content {
  max-width: 70%;
}

.message.user .message-content {
  background: #409EFF;
  color: #fff;
  padding: 12px 16px;
  border-radius: 12px 12px 0 12px;
}

.message.assistant .message-content {
  background: #f5f7fa;
  padding: 12px 16px;
  border-radius: 12px 12px 12px 0;
}

.message-text {
  word-break: break-word;
  white-space: pre-wrap;
}

.tool-events {
  display: none;
}

.event-message {
  padding: 2px 16px 2px 52px;
}

.tool-event-item {
  display: inline-flex;
  gap: 8px;
  align-items: center;
  font-size: 12px;
  color: #606266;
  background: #f5f7fa;
  border: 1px solid #ebeef5;
  border-radius: 6px;
  padding: 4px 10px;
  max-width: 100%;
}
.tool-event-type {
  color: #409eff;
  font-weight: 600;
  white-space: nowrap;
}

.tool-event-summary {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* Markdown Styles */
.markdown-body {
  font-size: 14px;
  line-height: 1.6;
  white-space: normal;
}

.markdown-body :deep(pre) {
  background-color: #282c34;
  color: #abb2bf;
  border-radius: 6px;
  padding: 12px;
  overflow-x: auto;
  margin: 8px 0;
}

.markdown-body :deep(code) {
  font-family: Consolas, Monaco, 'Andale Mono', 'Ubuntu Mono', monospace;
  background-color: rgba(175, 184, 193, 0.2);
  padding: 0.2em 0.4em;
  border-radius: 4px;
  font-size: 85%;
}

.markdown-body :deep(pre code) {
  background-color: transparent;
  padding: 0;
  border-radius: 0;
  color: inherit;
  font-size: 100%;
}

.markdown-body :deep(p) {
  margin: 0.5em 0;
}

.markdown-body :deep(p:first-child) {
  margin-top: 0;
}

.markdown-body :deep(p:last-child) {
  margin-bottom: 0;
}

.markdown-body :deep(ul), .markdown-body :deep(ol) {
  padding-left: 20px;
  margin: 0.5em 0;
}

.markdown-body :deep(blockquote) {
  margin: 0.5em 0;
  padding-left: 1em;
  border-left: 4px solid #dfe2e5;
  color: #6a737d;
}

.markdown-body :deep(table) {
  border-collapse: collapse;
  width: 100%;
  margin: 0.5em 0;
}

.markdown-body :deep(th), .markdown-body :deep(td) {
  border: 1px solid #dfe2e5;
  padding: 6px 13px;
}

.markdown-body :deep(th) {
  background-color: #f6f8fa;
  font-weight: 600;
}

.markdown-body :deep(img) {
  max-width: 100%;
  border-radius: 4px;
}

.message-footer {
  display: flex;
  justify-content: flex-end;
  align-items: center;
  margin-top: 4px;
  height: 20px;
  gap: 8px;
}

.message-actions {
  display: flex;
  align-items: center;
  gap: 2px;
  margin-left: auto;
}

.copy-btn {
  opacity: 0;
  transition: opacity 0.2s;
  padding: 0 4px;
  color: inherit;
}

.message-content:hover .copy-btn {
  opacity: 1;
}

.copy-btn:hover {
  color: #409EFF;
}

.message.user .copy-btn:hover {
  color: rgba(255, 255, 255, 0.8);
}

.message-time {
  font-size: 12px;
  color: #909399;
  margin-right: auto;
}

.message.user .message-time {
  color: rgba(255, 255, 255, 0.8);
}

.message-input {
  border-top: 1px solid #e4e7ed;
  padding: 10px 16px 16px;
  background: #fff;
  position: relative;
  transition: border-color 0.2s;
}

.message-input.drag-over {
  border-color: #409EFF;
  background: #f0f7ff;
}

/* 拖拽遮罩 */
.drag-overlay {
  position: absolute;
  inset: 0;
  background: rgba(64, 158, 255, 0.08);
  border: 2px dashed #409EFF;
  border-radius: 8px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  z-index: 10;
  pointer-events: none;
  font-size: 14px;
  color: #409EFF;
  font-weight: 500;
}
.drag-icon {
  font-size: 32px;
}

/* 附件预览区 */
.attached-files {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 8px;
}
.attached-file-chip {
  display: flex;
  align-items: center;
  gap: 4px;
  background: #ecf5ff;
  border: 1px solid #b3d8ff;
  border-radius: 14px;
  padding: 2px 10px 2px 8px;
  font-size: 12px;
  color: #409EFF;
  max-width: 220px;
  cursor: default;
}
.file-chip-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 150px;
}
.file-chip-icon {
  flex-shrink: 0;
  font-size: 14px;
}
.file-chip-remove {
  flex-shrink: 0;
  cursor: pointer;
  font-size: 13px;
  margin-left: 2px;
  opacity: 0.7;
}
.file-chip-remove:hover {
  opacity: 1;
  color: #f56c6c;
}

/* 命令面板 & @ 面板 */
.command-panel {
  position: absolute;
  bottom: calc(100% + 4px);
  left: 16px;
  right: 16px;
  background: #fff;
  border: 1px solid #e4e7ed;
  border-radius: 8px;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.12);
  z-index: 100;
  overflow: hidden;
  max-height: 300px;
  display: flex;
  flex-direction: column;
}
.at-panel {
  right: auto;
  min-width: 340px;
}
.command-panel-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 6px 12px;
  background: #f5f7fa;
  border-bottom: 1px solid #e4e7ed;
  font-size: 12px;
  color: #606266;
  font-weight: 500;
}
.command-search-hint {
  font-weight: normal;
  color: #909399;
  font-family: monospace;
}
.command-panel-list {
  overflow-y: auto;
  max-height: 240px;
}
.command-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 7px 12px;
  cursor: pointer;
  transition: background 0.15s;
}
.command-item:hover,
.command-item.active {
  background: #ecf5ff;
}
.command-label {
  font-size: 13px;
  font-weight: 500;
  color: #303133;
  min-width: 120px;
  white-space: nowrap;
}
.command-desc {
  font-size: 12px;
  color: #909399;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.command-file-icon {
  color: #909399;
  flex-shrink: 0;
}
.command-empty {
  padding: 12px;
  text-align: center;
  color: #c0c4cc;
  font-size: 13px;
}

/* 工具栏 */
.input-toolbar {
  display: flex;
  align-items: center;
  margin-bottom: 8px;
  gap: 0;
}

.toolbar-left {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.toolbar-label {
  font-size: 12px;
  color: #909399;
  white-space: nowrap;
  flex-shrink: 0;
}

.agent-mode-select {
  width: 90px;
  flex-shrink: 0;
}

:deep(.agent-mode-popper .el-select-dropdown__item) {
  display: flex;
  align-items: center;
  gap: 8px;
}

:deep(.agent-option-name) {
  font-size: 13px;
  font-weight: 500;
  color: #303133;
  min-width: 40px;
}

:deep(.agent-option-desc) {
  font-size: 11px;
  color: #909399;
}

.model-select {
  width: 220px;
}

:deep(.model-option-name) {
  font-size: 13px;
  color: #303133;
}

:deep(.model-option-provider) {
  font-size: 11px;
  color: #909399;
  margin-left: 8px;
}

:deep(.model-select-popper .el-select-dropdown__item) {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

:deep(.model-select-popper) {
  width: 320px !important;
  max-height: 400px;
}

.refresh-btn {
  padding: 4px 6px;
  color: #909399;
}

.refresh-btn:hover {
  color: #409EFF;
}

/* 操作按钮行 */
.input-actions {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-top: 10px;
}

.input-actions-left {
  display: flex;
  gap: 8px;
  align-items: center;
}

.input-actions-right {
  display: flex;
  gap: 8px;
  align-items: center;
}

.abort-btn {
  flex-shrink: 0;
}

.queue-indicator {
  margin-left: 4px;
}

.undo-btn {
  opacity: 0;
  transition: opacity 0.2s;
  padding: 0 4px;
  color: inherit;
}

.message-content:hover .undo-btn {
  opacity: 1;
}

.undo-btn:hover {
  color: #f56c6c;
}

.message.user .undo-btn:hover {
  color: rgba(255, 255, 255, 0.8);
}

/* ── 记忆提示区 ── */
.memory-hints {
  border-top: 1px solid #ebeef5;
  background: #fafbff;
  padding: 6px 16px;
  display: flex;
  align-items: flex-start;
  gap: 8px;
  flex-wrap: wrap;
}

.memory-hints-label {
  font-size: 12px;
  color: #909399;
  white-space: nowrap;
  padding-top: 2px;
  flex-shrink: 0;
}

.memory-hints-list {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.memory-hint-tag {
  max-width: 220px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  cursor: default;
}

.hint-cat {
  font-weight: 600;
  margin-right: 4px;
  opacity: 0.75;
}

.hint-cat::after {
  content: '·';
  margin-left: 3px;
}
</style>
