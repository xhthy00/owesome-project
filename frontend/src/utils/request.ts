import axios, {
  AxiosError,
  type AxiosInstance,
  type AxiosRequestConfig,
  type AxiosResponse,
  type InternalAxiosRequestConfig,
  type CancelTokenSource,
} from 'axios'
import { ElMessage } from 'element-plus'
import { useCache } from '@/utils/useCache'
import { getLocale } from '@/utils/utils'

const { wsCache } = useCache()

export interface ApiResponse<T = unknown> {
  code: number
  data: T
  message: string
  [key: string]: any
}

export interface RequestOptions {
  silent?: boolean
  rawResponse?: boolean
  customError?: boolean
  retryCount?: number
}

export interface FullRequestConfig extends AxiosRequestConfig {
  requestOptions?: RequestOptions
}

class HttpService {
  private instance: AxiosInstance
  private cancelTokenSource: CancelTokenSource

  constructor(config?: AxiosRequestConfig) {
    this.cancelTokenSource = axios.CancelToken.source()
    this.instance = axios.create({
      baseURL: import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000/api/v1',
      timeout: 100000,
      headers: {
        'Content-Type': 'application/json',
        ...config?.headers,
      },
      ...config,
    })
    this.setupInterceptors()
  }

  private setupInterceptors() {
    this.instance.interceptors.request.use(
      (config: InternalAxiosRequestConfig) => {
        const token = wsCache.get('user.token')
        if (token && config.headers) {
          config.headers['Authorization'] = `Bearer ${token}`
        }
        const locale = getLocale()
        if (locale && config.headers) {
          config.headers['Accept-Language'] = locale
        }
        return config
      },
      (error) => Promise.reject(error)
    )

    this.instance.interceptors.response.use(
      (response: AxiosResponse) => {
        if ((response.config as FullRequestConfig).requestOptions?.rawResponse) {
          return response
        }
        // Backend convention: { code, message, data }, code === 200 means success
        const body = response.data
        if (body && typeof body === 'object' && 'code' in body) {
          if (body.code === 200) return body.data
          return Promise.reject(body)
        }
        return body
      },
      async (error: AxiosError) => {
        const cfg = error.config as FullRequestConfig | undefined
        const opts = cfg?.requestOptions || {}
        if (!opts.customError && !opts.silent) {
          this.handleError(error)
        }
        return Promise.reject(error)
      }
    )
  }

  private handleError(error: AxiosError) {
    let msg = 'Request error'
    if (error.response) {
      const status = error.response.status
      const data: any = error.response.data
      msg = data?.message || data?.detail || `Server error: ${status}`
      if (status === 401) {
        msg = data?.message || 'Unauthorized'
        wsCache.delete('user.token')
      }
    } else if (error.request) {
      msg = 'No response from server'
    } else if (axios.isCancel(error)) {
      return
    } else {
      msg = error.message || 'Unknown error'
    }
    ElMessage({ message: msg, type: 'error', showClose: true })
  }

  public cancelRequests(message?: string) {
    this.cancelTokenSource.cancel(message)
    this.cancelTokenSource = axios.CancelToken.source()
  }

  public request<T = any>(config: FullRequestConfig): Promise<T> {
    return this.instance.request({ cancelToken: this.cancelTokenSource.token, ...config })
  }

  public get<T = any>(url: string, config?: FullRequestConfig): Promise<T> {
    return this.request({ ...config, method: 'GET', url })
  }

  public post<T = any>(url: string, data?: any, config?: FullRequestConfig): Promise<T> {
    return this.request({ ...config, method: 'POST', url, data })
  }

  public put<T = any>(url: string, data?: any, config?: FullRequestConfig): Promise<T> {
    return this.request({ ...config, method: 'PUT', url, data })
  }

  public delete<T = any>(url: string, config?: FullRequestConfig): Promise<T> {
    return this.request({ ...config, method: 'DELETE', url })
  }
}

export const request = new HttpService()
