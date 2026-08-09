import { createApp } from 'vue'
import { createPinia } from 'pinia'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import * as ElementPlusIconsVue from '@element-plus/icons-vue'
import axios from 'axios'

import App from './App.vue'
import router from './router'

const app = createApp(App)
const pinia = createPinia()

// 局域网浏览器未配对时，由根组件统一展示配对界面，避免每个页面各自处理 401。
axios.defaults.withCredentials = true
axios.interceptors.response.use(
  response => response,
  error => {
    if (error?.response?.status === 401 && error?.response?.headers?.['x-codebot-pairing-required'] === '1') {
      window.dispatchEvent(new CustomEvent('codebot-auth-required'))
    }
    return Promise.reject(error)
  }
)

// 注册 Element Plus 图标
for (const [key, component] of Object.entries(ElementPlusIconsVue)) {
  app.component(key, component)
}

app.use(pinia)
app.use(router)
app.use(ElementPlus, {
  locale: zhCn
})

app.mount('#app')
