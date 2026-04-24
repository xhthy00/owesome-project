<script lang="ts" setup>
import { computed } from 'vue'
import MarkdownIt from 'markdown-it'
import linkAttributes from 'markdown-it-link-attributes'
import hljs from 'highlight.js/lib/core'
import javascript from 'highlight.js/lib/languages/javascript'
import typescript from 'highlight.js/lib/languages/typescript'
import python from 'highlight.js/lib/languages/python'
import sql from 'highlight.js/lib/languages/sql'
import json from 'highlight.js/lib/languages/json'
import bash from 'highlight.js/lib/languages/bash'
import yaml from 'highlight.js/lib/languages/yaml'
import xml from 'highlight.js/lib/languages/xml'

hljs.registerLanguage('javascript', javascript)
hljs.registerLanguage('typescript', typescript)
hljs.registerLanguage('python', python)
hljs.registerLanguage('sql', sql)
hljs.registerLanguage('json', json)
hljs.registerLanguage('bash', bash)
hljs.registerLanguage('yaml', yaml)
hljs.registerLanguage('html', xml)
hljs.registerLanguage('xml', xml)

const props = defineProps<{ content?: string }>()

const safeJsonPretty = (raw: string): string => {
  try {
    return JSON.stringify(JSON.parse(raw), null, 2)
  } catch {
    return raw
  }
}

const normalizeThinkTags = (raw?: string): string => {
  if (!raw) return ''
  // 把 <think>...</think> 转成自定义 fenced block，避免原始标签直接露出。
  const withBlocks = raw.replace(/<think>([\s\S]*?)<\/think>/gi, (_m, inner: string) => {
    const text = String(inner || '').trim()
    if (!text) return ''
    return `\n\`\`\`vis-thinking\n${text}\n\`\`\`\n`
  })
  // 兜底清理残留的孤立标签。
  return withBlocks.replace(/<\/?think>/gi, '')
}

const renderVisBlock = (lang: string, content: string): string | null => {
  const escaped = md.utils.escapeHtml(content)
  if (lang === 'vis-thinking') {
    return `
      <details class="vis-thinking-block">
        <summary class="vis-block-title">思考过程</summary>
        <pre>${escaped}</pre>
      </details>
    `
  }
  if (lang === 'vis-chart') {
    const pretty = md.utils.escapeHtml(safeJsonPretty(content))
    return `
      <div class="vis-chart-block">
        <div class="vis-block-title">图表配置</div>
        <pre>${pretty}</pre>
      </div>
    `
  }
  if (lang === 'agent-plans' || lang === 'agent-messages') {
    const pretty = md.utils.escapeHtml(safeJsonPretty(content))
    return `
      <div class="vis-agent-block">
        <div class="vis-block-title">${lang}</div>
        <pre>${pretty}</pre>
      </div>
    `
  }
  return null
}

const md = new MarkdownIt({
  html: false,
  linkify: true,
  breaks: true,
  highlight(str: string, lang: string): string {
    if (lang && hljs.getLanguage(lang)) {
      try {
        const out = hljs.highlight(str, { language: lang, ignoreIllegals: true }).value
        return `<pre class="hljs-pre"><code class="hljs language-${lang}">${out}</code></pre>`
      } catch {
        // fall through
      }
    }
    return `<pre class="hljs-pre"><code class="hljs">${md.utils.escapeHtml(str)}</code></pre>`
  },
})

md.use(linkAttributes, {
  attrs: { target: '_blank', rel: 'noopener noreferrer' },
})

const defaultFence = md.renderer.rules.fence
md.renderer.rules.fence = (tokens, idx, options, env, slf) => {
  const token = tokens[idx]
  const lang = (token.info || '').trim().toLowerCase()
  const custom = renderVisBlock(lang, token.content)
  if (custom) return custom
  if (defaultFence) return defaultFence(tokens, idx, options, env, slf)
  return slf.renderToken(tokens, idx, options)
}

const html = computed(() => md.render(normalizeThinkTags(props.content)))
</script>

<template>
  <div class="markdown-body" v-html="html" />
</template>

