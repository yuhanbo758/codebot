<template>
  <div id="app">
    <el-container>
      <!-- 顶部导航栏 -->
      <el-header>
        <div class="header-content">
          <div class="logo">
            <el-icon><Monitor /></el-icon>
            <span>Codebot</span>
          </div>
          
          <el-menu
            mode="horizontal"
            :router="true"
            :default-active="$route.path"
            class="nav-menu"
          >
            <el-menu-item index="/chat">
              <el-icon><ChatDotRound /></el-icon>
              <span>聊天</span>
            </el-menu-item>
            <el-menu-item index="/memory">
              <el-icon><Folder /></el-icon>
              <span>记忆</span>
            </el-menu-item>
            <el-menu-item index="/scheduler">
              <el-icon><Clock /></el-icon>
              <span>定时任务</span>
            </el-menu-item>
            <el-menu-item index="/skills">
              <el-icon><Grid /></el-icon>
              <span>技能</span>
            </el-menu-item>
            <el-menu-item index="/mcp">
              <el-icon><Connection /></el-icon>
              <span>MCP</span>
            </el-menu-item>
            <el-menu-item index="/logs">
              <el-icon><Document /></el-icon>
              <span>日志</span>
            </el-menu-item>
            <el-menu-item index="/settings">
              <el-icon><Setting /></el-icon>
              <span>设置</span>
            </el-menu-item>
          </el-menu>
          
          <div class="header-actions">
            <el-badge :value="growthPendingCount" :hidden="growthPendingCount === 0">
              <el-button size="small" @click="openGrowthDialog">成长候选</el-button>
            </el-badge>
            <!-- 通知铃铛 -->
            <el-badge :value="unreadCount" :hidden="unreadCount === 0">
              <el-button :icon="Bell" circle @click="showNotifications" />
            </el-badge>
            
            <!-- 用户菜单 -->
            <el-dropdown @command="handleAccountCommand">
              <div class="account-trigger">
                <el-avatar :size="32" icon="User" />
                <span v-if="account.authenticated" class="account-name">{{ displayUserName }}</span>
                <el-tag v-if="account.canDownloadUpdates" size="small" type="success">codebot</el-tag>
              </div>
              <template #dropdown>
                <el-dropdown-menu>
                  <el-dropdown-item v-if="!account.authenticated" command="login">登录</el-dropdown-item>
                  <el-dropdown-item v-else command="refresh">刷新会员</el-dropdown-item>
                  <el-dropdown-item command="check-update">检查更新</el-dropdown-item>
                  <el-dropdown-item command="about">关于</el-dropdown-item>
                  <el-dropdown-item v-if="account.authenticated" command="logout" divided>退出登录</el-dropdown-item>
                </el-dropdown-menu>
              </template>
            </el-dropdown>
          </div>
        </div>
      </el-header>
      
      <!-- 主内容区域 -->
      <el-main :class="{ 'no-scroll': $route.path.startsWith('/chat') }">
        <router-view />
      </el-main>
    </el-container>
    
    <!-- 通知抽屉 -->
    <el-drawer
      v-model="notificationDrawer"
      title="通知中心"
      size="400px"
    >
      <div class="notification-drawer-body">
        <!-- 可滚动的通知列表 -->
        <div class="notification-list">
          <div
            v-for="notif in notifications"
            :key="notif.id"
            class="notification-item"
            :class="{ unread: !notif.read }"
          >
            <div class="notification-content">
              <div class="notification-title">{{ notif.title }}</div>
              <div class="notification-message">{{ notif.message }}</div>
              <div class="notification-time">{{ formatDate(notif.created_at) }}</div>
            </div>
            <el-button
              v-if="!notif.read"
              size="small"
              @click="markAsRead(notif.id)"
            >
              标记已读
            </el-button>
          </div>

          <el-empty v-if="notifications.length === 0" description="暂无通知" />
        </div>

        <!-- 固定在底部的操作按钮 -->
        <div class="notification-actions" v-if="notifications.length > 0">
          <el-button @click="markAllAsRead" type="primary" plain>
            标记全部已读
          </el-button>
          <el-button @click="clearNotifications" type="danger" plain>
            清空通知
          </el-button>
        </div>
      </div>
    </el-drawer>

    <el-dialog v-model="updateDialogVisible" title="Codebot 更新" width="460px">
      <div class="update-panel">
        <div class="update-row">
          <span>更新源</span>
          <el-tag :type="updateState.source === 'object-storage' ? 'success' : 'info'">
            {{ updateState.source === 'object-storage' ? '会员对象存储' : 'GitHub Releases' }}
          </el-tag>
        </div>
        <div class="update-row">
          <span>状态</span>
          <span>{{ updateState.message || '尚未检查' }}</span>
        </div>
        <div v-if="updateState.progress > 0" class="update-progress">
          <el-progress :percentage="Math.round(updateState.progress)" />
        </div>
      </div>
      <template #footer>
        <el-button @click="checkUpdate" :loading="updateLoading">检查更新</el-button>
        <el-button type="primary" @click="downloadUpdate" :disabled="!updateState.available" :loading="downloadLoading">下载</el-button>
        <el-button type="success" @click="installUpdate" :disabled="!updateState.downloaded">安装重启</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="growthDialogVisible" title="成长候选" width="720px">
      <el-table v-loading="growthLoading" :data="growthCandidates" height="360px">
        <el-table-column prop="kind" label="类型" width="90" />
        <el-table-column prop="title" label="标题" width="180" />
        <el-table-column prop="content" label="内容" min-width="260" show-overflow-tooltip />
        <el-table-column label="操作" width="190">
          <template #default="{ row }">
            <el-button size="small" link @click="editGrowth(row)">编辑</el-button>
            <el-button size="small" type="primary" link @click="acceptGrowth(row)">接受</el-button>
            <el-button size="small" type="danger" link @click="rejectGrowth(row)">拒绝</el-button>
          </template>
        </el-table-column>
      </el-table>
      <template #footer>
        <el-button @click="loadGrowthCandidates" :loading="growthLoading">刷新</el-button>
        <el-button @click="growthDialogVisible = false">关闭</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="growthEditVisible" title="编辑成长候选" width="620px">
      <el-form label-width="90px">
        <el-form-item v-if="growthEditForm.kind !== 'task'" label="标题">
          <el-input v-model="growthEditForm.title" />
        </el-form-item>
        <template v-if="growthEditForm.kind === 'task'">
          <el-form-item label="任务名称">
            <el-input v-model="growthEditForm.task.name" />
          </el-form-item>
          <el-form-item label="自然语言时间">
            <el-input v-model="growthEditForm.task.schedule_text" placeholder="例如：每天早上 9 点、每周一 10 点" />
          </el-form-item>
          <el-form-item label="Cron 表达式">
            <el-input v-model="growthEditForm.task.cron_expression" placeholder="0 9 * * *" />
          </el-form-item>
          <el-form-item label="执行器">
            <el-radio-group v-model="growthEditForm.task.executor">
              <el-radio-button label="opencode">OpenCode</el-radio-button>
              <el-radio-button label="hermes">Hermes</el-radio-button>
            </el-radio-group>
          </el-form-item>
          <el-form-item label="执行模型">
            <el-select
              v-model="growthEditForm.task.execution_model"
              filterable
              clearable
              placeholder="使用记忆整理备用模型"
              style="width: 100%"
              :loading="growthModelsLoading"
              @focus="loadGrowthModels"
            >
              <el-option label="使用记忆整理备用模型" value="" />
              <el-option
                v-for="model in growthAvailableModels"
                :key="model.id"
                :label="model.name || model.id"
                :value="model.id"
              />
            </el-select>
          </el-form-item>
          <el-form-item label="执行内容">
            <el-input v-model="growthEditForm.task.task_prompt" type="textarea" :rows="5" />
          </el-form-item>
          <el-form-item label="通知渠道">
            <el-checkbox-group v-model="growthEditForm.task.notify_channels">
              <el-checkbox label="app">应用内</el-checkbox>
              <el-checkbox label="desktop">系统桌面</el-checkbox>
              <el-checkbox label="lark">飞书</el-checkbox>
              <el-checkbox label="email">邮箱</el-checkbox>
            </el-checkbox-group>
          </el-form-item>
          <el-form-item label="一次性任务">
            <el-switch v-model="growthEditForm.task.run_once" />
          </el-form-item>
        </template>
        <el-form-item v-else label="内容">
          <el-input v-model="growthEditForm.content" type="textarea" :rows="6" />
        </el-form-item>
        <el-form-item label="补充说明">
          <el-input v-model="growthEditForm.evidence" type="textarea" :rows="3" placeholder="可选" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="growthEditVisible = false">取消</el-button>
        <el-button type="primary" @click="saveGrowthEdit" :loading="growthEditLoading">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import { Bell, Monitor, ChatDotRound, Folder, Clock, Grid, Document, Setting, Connection } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { useNotificationStore } from './stores/notification'
