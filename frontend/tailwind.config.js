/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        hmi: {
          bg: '#12161b', panel: '#1b2129', panel2: '#222a33', line: '#2e3843', text: '#d9e1e8', dim: '#8a97a5',
          amber: '#f2b233', green: '#3fbf6b', red: '#e5484d', blue: '#3b82c4', cyan: '#36b3c9',
        },
      },
      fontFamily: { sans: ['"PingFang SC"', '"Microsoft YaHei"', '"Noto Sans SC"', 'system-ui', 'sans-serif'] },
    },
  },
  plugins: [],
}
