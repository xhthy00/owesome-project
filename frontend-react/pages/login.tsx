import { LockOutlined, UserOutlined } from "@ant-design/icons";
import { Alert, Button, Form, Input, Typography, message, ConfigProvider } from "antd";
import { useRouter } from "next/router";
import { useEffect, useState } from "react";
import { login } from "@/api/auth";
import { setAccessToken } from "@/auth/session";

const { Text } = Typography;

type LoginForm = {
  account: string;
  password: string;
};

export default function LoginPage() {
  const router = useRouter();
  const [loading, setLoading] = useState(false);
  const [errorMessage, setErrorMessage] = useState("");

  useEffect(() => {
    const key = "auth_expired_tip";
    if (typeof window === "undefined") return;
    const shouldTip = window.sessionStorage.getItem(key) === "1";
    if (!shouldTip) return;
    window.sessionStorage.removeItem(key);
    void message.warning("登录已过期，请重新登录");
  }, []);

  const handleSubmit = async (values: LoginForm) => {
    try {
      setLoading(true);
      setErrorMessage("");
      const result = await login({
        username: values.account,
        password: values.password
      });
      setAccessToken(result.access_token);

      const rawRedirect = router.query.redirect;
      const redirect = Array.isArray(rawRedirect) ? rawRedirect[0] : rawRedirect;
      await router.replace(redirect || "/");
    } catch (error) {
      const msg = error instanceof Error ? error.message : "登录失败，请重试";
      setErrorMessage(msg);
    } finally {
      setLoading(false);
    }
  };

  return (
    <ConfigProvider
      theme={{
        token: {
          colorPrimary: "#3B82F6",
          colorPrimaryHover: "#60A5FA",
          borderRadius: 12,
        },
      }}
    >
      <div className="min-h-screen relative overflow-hidden">
        <div className="absolute inset-0 bg-gradient-to-br from-blue-600 via-blue-700 to-cyan-600" />
        
        <div className="absolute inset-0 backdrop-blur-[1px]">
          <div className="absolute top-[-10%] left-[-10%] w-[50%] h-[50%] rounded-full bg-gradient-to-br from-cyan-400 to-blue-500 opacity-50 blur-3xl animate-pulse" />
          <div className="absolute bottom-[-10%] right-[-10%] w-[60%] h-[60%] rounded-full bg-gradient-to-br from-blue-500 to-cyan-400 opacity-50 blur-3xl animate-pulse" style={{ animationDelay: "1s" }} />
          <div className="absolute top-[30%] right-[20%] w-[40%] h-[40%] rounded-full bg-gradient-to-br from-blue-400 to-cyan-500 opacity-40 blur-3xl animate-pulse" style={{ animationDelay: "2s" }} />
        </div>
        
        <div className="relative z-10 min-h-screen flex items-center justify-center p-6">
          <div className="w-full max-w-[1200px] flex flex-col lg:flex-row items-center gap-10 lg:gap-14">
            <div className="lg:w-1/2 text-center lg:text-left">
              <div className="text-white space-y-5 lg:space-y-6">
                <h1 className="m-0 text-5xl lg:text-6xl font-semibold leading-[1.35] tracking-tight">
                  <span className="slogan-line block">
                    学情看得见，
                  </span>
                  <span className="slogan-line mt-3 inline-block pl-[2em]">
                    考情更清晰
                  </span>
                </h1>

                <Text className="!text-white/80 !text-lg lg:!text-xl !leading-relaxed block max-w-lg">
                  面向学校与班级场景，一键生成学情诊断、考情分析与知识点掌握报告
                </Text>

                <div className="flex flex-wrap gap-3 justify-center lg:justify-start pt-1">
                  <div className="flex items-center gap-2 bg-white/10 backdrop-blur-md px-4 py-2 rounded-full border border-white/20">
                    <span className="w-2 h-2 rounded-full bg-emerald-400"></span>
                    <span className="text-white/90 text-sm">学情诊断</span>
                  </div>
                  <div className="flex items-center gap-2 bg-white/10 backdrop-blur-md px-4 py-2 rounded-full border border-white/20">
                    <span className="w-2 h-2 rounded-full bg-sky-300"></span>
                    <span className="text-white/90 text-sm">考情分析</span>
                  </div>
                  <div className="flex items-center gap-2 bg-white/10 backdrop-blur-md px-4 py-2 rounded-full border border-white/20">
                    <span className="w-2 h-2 rounded-full bg-amber-300"></span>
                    <span className="text-white/90 text-sm">知识点掌握</span>
                  </div>
                </div>
              </div>
            </div>

            <div className="lg:w-1/2 w-full flex justify-center lg:justify-end">
              <div className="w-full max-w-[420px]">
                <div className="login-card relative overflow-hidden rounded-[28px] px-9 py-11 sm:px-11 sm:py-12">
                  <div
                    className="pointer-events-none absolute inset-0 rounded-[28px]"
                    style={{
                      background:
                        "linear-gradient(160deg, rgba(255,255,255,0.28) 0%, rgba(255,255,255,0.12) 42%, rgba(255,255,255,0.08) 100%)",
                    }}
                  />
                  <div className="pointer-events-none absolute inset-px rounded-[27px] border border-white/35" />
                  <div className="pointer-events-none absolute inset-x-8 top-0 h-px bg-gradient-to-r from-transparent via-white/70 to-transparent" />

                  <div className="relative z-10">
                    <div className="mb-9 text-center">
                      <h2 className="m-0 text-white text-[20px] sm:text-[22px] font-semibold leading-snug tracking-wide">
                        <span className="block">扬州市学情/考情分析</span>
                        <span className="block mt-1.5 text-[18px] sm:text-[20px] font-medium text-white/95">
                          智能体
                        </span>
                      </h2>
                      <div className="mx-auto mt-4 h-[3px] w-12 rounded-full bg-gradient-to-r from-cyan-200/80 to-white/70" />
                    </div>

                    {errorMessage && (
                      <Alert
                        type="error"
                        message={errorMessage}
                        showIcon
                        className="mb-6 rounded-2xl bg-white/15 border border-white/25 text-white backdrop-blur-md"
                      />
                    )}

                    <Form<LoginForm>
                      layout="vertical"
                      onFinish={handleSubmit}
                      initialValues={{ account: "", password: "" }}
                      className="login-form"
                      requiredMark={false}
                    >
                      <Form.Item
                        name="account"
                        label={<span className="text-white/90 text-sm font-medium tracking-wide">账号</span>}
                        rules={[{ required: true, message: "请输入账号" }]}
                        className="!mb-5"
                      >
                        <Input
                          prefix={<UserOutlined className="text-white/55" />}
                          placeholder="请输入账号"
                          autoComplete="username"
                          size="large"
                          className="login-input"
                        />
                      </Form.Item>

                      <Form.Item
                        name="password"
                        label={<span className="text-white/90 text-sm font-medium tracking-wide">密码</span>}
                        rules={[{ required: true, message: "请输入密码" }]}
                        className="!mb-7"
                      >
                        <Input.Password
                          prefix={<LockOutlined className="text-white/55" />}
                          placeholder="请输入密码"
                          autoComplete="current-password"
                          size="large"
                          className="login-input"
                        />
                      </Form.Item>

                      <Form.Item className="!mb-0">
                        <Button
                          type="primary"
                          htmlType="submit"
                          block
                          loading={loading}
                          size="large"
                          className="login-submit h-[52px] text-[17px] font-semibold rounded-2xl border-0 shadow-[0_10px_28px_rgba(15,60,140,0.22)] transition-all duration-300 hover:-translate-y-0.5 hover:shadow-[0_14px_32px_rgba(15,60,140,0.28)]"
                        >
                          {loading ? "登录中..." : "登录"}
                        </Button>
                      </Form.Item>
                    </Form>

                    <div className="mt-9 pt-5 border-t border-white/15 text-center">
                      <Text className="!text-white/45 text-xs tracking-wide">
                        © 2026 扬州市电化教育馆 版权所有
                      </Text>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
        
        <style jsx global>{`
          .login-card {
            backdrop-filter: blur(22px);
            -webkit-backdrop-filter: blur(22px);
            background: rgba(255, 255, 255, 0.14);
            box-shadow:
              0 24px 60px rgba(15, 45, 110, 0.28),
              inset 0 1px 0 rgba(255, 255, 255, 0.35);
          }

          .login-form .ant-form-item-label > label {
            height: auto;
          }

          .login-form .ant-form-item-explain-error {
            color: #fecaca;
          }

          .login-input.ant-input-affix-wrapper,
          .login-input.ant-input-affix-wrapper-lg {
            min-height: 48px;
            padding: 0 14px;
            border-radius: 14px !important;
            background: rgba(255, 255, 255, 0.12) !important;
            border: 1px solid rgba(255, 255, 255, 0.28) !important;
            box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.18);
            transition: border-color 0.2s ease, background 0.2s ease, box-shadow 0.2s ease;
          }

          .login-input.ant-input-affix-wrapper:hover,
          .login-input.ant-input-affix-wrapper-focused,
          .login-input.ant-input-affix-wrapper:focus-within {
            background: rgba(255, 255, 255, 0.18) !important;
            border-color: rgba(255, 255, 255, 0.55) !important;
            box-shadow:
              inset 0 1px 0 rgba(255, 255, 255, 0.22),
              0 0 0 3px rgba(147, 197, 253, 0.22);
          }

          .login-input .ant-input {
            background: transparent !important;
            color: #fff !important;
            font-size: 15px;
          }

          .login-input .ant-input::placeholder {
            color: rgba(255, 255, 255, 0.42) !important;
          }

          .login-input .ant-input-password-icon,
          .login-input .anticon {
            color: rgba(255, 255, 255, 0.55) !important;
          }

          .login-input .ant-input-password-icon:hover {
            color: rgba(255, 255, 255, 0.85) !important;
          }

          .login-submit.ant-btn {
            background: linear-gradient(180deg, #ffffff 0%, #f3f8ff 100%) !important;
            color: #1d4ed8 !important;
          }

          .login-submit.ant-btn:hover,
          .login-submit.ant-btn:focus {
            background: linear-gradient(180deg, #ffffff 0%, #e8f1ff 100%) !important;
            color: #1e40af !important;
          }

          .slogan-line {
            background-image: linear-gradient(
              105deg,
              #ffffff 0%,
              #e0f2fe 28%,
              #7dd3fc 62%,
              #93c5fd 100%
            );
            -webkit-background-clip: text;
            background-clip: text;
            -webkit-text-fill-color: transparent;
            color: transparent;
          }
        `}</style>
      </div>
    </ConfigProvider>
  );
}
