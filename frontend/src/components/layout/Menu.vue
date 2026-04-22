<script lang="ts" setup>
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import MenuItem from './MenuItem.vue'

defineProps<{ collapse?: boolean }>()

const route = useRoute()
const router = useRouter()

const activeMenu = computed(() => route.path)

const routerList = computed(() => {
  return router.getRoutes().filter((r) => {
    return (
      r.path !== '/' &&
      !!r.meta?.title &&
      !r.path.includes(':') &&
      !r.redirect
    )
  })
})
</script>

<template>
  <el-menu :default-active="activeMenu" class="ed-menu-vertical" :collapse="collapse">
    <MenuItem v-for="menu in routerList" :key="menu.path" :menu="menu as any" />
  </el-menu>
</template>

<style lang="less">
.ed-menu-vertical {
  --el-menu-bg-color: transparent;
  --el-menu-item-height: 40px;
  border-right: none !important;

  .el-menu-item {
    height: 40px !important;
    border-radius: 6px !important;
    margin-bottom: 2px;

    &.is-active {
      background-color: var(--white) !important;
      color: var(--el-color-primary) !important;
      font-weight: 500;
    }
  }
}
</style>
