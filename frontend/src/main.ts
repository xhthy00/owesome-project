import { createApp } from 'vue'
import App from './App.vue'
import router from './router'
import { setupStore } from './stores'
import { i18n } from './i18n'
import 'element-plus/dist/index.css'
import './style.less'

const app = createApp(App)
setupStore(app)
app.use(router)
app.use(i18n)
app.mount('#app')
