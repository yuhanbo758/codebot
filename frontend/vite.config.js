import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { resolve } from 'path'

const backendTarget = process.env.VITE_CODEBOT_BACKEND_URL || process.env.CODEBOT_BACKEND_URL || 'http://localhost:18080'

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': resolve(import.meta.dirname, 'src')
    }
  },
  server: {
    port: 3000,
    proxy: {
      '/api': {
        target: backendTarget,
        changeOrigin: true
      },
      '/logo.ico': {
        target: backendTarget,
        changeOrigin: true
      }
    }
  },
  build: {
    // 完整语法高亮库按需加载，单独保留以复用全部语言支持。
    chunkSizeWarningLimit: 1100,
    rollupOptions: {
      output: {
        manualChunks(id) {
          // Vue 核心生态
          if (id.includes('node_modules/vue/') ||
              id.includes('node_modules/@vue/') ||
              id.includes('node_modules/vue-router/') ||
              id.includes('node_modules/pinia/')) {
            return 'vendor-vue'
          }
          // Element Plus 图标（体积小但条目多，独立分包避免污染主包）
          if (id.includes('node_modules/@element-plus/icons-vue')) {
            return 'vendor-el-icons'
          }
          // Element Plus 组件库
          if (id.includes('node_modules/element-plus')) {
            return 'vendor-element-plus'
          }
          // 样式不能合入语言库的 JS chunk，否则静态 CSS 导入会提前拉取整个语言库。
          if (id.includes('node_modules/highlight.js') && !id.includes('/styles/')) {
            return 'vendor-highlight'
          }
          // markdown-it
          if (id.includes('node_modules/markdown-it') ||
              id.includes('node_modules/mdurl') ||
              id.includes('node_modules/linkify-it') ||
              id.includes('node_modules/uc.micro') ||
              id.includes('node_modules/entities')) {
            return 'vendor-markdown'
          }
          // axios
          if (id.includes('node_modules/axios')) {
            return 'vendor-axios'
          }
        }
      }
    }
  }
})
