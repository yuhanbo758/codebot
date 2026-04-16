<template>
  <div class="docs-view">
    <el-card shadow="never">
      <template #header>
        <div class="docs-header">
          <span>使用文档</span>
          <el-button size="small" @click="loadDoc" :loading="loading">刷新</el-button>
        </div>
      </template>

      <el-skeleton v-if="loading" :rows="8" animated />
      <el-empty v-else-if="!html" description="暂无文档内容" />
      <div v-else class="markdown-body docs-body" v-html="html"></div>
    </el-card>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import MarkdownIt from 'markdown-it'
import hljs from 'highlight.js'
import 'highlight.js/styles/github.css'

const md = new MarkdownIt({
  html: true,
  linkify: true,
  breaks: true,
  highlight: function (str, lang) {
    if (lang && hljs.getLanguage(lang)) {
      try {
        return '<pre class="hljs"><code>' +
               hljs.highlight(str, { language: lang, ignoreIllegals: true }).value +
               '</code></pre>'
      } catch (__) {}
    }
    return '<pre class="hljs"><code>' + md.utils.escapeHtml(str) + '</code></pre>'
  }
})

// Open links in external browser (Electron) or new tab (web)
md.renderer.rules.link_open = function (tokens, idx, options, env, self) {
  tokens[idx].attrSet('target', '_blank')
  tokens[idx].attrSet('rel', 'noopener noreferrer')
  return self.renderToken(tokens, idx, options)
}

// Add referrerpolicy="no-referrer" to all images so that CDNs with anti-hotlink
// (Referer-based) protection don't block images when the page is served from
// http://127.0.0.1 (Electron). Without this, the browser sends
// "Referer: http://127.0.0.1:8080/..." and the CDN rejects the request.
md.renderer.rules.image = function (tokens, idx, options, env, self) {
  const token = tokens[idx]
  // MarkdownIt stores the alt text as token children; render them as plain text
  const altIdx = token.attrIndex('alt')
  if (altIdx >= 0) {
    token.attrs[altIdx][1] = self.renderInlineAsText(token.children, options, env)
  }
  token.attrSet('referrerpolicy', 'no-referrer')
  token.attrSet('loading', 'lazy')
  return self.renderToken(tokens, idx, options)
}

const loading = ref(false)
const html = ref('')

const loadDoc = async () => {
  loading.value = true
  try {
    const res = await fetch('/api/docs/readme')
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const text = await res.text()
    html.value = md.render(text || '')
  } catch (e) {
    ElMessage.error(`加载文档失败: ${e.message || e}`)
  } finally {
    loading.value = false
  }
}

onMounted(loadDoc)
</script>

<style scoped>
.docs-view {
  padding: 20px;
}

.docs-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.docs-body {
  max-width: 960px;
  margin: 0 auto;
}

/* Markdown content styles */
.markdown-body {
  font-size: 14px;
  line-height: 1.6;
  color: #24292e;
}

.markdown-body :deep(h1),
.markdown-body :deep(h2),
.markdown-body :deep(h3),
.markdown-body :deep(h4),
.markdown-body :deep(h5),
.markdown-body :deep(h6) {
  font-weight: 600;
  line-height: 1.25;
  margin-top: 1.2em;
  margin-bottom: 0.6em;
  color: #1f2328;
}

.markdown-body :deep(h1) { font-size: 2em; border-bottom: 1px solid #d1d9e0; padding-bottom: 0.3em; }
.markdown-body :deep(h2) { font-size: 1.5em; border-bottom: 1px solid #d1d9e0; padding-bottom: 0.3em; }
.markdown-body :deep(h3) { font-size: 1.25em; }
.markdown-body :deep(h4) { font-size: 1em; }
.markdown-body :deep(h5) { font-size: 0.875em; }
.markdown-body :deep(h6) { font-size: 0.85em; color: #57606a; }

.markdown-body :deep(p) {
  margin: 0.6em 0;
}

.markdown-body :deep(a) {
  color: #0969da;
  text-decoration: none;
}
.markdown-body :deep(a:hover) {
  text-decoration: underline;
}

.markdown-body :deep(pre) {
  background-color: #282c34;
  color: #abb2bf;
  border-radius: 6px;
  padding: 12px 16px;
  overflow-x: auto;
  margin: 10px 0;
  font-size: 13px;
  line-height: 1.5;
}

.markdown-body :deep(code) {
  font-family: Consolas, Monaco, 'Andale Mono', 'Ubuntu Mono', monospace;
  background-color: rgba(175, 184, 193, 0.2);
  padding: 0.2em 0.4em;
  border-radius: 4px;
  font-size: 85%;
}

.markdown-body :deep(pre code) {
  background-color: transparent;
  padding: 0;
  border-radius: 0;
  color: inherit;
  font-size: 100%;
}

.markdown-body :deep(pre.hljs) {
  background-color: #f6f8fa;
  color: #24292e;
  border: 1px solid #d1d9e0;
}

.markdown-body :deep(ul),
.markdown-body :deep(ol) {
  padding-left: 24px;
  margin: 0.6em 0;
}

.markdown-body :deep(li) {
  margin: 0.2em 0;
}

.markdown-body :deep(blockquote) {
  margin: 0.6em 0;
  padding: 0 1em;
  border-left: 4px solid #d1d9e0;
  color: #57606a;
}

.markdown-body :deep(table) {
  border-collapse: collapse;
  width: 100%;
  margin: 0.8em 0;
  display: block;
  overflow-x: auto;
}

.markdown-body :deep(th),
.markdown-body :deep(td) {
  border: 1px solid #d1d9e0;
  padding: 6px 13px;
}

.markdown-body :deep(th) {
  background-color: #f6f8fa;
  font-weight: 600;
}

.markdown-body :deep(tr:nth-child(even)) {
  background-color: #f6f8fa;
}

.markdown-body :deep(img) {
  max-width: 100%;
  height: auto;
  border-radius: 4px;
  display: block;
  margin: 8px 0;
}

.markdown-body :deep(hr) {
  border: none;
  border-top: 1px solid #d1d9e0;
  margin: 1.5em 0;
}

.markdown-body :deep(strong) {
  font-weight: 600;
}
</style>
