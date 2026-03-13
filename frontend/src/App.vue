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
            <!-- 通知铃铛 -->
            <el-badge :value="unreadCount" :hidden="unreadCount === 0">
              <el-button :icon="Bell" circle @click="showNotifications" />
            </el-badge>
            
            <!-- 用户菜单 -->
            <el-dropdown>
              <el-avatar :size="32" icon="User" />
              <template #dropdown>
                <el-dropdown-menu>
                  <el-dropdown-item>关于</el-dropdown-item>
                  <el-dropdown-item divided>退出</el-dropdown-item>
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
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { Bell, Monitor, ChatDotRound, Folder, Clock, Grid, Document, Setting, Connection } from '@element-plus/icons-vue'
import { useNotificationStore } from './stores/notification'
import { storeToRefs } from 'pinia'

const notificationDrawer = ref(false)
const notificationStore = useNotificationStore()
const { notifications, unreadCount, config } = storeToRefs(notificationStore)
const pollTimer = ref(null)

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

const formatDate = (dateStr) => {
  const date = new Date(dateStr)
  return date.toLocaleString('zh-CN')
}

onMounted(() => {
  notificationStore.fetchUnreadCount()
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
