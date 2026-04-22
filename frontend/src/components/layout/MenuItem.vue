<script lang="ts" setup>
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ChatDotRound, Connection } from '@element-plus/icons-vue'

const props = defineProps<{
  menu: {
    path: string
    meta?: { title?: string; iconActive?: string; iconDeActive?: string }
  }
}>()

const route = useRoute()
const router = useRouter()

const iconMap: Record<string, any> = {
  chat: ChatDotRound,
  noChat: ChatDotRound,
  ds: Connection,
  noDs: Connection,
}

const isActive = computed(() => route.path === props.menu.path)
const iconKey = computed(() =>
  isActive.value ? props.menu.meta?.iconActive : props.menu.meta?.iconDeActive
)
const iconCom = computed(() => (iconKey.value ? iconMap[iconKey.value] : null))

const onClick = () => router.push(props.menu.path)
</script>

<template>
  <el-menu-item :index="menu.path" @click="onClick">
    <el-icon v-if="iconCom" :size="18">
      <component :is="iconCom" />
    </el-icon>
    <template #title>
      <span>{{ menu.meta?.title }}</span>
    </template>
  </el-menu-item>
</template>
