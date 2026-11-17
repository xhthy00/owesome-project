import { defineConfig } from '@umijs/max';
import settings from './defaultSettings';
import routes from './routes';

export default defineConfig({
  title: settings.title,
  history: { type: 'browser' },
  hash: true,
  utoopack: {},
  npmClient: 'npm',
  define: { 'process.env.DEFAULT_DATASOURCE_ID': process.env.DEFAULT_DATASOURCE_ID || '1' },
  antd: { configProvider: { variant: 'filled' } },
  layout: { ...settings, locale: false },
  proxy: {
    '/api/v1': {
      target: process.env.BACKEND_URL || 'http://127.0.0.1:8000',
      changeOrigin: true,
      proxyTimeout: 300_000,
    },
  },
  routes,
});
