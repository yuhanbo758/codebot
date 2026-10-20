import { shallowRef } from 'vue'

const highlighter = shallowRef(null)
let loading

export function highlightCode(code, language, escapeHtml) {
  const engine = highlighter.value
  if (language && !engine && !loading) {
    // 首次遇到代码块才加载完整语言库；响应式引用使已显示的代码自动补上高亮。
    loading = import('highlight.js').then(module => {
      highlighter.value = module.default
    }).catch(error => {
      loading = null
      console.warn('代码高亮加载失败，将在下次渲染时重试', error)
    })
  }
  let html = escapeHtml(code)
  if (language && engine?.getLanguage(language)) {
    try {
      html = engine.highlight(code, { language, ignoreIllegals: true }).value
    } catch (_) {}
  }
  return `<pre class="hljs"><code>${html}</code></pre>`
}
