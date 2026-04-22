import WebStorageCache from 'web-storage-cache'

type CacheType = 'localStorage' | 'sessionStorage'

const PREFIX = 'awesome_v1_'

const NEED_PREFIX = new Set(['get', 'delete', 'touch', 'add', 'replace'])

export const useCache = (type: CacheType = 'localStorage') => {
  const original = new WebStorageCache({ storage: type })

  const wrapped = new Proxy(original, {
    get(target, prop) {
      const fn = (target as any)[prop]
      if (typeof fn !== 'function') return fn
      if (NEED_PREFIX.has(prop as string)) {
        return (key: string, ...rest: any[]) => fn.apply(target, [`${PREFIX}${key}`, ...rest])
      }
      if (prop === 'set') {
        return (key: string, value: any, ...rest: any[]) =>
          fn.apply(target, [`${PREFIX}${key}`, value, ...rest])
      }
      return fn.bind(target)
    },
  })

  return { wsCache: wrapped }
}
