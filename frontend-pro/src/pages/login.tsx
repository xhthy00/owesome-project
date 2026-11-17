import { ArrowRightOutlined, LockOutlined, QuestionCircleOutlined, UserOutlined } from '@ant-design/icons';
import { Alert, Button, Card, Flex, Form, Input, Popover, Typography, theme } from 'antd';
import { history, useLocation } from '@umijs/max';
import { useEffect, useState } from 'react';
import { login } from '@/api/auth';
import { clearAuthSession, setAccessToken } from '@/auth/session';
import BrandLogo from '@/components/BrandLogo';
import settings from '../../config/defaultSettings';

type LoginValues = { username: string; password: string };

export default function Login() {
  const { search } = useLocation();
  const { token } = theme.useToken();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [expired, setExpired] = useState(false);
  const [helpOpen, setHelpOpen] = useState(false);

  // 401 跳转过来时给出“登录已过期”的提示，读一次就清掉。
  useEffect(() => {
    try {
      if (sessionStorage.getItem('auth_expired_tip')) {
        setExpired(true);
        sessionStorage.removeItem('auth_expired_tip');
      }
    } catch {
      /* 隐私模式下 sessionStorage 可能不可用，忽略 */
    }
  }, []);

  async function submit(values: LoginValues) {
    setBusy(true);
    setError('');
    setExpired(false);
    try {
      const session = await login(values);
      clearAuthSession();
      setAccessToken(session.access_token);
      const redirect = new URLSearchParams(search).get('redirect');
      const safe = redirect && /^\/(?!\/|\\)/.test(redirect) && !redirect.startsWith('/login');
      history.replace(safe ? redirect : '/chat');
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '登录失败，请重试');
    } finally {
      setBusy(false);
    }
  }

  return <main className="login-page" style={{ background: token.colorBgContainer }}>
    <div
      className="login-visual"
      aria-hidden="true"
      style={{ backgroundColor: token.colorFillSecondary, borderRadius: token.borderRadiusLG, margin: token.margin }}
    />

    <section
      className="login-panel"
      aria-labelledby="login-heading"
      style={{
        padding: token.paddingLG,
        backgroundColor: token.colorBgContainer,
        backgroundImage: [
          `radial-gradient(ellipse at 90% 7%, ${token.colorPrimaryBg} 0%, transparent 62%)`,
          `radial-gradient(ellipse at 0% 88%, ${token.colorSuccessBg} 0%, transparent 54%)`,
        ].join(', '),
      }}
    >
      <div className="login-daylight" aria-hidden="true" style={{ backgroundColor: token.colorBgContainer }} />
      <Flex className="login-content" vertical gap={token.marginLG}>
        <Card
          className="login-glass"
          style={{
            background: `color-mix(in srgb, ${token.colorBgContainer} 66%, transparent)`,
            borderColor: `color-mix(in srgb, ${token.colorBgContainer} 82%, ${token.colorPrimaryBorder})`,
            borderRadius: token.borderRadiusLG * 2,
            boxShadow: `0 20px 54px -28px ${token.colorPrimaryBorderHover}, inset 0 1px 0 ${token.colorBgContainer}`,
          }}
          styles={{ body: { padding: `clamp(${token.paddingLG}px, 5vw, ${token.paddingXL + token.paddingXS}px)` } }}
        >
          <Flex vertical gap={token.marginLG}>
            {/* 卡片头：logo + 品牌字（渐变压字 + 紧字距）+ 宽字距标语 */}
            <Flex vertical align="center" gap={10}>
              <Flex align="center" justify="center" gap={token.marginSM}>
                <BrandLogo size={44} />
                <span id="login-heading" className="login-brand-name">{settings.title}</span>
              </Flex>
              <Flex align="center" gap={10} className="login-tagline">
                <span className="login-tagline-rule" aria-hidden="true" />
                <span className="login-tagline-text">以数据洞察学情，用 AI 辅助教学决策</span>
                <span className="login-tagline-rule login-tagline-rule-end" aria-hidden="true" />
              </Flex>
            </Flex>

            {expired && <Alert type="warning" showIcon title="登录状态已过期，请重新登录" />}
            {error && <Alert type="error" showIcon title={error} />}

            <Form<LoginValues>
              name="login"
              layout="vertical"
              size="large"
              requiredMark={false}
              aria-labelledby="login-heading"
              aria-busy={busy}
              onFinish={submit}
              disabled={busy}
            >
              <Form.Item name="username" label="账号" rules={[{ required: true, message: '请输入账号' }]}>
                <Input variant="outlined" placeholder="请输入账号"
                  prefix={<UserOutlined aria-hidden="true" style={{ color: token.colorTextTertiary }} />}
                  autoComplete="username" autoCapitalize="none" />
              </Form.Item>
              <Form.Item name="password" label="密码" rules={[{ required: true, message: '请输入密码' }]}>
                <Input.Password variant="outlined" placeholder="请输入密码"
                  prefix={<LockOutlined aria-hidden="true" style={{ color: token.colorTextTertiary }} />}
                  autoComplete="current-password" />
              </Form.Item>
              <Button type="primary" htmlType="submit" block loading={busy}
                icon={<ArrowRightOutlined aria-hidden="true" />} iconPlacement="end">
                登录
              </Button>
            </Form>
          </Flex>
        </Card>

        <Flex justify="center">
          <Popover
            trigger="click"
            placement="top"
            open={helpOpen}
            onOpenChange={setHelpOpen}
            title="登录帮助"
            content={<Typography.Text id="login-help-copy" style={{ display: 'block', maxWidth: 240 }}>
              忘记密码或无法登录，请联系学校系统管理员。
            </Typography.Text>}
          >
            <Button
              type="text"
              icon={<QuestionCircleOutlined aria-hidden="true" />}
              aria-expanded={helpOpen}
              aria-describedby={helpOpen ? 'login-help-copy' : undefined}
              onKeyDown={(event) => {
                if (event.key === 'Escape') setHelpOpen(false);
              }}
            >
              登录帮助
            </Button>
          </Popover>
        </Flex>
      </Flex>
    </section>
  </main>;
}
