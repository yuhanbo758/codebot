import test from 'node:test'
import assert from 'node:assert/strict'
import { watchEffect } from 'vue'
import { highlightCode } from '../src/utils/syntaxHighlight.js'

const escapeHtml = text => text.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;')

test('无语言标记和未知语言的代码保持 HTML 转义', () => {
  assert.equal(highlightCode('<script>', '', escapeHtml), '<pre class="hljs"><code>&lt;script&gt;</code></pre>')
  assert.equal(highlightCode('<script>', 'unknown-language', escapeHtml), '<pre class="hljs"><code>&lt;script&gt;</code></pre>')
})

test('完整语言库加载后会触发响应式重绘，保留语言别名支持', async () => {
  let stop
  try {
    await new Promise((resolve, reject) => {
      const timeout = setTimeout(() => reject(new Error('高亮加载后没有触发重绘')), 5000)
      stop = watchEffect(() => {
        const html = highlightCode('def hello():\n    return True', 'py', escapeHtml)
        if (html.includes('hljs-keyword')) {
          clearTimeout(timeout)
          resolve()
        }
      })
    })
  } finally {
    stop?.()
  }
})