import { storeToRefs } from 'pinia'
import axios from 'axios'

const notificationDrawer = ref(false)
const notificationStore = useNotificationStore()
const { notifications, unreadCount, config } = storeToRefs(notificationStore)
const pollTimer = ref(null)
const account = ref({ authenticated: false, canDownloadUpdates: false, user: null })
const accountLoading = ref(false)
const updateDialogVisible = ref(false)
const updateLoading = ref(false)
const downloadLoading = ref(false)
const updateState = ref({
  source: 'github',
  message: '',
  available: false,
  downloaded: false,
  progress: 0
})
const growthDialogVisible = ref(false)
const growthLoading = ref(false)
const growthCandidates = ref([])
const growthPendingCount = ref(0)
const growthPollTimer = ref(null)
const growthEditVisible = ref(false)
const growthEditLoading = ref(false)
const growthEditForm = ref({
  id: '',
  kind: '',
  title: '',
  content: '',
  evidence: '',
  payload: {},
  task: {
    name: '',
    cron_expression: '',
    schedule_text: '',
    task_prompt: '',
    executor: 'opencode',
    execution_model: '',
    run_once: false,
    notify_channels: ['app']
  }
})
const growthAvailableModels = ref([])
const growthModelsLoading = ref(false)
let removeUpdateListener = null
let removeAccountChangedListener = null
let removeSkillDownloadListener = null

