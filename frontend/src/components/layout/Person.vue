<script lang="ts" setup>
import { computed } from 'vue'
import { useUserStore } from '@/stores/user'
import { UserFilled } from '@element-plus/icons-vue'

defineProps<{ collapse?: boolean }>()

const userStore = useUserStore()
const name = computed(() => userStore.getName)
</script>

<template>
  <div class="person" :class="collapse && 'collapse'" :title="name">
    <el-avatar :size="32" class="avatar">
      <el-icon :size="18"><UserFilled /></el-icon>
    </el-avatar>
    <span v-if="!collapse" class="name ellipsis">{{ name }}</span>
  </div>
</template>

<style lang="less" scoped>
.person {
  display: flex;
  align-items: center;
  gap: 8px;
  height: 40px;
  padding: 0 8px;
  border-radius: 6px;

  .avatar {
    background-color: var(--el-color-primary);
    color: #fff;
  }

  .name {
    flex: 1;
    font-size: 14px;
    font-weight: 400;
  }
}
</style>
