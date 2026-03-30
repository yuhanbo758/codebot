import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { resolve } from 'path'

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': resolve(__dirname, 'src')
    }
  },
  server: {
    port: 3000,
    proxy: {
      '/api': {
        target: 'http://localhost:8080',
        changeOrigin: true
      },
      '/logo.ico': {
        target: 'http://localhost:8080',
        changeOrigin: true
      }
    }
  },
  build: {
    // 提高单 chunk 警告阈值（element-plus 本身约 1MB，无法更小）
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
          // highlight.js 核心 + 语言包（统一打包，按需加载由路由懒加载保证）
          if (id.includes('node_modules/highlight.js')) {
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
