/** @type {import('next').NextConfig} */
const nextConfig = {
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
