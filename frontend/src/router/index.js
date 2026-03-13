import { createRouter, createWebHistory } from 'vue-router'

import MemoryView from '@/views/Memory.vue'
import ActiveMemoriesView from '@/components/ActiveMemories.vue'
import ArchivedMemoriesView from '@/components/ArchivedMemories.vue'
import BackupRestoreView from '@/components/BackupRestore.vue'
import MemoryConfigView from '@/components/MemoryConfig.vue'

const routes = [
  {
    path: '/',
    redirect: '/chat'
  },
  {
    path: '/chat',
    name: 'Chat',
    component: () => import('@/views/Chat.vue')
  },
  {
    path: '/memory',
    name: 'Memory',
    component: MemoryView,
    children: [
      {
        path: '',
        redirect: '/memory/active'
      },
      {
        path: 'active',
        name: 'ActiveMemories',
        component: ActiveMemoriesView
      },
      {
        path: 'archived',
        name: 'ArchivedMemories',
        component: ArchivedMemoriesView
      },
      {
        path: 'backup',
        name: 'MemoryBackup',
        component: BackupRestoreView
      },
      {
        path: 'config',
        name: 'MemoryConfig',
        component: MemoryConfigView
      }
    ]
  },
  {
    path: '/scheduler',
    name: 'Scheduler',
    component: () => import('@/views/Scheduler.vue')
  },
  {
    path: '/skills',
    name: 'Skills',
    component: () => import('@/views/Skills.vue')
  },
  {
    path: '/mcp',
    name: 'MCP',
    component: () => import('@/views/MCP.vue')
  },
  {
    path: '/logs',
    name: 'Logs',
    component: () => import('@/views/Logs.vue')
  },
  {
    path: '/settings',
    name: 'Settings',
    component: () => import('@/views/Settings.vue')
  }
]

const router = createRouter({
  history: createWebHistory(),
  routes
})

router.onError((error) => {
  const message = String(error?.message || '')
  const isChunkLoadError =
    message.includes('Failed to fetch dynamically imported module') ||
    message.includes('Importing a module script failed') ||
    message.includes('Loading chunk') ||
    message.includes('ChunkLoadError')
  if (isChunkLoadError) {
    window.location.reload()
  }
})

export default router
