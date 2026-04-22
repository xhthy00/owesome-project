import dayjs from 'dayjs'
import { useCache } from '@/utils/useCache'

const { wsCache } = useCache()

export const getBrowserLocale = (): string => {
  const language = navigator.language
  if (!language) return 'zh-CN'
  if (language.toLowerCase().startsWith('zh')) return 'zh-CN'
  return 'zh-CN'
}

export const getLocale = (): string => {
  return wsCache.get('user.language') || getBrowserLocale() || 'zh-CN'
}

export const getDate = (time?: Date | string | number): Date | undefined => {
  if (!time) return undefined
  if (time instanceof Date) return time
  if (typeof time === 'string') return dayjs(time).toDate()
  return new Date(time)
}

export const datetimeFormat = (timestamp?: Date | string | number): string => {
  const dt = getDate(timestamp)
  if (!dt) return ''
  return dayjs(dt).format('YYYY-MM-DD HH:mm:ss')
}