const displayUserName = computed(() => {
  const user = account.value.user || {}
  return user.nickname || user.name || user.username || user.email || '已登录'
})

const showNotifications = () => {
  notificationDrawer.value = true
  loadNotifications()
}

const loadNotifications = async () => {
  await notificationStore.fetchNotifications()
}

const markAsRead = async (id) => {
  await notificationStore.markAsRead(id)
  loadNotifications()
}

const markAllAsRead = async () => {
  await notificationStore.markAllAsRead()
  loadNotifications()
}

const clearNotifications = async () => {
  await notificationStore.clearNotifications()
  loadNotifications()
}

const electronApi = () => window.electronAPI || null

const loadAccount = async () => {
  const api = electronApi()
  if (!api?.accountMe) return
  try {
    const result = await api.accountMe()
    account.value = result || { authenticated: false, canDownloadUpdates: false, user: null }
  } catch (err) {
    account.value = { authenticated: false, canDownloadUpdates: false, user: null }
  }
}

const login = async () => {
  const api = electronApi()
  if (!api?.accountLogin) {
    ElMessage.warning('当前运行环境不支持 Electron 登录')
    return
  }
  accountLoading.value = true
  try {
    await api.accountLogin()
    ElMessage.success('已打开程序小店登录页，完成登录后应用会自动同步状态')
  } catch (err) {
    ElMessage.error(err?.message || '登录失败')
  } finally {
    accountLoading.value = false
  }
}

