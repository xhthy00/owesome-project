import { createI18n } from 'vue-i18n'
import zhCN from './zh-CN.json'
import { getBrowserLocale } from '@/utils/utils'
import { useCache } from '@/utils/useCache'

const { wsCache } = useCache()

const getDefaultLocale = (): string => {
  return wsCache.get('user.language') || getBrowserLocale() || 'zh-CN'
}

export const i18n = createI18n({
  legacy: false,
  locale: getDefaultLocale(),
  fallbackLocale: 'zh-CN',
  globalInjection: true,
  messages: {
    'zh-CN': zhCN,
  },
})
