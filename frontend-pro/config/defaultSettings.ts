import type { ProLayoutProps } from '@ant-design/pro-components';

/** 问数模块在菜单、路由、跳转文案里统一使用的名字，改名只需改这一处。 */
export const CHAT_MENU_TITLE = 'AI 学情分析';

const settings = {
  title: '学情智诊-AI分析平台',
  colorPrimary: '#1677ff',
  layout: 'mix',
  navTheme: 'light',
  contentWidth: 'Fluid',
  fixedHeader: false,
  fixSiderbar: true,
  siderWidth: 256,
} satisfies ProLayoutProps;

export default settings;
