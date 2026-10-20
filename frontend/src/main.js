import { createApp, h } from 'vue'
import { createPinia } from 'pinia'
import { ElConfigProvider } from 'element-plus'
import element from './plugins/element'
import 'element-plus/dist/index.css'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import axios from 'axios'

import App from './App.vue'
import router from './router'

const app = createApp({
  render: () => h(ElConfigProvider, { locale: zhCn }, () => h(App))
})
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

app.use(pinia)
app.use(router)
app.use(element)

app.mount('#app')
