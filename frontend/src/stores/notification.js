import { defineStore } from 'pinia'
import axios from 'axios'

export const useNotificationStore = defineStore('notification', {
  state: () => ({
    notifications: [],
    unreadCount: 0,
    config: {
      poll_interval: 30
    }
  }),
  
  getters: {
    hasUnread: (state) => state.unreadCount > 0
  },
  
  actions: {
    async fetchNotifications() {
      try {
        const response = await axios.get('/api/notifications')
        this.notifications = response.data.data || []
      } catch (error) {
        console.error('获取通知失败:', error)
      }
    },
    
    async fetchUnreadCount() {
      try {
        const response = await axios.get('/api/notifications/unread-count')
        this.unreadCount = response.data.count || 0
      } catch (error) {
        console.error('获取未读数失败:', error)
      }
    },
    
    async fetchConfig() {
      try {
        const response = await axios.get('/api/notifications/config')
        this.config = response.data.data || { poll_interval: 30 }
      } catch (error) {
        console.error('获取通知配置失败:', error)
      }
    },
    
    async markAsRead(id) {
      try {
        await axios.put(`/api/notifications/${id}/read`)
        await this.fetchUnreadCount()
      } catch (error) {
        console.error('标记已读失败:', error)
      }
    },
    
    async markAllAsRead() {
      try {
        await axios.put('/api/notifications/read-all')
        await this.fetchUnreadCount()
      } catch (error) {
        console.error('标记全部已读失败:', error)
      }
    },
    
    async clearNotifications() {
      try {
        await axios.delete('/api/notifications')
        this.notifications = []
        this.unreadCount = 0
      } catch (error) {
        console.error('清空通知失败:', error)
      }
    }
  }
})