<style lang="less">
.markdown-body {
  font-size: 14px;
  line-height: 1.7;
  color: #1f2329;
  word-break: break-word;
  word-wrap: break-word;

  > *:first-child {
    margin-top: 0;
  }

  > *:last-child {
    margin-bottom: 0;
  }

  p {
    margin: 8px 0;
  }

  h1, h2, h3, h4, h5, h6 {
    margin: 16px 0 8px;
    font-weight: 600;
    color: #101828;
  }

  h1 { font-size: 20px; }
  h2 { font-size: 18px; }
  h3 { font-size: 16px; }
  h4 { font-size: 14px; }

  ul, ol {
    padding-left: 24px;
    margin: 8px 0;
  }

  li {
    margin: 4px 0;
  }

  a {
    color: var(--el-color-primary);
    text-decoration: none;

    &:hover {
      text-decoration: underline;
    }
  }

  blockquote {
    margin: 8px 0;
    padding: 4px 12px;
    color: #475467;
    border-left: 3px solid var(--el-color-primary-light-7);
    background: var(--el-color-primary-light-9);
    border-radius: 0 6px 6px 0;
  }

  code:not(.hljs) {
    background: rgba(31, 35, 41, 0.06);
    color: #d6336c;
    padding: 0.1em 0.4em;
    border-radius: 4px;
    font-family: 'SFMono-Regular', Consolas, Menlo, monospace;
    font-size: 12.5px;
  }

  pre.hljs-pre {
    margin: 10px 0;
    padding: 12px 14px;
    background: #1e1e1e;
    color: #d4d4d4;
    border-radius: 8px;
    overflow-x: auto;
    font-family: 'SFMono-Regular', Consolas, Menlo, monospace;
    font-size: 12.5px;
    line-height: 1.55;

    code {
      background: transparent;
      padding: 0;
      color: inherit;
    }
  }

  table {
    border-collapse: collapse;
    margin: 10px 0;
    font-size: 13px;
    width: 100%;

    th, td {
      border: 1px solid #e4e7ec;
      padding: 6px 10px;
      text-align: left;
    }

    th {
      background: #f5f7fb;
      font-weight: 600;
    }

    tr:nth-child(even) td {
      background: #fafbfc;
    }
  }

  hr {
    margin: 12px 0;
    border: none;
    border-top: 1px solid #e4e7ec;
  }

  // hljs minimal one-dark theme
  .hljs-keyword,
  .hljs-selector-tag,
  .hljs-built_in {
    color: #c678dd;
  }
  .hljs-string,
  .hljs-attr,
  .hljs-template-tag,
  .hljs-template-variable,
  .hljs-symbol,
  .hljs-bullet {
    color: #98c379;
  }
  .hljs-number,
  .hljs-literal {
    color: #d19a66;
  }
  .hljs-title,
  .hljs-section,
  .hljs-name {
    color: #61afef;
  }
  .hljs-comment,
  .hljs-quote {
    color: #7f848e;
    font-style: italic;
  }
  .hljs-variable,
  .hljs-regexp,
  .hljs-link {
    color: #e06c75;
  }
  .hljs-type,
  .hljs-class .hljs-title {
    color: #e5c07b;
  }

  .vis-thinking-block,
  .vis-chart-block,
  .vis-agent-block {
    margin: 10px 0;
    border: 1px solid var(--border-color-light);
    border-radius: 10px;
    background: #fafbfc;
    overflow: hidden;

    .vis-block-title {
      height: 30px;
      display: flex;
      align-items: center;
      padding: 0 10px;
      font-size: 12px;
      font-weight: 600;
      color: #475467;
      background: #f2f4f7;
      border-bottom: 1px solid var(--border-color-light);
      text-transform: uppercase;
      letter-spacing: 0.2px;
    }

    pre {
      margin: 0;
      padding: 10px 12px;
      overflow-x: auto;
      font-family: 'SFMono-Regular', Consolas, Menlo, monospace;
      font-size: 12px;
      line-height: 1.55;
      color: #1f2937;
      background: #fff;
    }
  }

  .vis-thinking-block {
    border-color: var(--el-color-primary-light-7);
    background: var(--el-color-primary-light-9);

    .vis-block-title {
      cursor: pointer;
      user-select: none;
      list-style: none;
    }

    .vis-block-title::-webkit-details-marker {
      display: none;
    }

    .vis-block-title::before {
      content: '';
      width: 0;
      height: 0;
      border-left: 4px solid transparent;
      border-right: 4px solid transparent;
      border-top: 6px solid #98a2b3;
      margin-right: 8px;
      transform: rotate(-90deg);
      transition: transform 0.15s ease;
    }

    &[open] .vis-block-title::before {
      transform: rotate(0deg);
    }

    &:not([open]) pre {
      display: none;
    }
  }
}
</style>
