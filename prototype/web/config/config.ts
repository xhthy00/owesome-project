import { defineConfig } from '@umijs/max';
import settings from './defaultSettings';

export default defineConfig({
  title: settings.title,
  history: { type: 'hash' },
  hash: true,
  publicPath: process.env.NODE_ENV === 'production' ? './' : '/',
  npmClient: 'npm',
  fastRefresh: true,
  utoopack: {},
  antd: { configProvider: { variant: 'filled' } },
  layout: {
    ...settings,
    locale: false,
  },
  routes: [{
    path: '/',
    component: '@/workspace',
    routes: [
      { path: '/', redirect: '/overview' },
      { path: '/overview', name: '学情总览' },
      { path: '/analysis', name: '学情分析' },
      { path: '/benchmark', name: '达线看板' },
      { path: '/alerts/*', name: '异常提醒' },
      { path: '/resources', name: '数据管理' },
      { path: '/permissions', name: '权限管理' },
      { path: '/audit', name: '系统日志' },
      { path: '/chat', name: 'AI 问数' },
      { path: '/docs', name: '使用帮助' },
      { path: '*', redirect: '/overview' },
    ],
  }],
});