const logout = async () => {
  const api = electronApi()
  if (!api?.accountLogout) return
  await api.accountLogout()
  account.value = { authenticated: false, canDownloadUpdates: false, user: null }
  ElMessage.success('已退出登录')
}

const handleAccountCommand = async (command) => {
  if (command === 'login') await login()
  else if (command === 'logout') await logout()
  else if (command === 'refresh') {
    await loadAccount()
    ElMessage.success('会员状态已刷新')
  } else if (command === 'check-update') {
    updateDialogVisible.value = true
    await checkUpdate()
  } else if (command === 'about') {
    ElMessage.info('Codebot')
  }
}

const handleUpdateStatus = (payload) => {
  if (!payload) return
  updateState.value.source = payload.source || updateState.value.source
  if (payload.type === 'checking') {
    updateState.value.available = false
    updateState.value.downloaded = false
    updateState.value.progress = 0
    updateState.value.message = '正在检查更新...'
  }
  if (payload.type === 'available') {
    updateState.value.available = true
    updateState.value.downloaded = false
    updateState.value.progress = 0
    updateState.value.message = `发现新版本 ${payload.info?.version || ''}`.trim()
  }
  if (payload.type === 'not-available') {
    updateState.value.available = false
    updateState.value.downloaded = false
    updateState.value.progress = 0
    updateState.value.message = '当前已是最新版本'
  }
  if (payload.type === 'download-progress') {
    updateState.value.progress = Number(payload.progress?.percent || 0)
    updateState.value.message = '正在下载更新...'
  }
  if (payload.type === 'downloaded') {
    updateState.value.downloaded = true
    updateState.value.progress = 100
    updateState.value.message = payload.message || '更新已下载，重启后安装'
  }
  if (payload.type === 'error') {
    updateState.value.message = payload.message || '更新失败'
    ElMessage.error(updateState.value.message)
  }
}

const checkUpdate = async () => {
  const api = electronApi()
  if (!api?.checkUpdate) {
    ElMessage.warning('当前运行环境不支持自动更新')
    return
  }
  updateLoading.value = true
  updateState.value.message = '正在检查更新...'
  try {
    const result = await api.checkUpdate()
    updateState.value.source = result?.source || 'github'
  } catch (err) {
    updateState.value.message = err?.message || '检查更新失败'
    ElMessage.error(updateState.value.message)
  } finally {
    updateLoading.value = false
  }
}

const downloadUpdate = async () => {
  const api = electronApi()
  if (!api?.downloadUpdate) return
  downloadLoading.value = true
  try {
    await api.downloadUpdate()
  } catch (err) {
    ElMessage.error(err?.message || '下载更新失败')
  } finally {
    downloadLoading.value = false
  }
}

const installUpdate = async () => {
  const api = electronApi()
  if (api?.installUpdate) await api.installUpdate()
}

const loadGrowthCandidates = async () => {
  growthLoading.value = true
  try {
    const res = await axios.get('/api/growth/candidates', { params: { status: 'pending', limit: 50 } })
    growthCandidates.value = res.data?.data?.items || []
    growthPendingCount.value = growthCandidates.value.length
  } catch (err) {
    ElMessage.error('加载成长候选失败')
  } finally {
    growthLoading.value = false
  }
}

const refreshGrowthCandidateState = async () => {
  if (document.visibilityState === 'hidden') return
  try {
    const res = await axios.get('/api/growth/candidates', { params: { status: 'pending', limit: 50 } })
    const items = res.data?.data?.items || []
    growthPendingCount.value = items.length
    if (growthDialogVisible.value) growthCandidates.value = items
  } catch {}
}

const loadGrowthModels = async () => {
  if (growthAvailableModels.value.length > 0 || growthModelsLoading.value) return
  growthModelsLoading.value = true
  try {
    const resp = await axios.get('/api/chat/models')
    if (resp.data?.success) {
      growthAvailableModels.value = resp.data.data?.models || []
    }
  } catch (err) {
    console.warn('加载成长候选模型列表失败:', err)
  } finally {
    growthModelsLoading.value = false
  }
}

