/** @type {import('next').NextConfig} */
const nextConfig = {
  // 关闭 Next 内置 gzip 压缩：next start 默认用 zlib gzip 压缩响应，而 gzip 是
  // 缓冲式压缩器，会把 rewrites 代理透传的 SSE 小帧攒到流结束才 flush，导致
  // chat-stream 等流式接口在线上“很久后一次性蹦出”。SSE 本就不应被压缩。
  // （若将来接入 Nginx，由 Nginx 对非 SSE 路径统一压缩更合适，此处保持 false。）
  compress: false,
  reactStrictMode: true,
  transpilePackages: [
    "antd",
    "@ant-design/icons",
    "@ant-design/icons-svg",
    "@rc-component/util",
    "rc-util",
    "rc-pagination",
    "rc-picker",
    "rc-tree",
    "rc-table",
    "rc-field-form",
    "rc-motion",
    "rc-select",
    "rc-input",
    "rc-textarea",
    "rc-dropdown",
    "rc-menu"
  ],
  async rewrites() {
    const backend = process.env.BACKEND_URL;
    if (!backend) {
      return [];
    }
    return [
      {
        source: "/api/:path*",
        destination: `${backend}/:path*`
      }
    ];
  }
};

module.exports = nextConfig;
