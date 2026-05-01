import { LockOutlined, UserOutlined } from "@ant-design/icons";
import { Alert, Button, Form, Input, Typography, message, ConfigProvider } from "antd";
import { useRouter } from "next/router";
import { useEffect, useState } from "react";
import { login } from "@/api/auth";
import { setAccessToken } from "@/auth/session";
import Image from "next/image";

const { Title, Text } = Typography;

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
          <div className="w-full max-w-[1200px] flex flex-col lg:flex-row items-center gap-12">
            <div className="lg:w-1/2 text-center lg:text-left">
              <div className="mb-10">
                <Image
                  src="/logo-horizontal.svg"
                  alt="Logo"
                  width={640}
                  height={192}
                  className="drop-shadow-xl"
                  style={{ filter: "drop-shadow(0 4px 12px rgba(59, 130, 246, 0.3))" }}
                />
              </div>
              
              <div className="text-white space-y-6">
                <Title level={1} className="!text-white !text-5xl lg:!text-6xl !leading-tight">
                  启明数智Agent
                  <br />
                  <span className="bg-gradient-to-r from-cyan-300 to-blue-300 bg-clip-text text-transparent">
                    让洞察更简单
                  </span>
                </Title>
                
                <Text className="!text-white/80 !text-xl leading-relaxed block max-w-lg">
                  多Agent协同，将复杂的数据转化为直观洞察
                </Text>
                
                <div className="flex flex-wrap gap-4 justify-center lg:justify-start mt-8">
                  <div className="flex items-center gap-2 bg-white/10 backdrop-blur-md px-4 py-2 rounded-full border border-white/20">
                    <span className="w-2 h-2 rounded-full bg-green-400"></span>
                    <span className="text-white/90 text-sm">智能分析</span>
                  </div>
                  <div className="flex items-center gap-2 bg-white/10 backdrop-blur-md px-4 py-2 rounded-full border border-white/20">
                    <span className="w-2 h-2 rounded-full bg-blue-400"></span>
                    <span className="text-white/90 text-sm">安全可靠</span>
                  </div>
                  <div className="flex items-center gap-2 bg-white/10 backdrop-blur-md px-4 py-2 rounded-full border border-white/20">
                    <span className="w-2 h-2 rounded-full bg-purple-400"></span>
                    <span className="text-white/90 text-sm">自主规划</span>
                  </div>
                </div>
              </div>
            </div>
            
            <div className="lg:w-1/2 w-full flex justify-center">
              <div className="w-full max-w-md">
                <div className="bg-white/20 backdrop-blur-xl rounded-3xl p-10 shadow-2xl border border-white/30">
                  <div className="text-center mb-8">
                    <Title level={2} className="!mb-2 !text-white !text-2xl">
                      欢迎回来
                    </Title>
                    <Text className="!text-white/70">
                      立即开始您的高效数据之旅
                    </Text>
                  </div>

                  {errorMessage && (
                    <Alert
                      type="error"
                      message={errorMessage}
                      showIcon
                      className="mb-6 rounded-xl bg-white/10 border border-white/20 text-white backdrop-blur-md"
                    />
                  )}

                  <Form<LoginForm>
                    layout="vertical"
                    onFinish={handleSubmit}
                    initialValues={{ account: "admin", password: "" }}
                    className="space-y-5"
                  >
                    <Form.Item
                      name="account"
                      label={
                        <span className="text-white font-medium">
                          账号
                        </span>
                      }
                      rules={[{ required: true, message: "请输入账号" }]}
                    >
                      <Input
                        prefix={<UserOutlined className="text-white/50" />}
                        placeholder="请输入账号"
                        autoComplete="username"
                        size="large"
                        className="!bg-white/10 !border-white/20 !text-white placeholder:!text-white/40 !rounded-xl backdrop-blur-md focus:!border-white/50"
                      />
                    </Form.Item>
                    
                    <Form.Item
                      name="password"
                      label={
                        <span className="text-white font-medium">
                          密码
                        </span>
                      }
                      rules={[{ required: true, message: "请输入密码" }]}
                    >
                      <Input.Password
                        prefix={<LockOutlined className="text-white/50" />}
                        placeholder="请输入密码"
                        autoComplete="current-password"
                        size="large"
                        className="!bg-white/10 !border-white/20 !text-white placeholder:!text-white/40 !rounded-xl backdrop-blur-md focus:!border-white/50"
                      />
                    </Form.Item>
                    
                    <Form.Item className="!mb-0 pt-3">
                      <Button
                        type="primary"
                        htmlType="submit"
                        block
                        loading={loading}
                        size="large"
                        className="h-14 text-lg font-semibold rounded-xl bg-white !text-blue-600 hover:!bg-white/90 border-0 shadow-xl transition-all duration-300 hover:shadow-2xl hover:scale-[1.02]"
                      >
                        {loading ? "登录中..." : "登录"}
                      </Button>
                    </Form.Item>
                  </Form>
                  
                  <div className="mt-8 pt-6 border-t border-white/20">
                    <div className="mt-4 text-center">
                      <Text className="text-white/40 text-xs">
                        © 2026 扬州运河算力有限公司. All rights reserved.
                      </Text>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
        
        <style jsx global>{`
          @keyframes float {
            0%, 100% { transform: translateY(0px); }
            50% { transform: translateY(-20px); }
          }
        `}</style>
      </div>
    </ConfigProvider>
  );
}
