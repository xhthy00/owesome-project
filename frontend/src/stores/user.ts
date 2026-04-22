import { defineStore } from 'pinia'
import { store } from './index'
import { useCache } from '@/utils/useCache'
import { i18n } from '@/i18n'

const { wsCache } = useCache()

interface UserState {
  token: string
  name: string
  language: string
}

export const UserStore = defineStore('user', {
  state: (): UserState => ({
    token: wsCache.get('user.token') || '',
    name: wsCache.get('user.name') || 'Administrator',
    language: wsCache.get('user.language') || 'zh-CN',
  }),
  getters: {
    getToken: (s) => s.token,
    getName: (s) => s.name,
    getLanguage: (s) => s.language,
  },
  actions: {
    setToken(token: string) {
      wsCache.set('user.token', token)
      this.token = token
    },
    setName(name: string) {
      wsCache.set('user.name', name)
      this.name = name
    },
    setLanguage(language: string) {
      wsCache.set('user.language', language)
      this.language = language
      i18n.global.locale.value = language as any
    },
    clear() {
      ;['token', 'name', 'language'].forEach((k) => wsCache.delete('user.' + k))
      this.$reset()
    },
  },
})

export const useUserStore = () => UserStore(store)
