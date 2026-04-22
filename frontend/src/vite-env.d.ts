/// <reference types="vite/client" />
/// <reference types="vite-svg-loader" />

declare module '*.vue' {
  import type { DefineComponent } from 'vue'
  const component: DefineComponent<{}, {}, any>
  export default component
}

declare module 'web-storage-cache' {
  interface CacheOptions {
    storage?: 'localStorage' | 'sessionStorage'
  }
  export default class WebStorageCache {
    constructor(options?: CacheOptions)
    set(key: string, value: any, options?: { exp?: number }): void
    get(key: string): any
    delete(key: string): void
    deleteAllExpires(): void
    clear(): void
    add(key: string, value: any): void
    replace(key: string, value: any): void
    touch(key: string, expIn?: number): void
  }
}

interface ImportMetaEnv {
  readonly VITE_API_BASE_URL: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
