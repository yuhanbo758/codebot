import { createRouter, createWebHistory } from 'vue-router'

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
    component: () => import('@/views/Memory.vue'),
    children: [
      {
        path: '',
        redirect: '/memory/active'
      },
      {
        path: 'active',
        name: 'ActiveMemories',
        component: () => import('@/components/ActiveMemories.vue')
      },
      {
        path: 'search',
        name: 'MemorySearch',
        component: () => import('@/components/MemorySearch.vue')
      },
      {
        path: 'archived',
        name: 'ArchivedMemories',
        component: () => import('@/components/ArchivedMemories.vue')
      },
      {
        path: 'backup',
        name: 'MemoryBackup',
        component: () => import('@/components/BackupRestore.vue')
      },
      {
        path: 'config',
        name: 'MemoryConfig',
        component: () => import('@/components/MemoryConfig.vue')
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