const openGrowthDialog = async () => {
  growthDialogVisible.value = true
  await loadGrowthCandidates()
}

const acceptGrowth = async (row) => {
  try {
    await axios.post(`/api/growth/candidates/${row.id}/accept`)
    ElMessage.success('已接受')
    await loadGrowthCandidates()
  } catch (err) {
    ElMessage.error(err?.response?.data?.detail || '接受失败')
  }
}

const editGrowth = (row) => {
  const payload = row.payload || {}
  growthEditForm.value = {
    id: row.id,
    kind: row.kind || '',
    title: row.title || '',
    content: row.content || '',
    evidence: row.evidence || '',
    payload,
    task: {
      name: payload.name || row.title || '',
      cron_expression: payload.cron_expression || payload.cron || '',
      schedule_text: payload.schedule_text || '',
      task_prompt: payload.task_prompt || row.content || '',
      executor: payload.executor || 'opencode',
      execution_model: payload.execution_model || '',
      run_once: Boolean(payload.run_once),
      notify_channels: Array.isArray(payload.notify_channels) && payload.notify_channels.length ? payload.notify_channels : ['app']
    }
  }
  growthEditVisible.value = true
  loadGrowthModels()
}

const saveGrowthEdit = async () => {
  growthEditLoading.value = true
  try {
    if (growthEditForm.value.kind === 'task') {
      if (!String(growthEditForm.value.task.name || '').trim() || !String(growthEditForm.value.task.task_prompt || '').trim()) {
        ElMessage.warning('任务名称和执行内容不能为空')
        return
      }
      await axios.patch(`/api/growth/candidates/${growthEditForm.value.id}`, {
        evidence: growthEditForm.value.evidence,
        task: {
          name: growthEditForm.value.task.name,
          cron_expression: growthEditForm.value.task.cron_expression,
          schedule_text: growthEditForm.value.task.schedule_text,
          task_prompt: growthEditForm.value.task.task_prompt,
          executor: growthEditForm.value.task.executor,
          execution_model: growthEditForm.value.task.execution_model || '',
          run_once: Boolean(growthEditForm.value.task.run_once),
          notify_channels: growthEditForm.value.task.notify_channels
        }
      })
    } else {
      if (!String(growthEditForm.value.title || '').trim() || !String(growthEditForm.value.content || '').trim()) {
        ElMessage.warning('标题和内容不能为空')
        return
      }
      await axios.patch(`/api/growth/candidates/${growthEditForm.value.id}`, {
        title: growthEditForm.value.title,
        content: growthEditForm.value.content,
        evidence: growthEditForm.value.evidence,
        payload: growthEditForm.value.payload
      })
    }
    ElMessage.success('候选已更新')
    growthEditVisible.value = false
    await loadGrowthCandidates()
  } catch (err) {
    ElMessage.error(err?.response?.data?.detail || '更新候选失败')
  } finally {
    growthEditLoading.value = false
  }
}

const rejectGrowth = async (row) => {
  try {
    await axios.post(`/api/growth/candidates/${row.id}/reject`)
    ElMessage.success('已拒绝')
    await loadGrowthCandidates()
  } catch (err) {
    ElMessage.error('拒绝失败')
  }
}

const formatDate = (dateStr) => {
  const date = new Date(dateStr)
  return date.toLocaleString('zh-CN')
}

