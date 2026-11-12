const defaultTheme = require("tailwindcss/defaultTheme");

/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    "./pages/**/*.{js,ts,jsx,tsx}",
    "./src/**/*.{js,ts,jsx,tsx}"
  ],
  theme: {
    extend: {
      fontFamily: {
        // 与 styles/tokens.css 的 --font-sans 一致:中文优先,不再声明未加载的 Josefin Sans
        sans: [
          "-apple-system",
          "BlinkMacSystemFont",
          '"Segoe UI"',
          '"PingFang SC"',
          '"Hiragino Sans GB"',
          '"Microsoft YaHei"',
          '"Noto Sans SC"',
          ...defaultTheme.fontFamily.sans
        ]
      },
      colors: {
        // 语义色,直接映射到 tokens.css;新代码用这些,别写十六进制
        brand: {
          DEFAULT: "var(--color-brand-solid)",
          hover: "var(--color-brand-solid-hover)",
          ink: "var(--color-brand-ink)",
          tint: "var(--color-brand-tint)",
          border: "var(--color-brand-tint-border)"
        },
        surface: {
          0: "var(--color-surface-0)",
          1: "var(--color-surface-1)",
          2: "var(--color-surface-2)"
        },
        line: {
          1: "var(--color-border-1)",
          2: "var(--color-border-2)"
        },
        ink: {
          1: "var(--color-text-1)",
          2: "var(--color-text-2)",
          3: "var(--color-text-3)",
          4: "var(--color-text-4)"
        },
        accent: {
          blue: {
            DEFAULT: "var(--accent-blue)",
            tint: "var(--accent-blue-tint)",
            border: "var(--accent-blue-border)"
          },
          green: {
            DEFAULT: "var(--accent-green)",
            tint: "var(--accent-green-tint)",
            border: "var(--accent-green-border)"
          },
          violet: {
            DEFAULT: "var(--accent-violet)",
            tint: "var(--accent-violet-tint)",
            border: "var(--accent-violet-border)"
          },
          amber: {
            DEFAULT: "var(--accent-amber)",
            tint: "var(--accent-amber-tint)",
            border: "var(--accent-amber-border)"
          }
        },
        // 旧键保留,避免打断既有页面
        theme: {
          primary: "var(--color-brand-solid)",
          light: "#f8fafc",
          dark: "#0f172a",
          "dark-container": "#1e293b"
        },
        gradientL: "#00DAEF",
        gradientR: "#105EFF"
      },
      borderRadius: {
        xs: "var(--radius-xs)",
        sm: "var(--radius-sm)",
        md: "var(--radius-md)",
        lg: "var(--radius-lg)",
        xl: "var(--radius-xl)"
      },
      boxShadow: {
        1: "var(--shadow-1)",
        2: "var(--shadow-2)",
        3: "var(--shadow-3)"
      },
      backgroundImage: {
        "gradient-light": "linear-gradient(180deg, #f8fbff 0%, #edf3ff 100%)",
        "gradient-dark": "linear-gradient(180deg, #191c2b 0%, #121522 100%)",
        "button-gradient": 'linear-gradient(to right, theme("colors.gradientL"), theme("colors.gradientR"))'
      }
    }
  },
  // 不给每条 utility 加 !important(那会和自定义样式打 !important 战争),
  // 而是把 utility 统一限定在 body 后代下:特异性 (0,1,1) 略高于 AntD 的
  // ':where(.hash).ant-x' (0,1,0),既能稳定压过组件库默认样式,
  // 又能让自定义样式、inline style 正常参与覆盖;弹层挂在 body 上也能命中。
  important: "body",
  darkMode: "class",
  plugins: []
};