onMounted(() => {
  loadAccount()
  if (electronApi()?.onUpdateStatus) {
    removeUpdateListener = electronApi().onUpdateStatus(handleUpdateStatus)
  }
  if (electronApi()?.onAccountChanged) {
    removeAccountChangedListener = electronApi().onAccountChanged(async () => {
      await loadAccount()
    })
  }
  if (electronApi()?.onSkillDownload) {
    removeSkillDownloadListener = electronApi().onSkillDownload((payload) => {
      if (payload?.type === 'completed') {
        const name = payload?.installed?.slug || payload?.fileName || '技能'
        ElMessage.success(`${name} 已下载到 Codebot 技能目录并作为自动生成技能安装`)
      } else if (payload?.type === 'error') {
        ElMessage.error(payload?.message || '程序小店技能下载失败')
      }
    })
  }
  notificationStore.fetchUnreadCount()
  refreshGrowthCandidateState()
  growthPollTimer.value = setInterval(refreshGrowthCandidateState, 2000)
  notificationStore.fetchConfig().then(() => {
    const interval = Math.max(5, Math.min(120, Number(config.value?.poll_interval || 30)))
    if (pollTimer.value) {
      clearInterval(pollTimer.value)
    }
    pollTimer.value = setInterval(() => {
      notificationStore.fetchUnreadCount()
    }, interval * 1000)
  })
})

onBeforeUnmount(() => {
  if (pollTimer.value) clearInterval(pollTimer.value)
  if (growthPollTimer.value) clearInterval(growthPollTimer.value)
  if (typeof removeUpdateListener === 'function') removeUpdateListener()
  if (typeof removeAccountChangedListener === 'function') removeAccountChangedListener()
  if (typeof removeSkillDownloadListener === 'function') removeSkillDownloadListener()
})
</script>

<style scoped>
#app {
  height: 100vh;
}

.el-header {
  background: #fff;
  border-bottom: 1px solid #e4e7ed;
  padding: 0;
}

.header-content {
  display: flex;
  align-items: center;
  height: 100%;
  padding: 0 20px;
}

.logo {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 20px;
  font-weight: bold;
  color: #409EFF;
  margin-right: 40px;
}

.nav-menu {
  flex: 1;
  border-bottom: none;
}

.header-actions {
  display: flex;
  align-items: center;
  gap: 16px;
}

.account-trigger {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  cursor: pointer;
}

.account-name {
  max-width: 96px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: #303133;
  font-size: 13px;
}

.update-panel {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.update-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}

.update-progress {
  padding-top: 4px;
}

.notification-drawer-body {
  display: flex;
  flex-direction: column;
  height: 100%;
  overflow: hidden;
}

.notification-list {
  flex: 1;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding-bottom: 8px;
}

.notification-item {
  padding: 12px;
  border-radius: 8px;
  background: #f5f7fa;
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
}

.notification-item.unread {
  background: #ecf5ff;
  border-left: 3px solid #409EFF;
}

.notification-content {
  flex: 1;
}

.notification-title {
  font-weight: bold;
  margin-bottom: 4px;
}

.notification-message {
  font-size: 14px;
  color: #606266;
  margin-bottom: 4px;
}

.notification-time {
  font-size: 12px;
  color: #909399;
}

.notification-actions {
  flex-shrink: 0;
  padding: 16px 0 4px;
  border-top: 1px solid #e4e7ed;
  display: flex;
  gap: 12px;
  justify-content: center;
}
</style>

<style>
html,
body {
  height: 100%;
  margin: 0;
  overflow: hidden;
}

#app {
  height: 100%;
  overflow: hidden;
}

/* el-container 撑满整个视口 */
.el-container {
  height: 100%;
}

/* el-main 占满剩余高度，允许内容页滚动 */
.el-main {
  overflow-y: auto !important;
  flex: 1;
  min-height: 0;
  position: relative;
}

/* 聊天页禁止 el-main 自身滚动，由内部子元素控制 */
.el-main.no-scroll {
  overflow: hidden !important;
  padding: 0 !important;
}

/* 让 drawer body 撑满高度且不自身滚动，由内部子元素控制滚动 */
.el-drawer__body {
  overflow: hidden !important;
  display: flex;
  flex-direction: column;
  height: 100%;
  padding: 16px 20px;
  box-sizing: border-box;
}
</style>
