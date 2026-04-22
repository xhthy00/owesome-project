<script lang="ts" setup>
import { ref, computed } from 'vue'
import Menu from './Menu.vue'
import Person from './Person.vue'
import {
  Fold,
  Expand,
  CaretBottom,
  OfficeBuilding,
} from '@element-plus/icons-vue'

const collapse = ref(false)
const toggle = () => (collapse.value = !collapse.value)

const sidebarWidth = computed(() => (collapse.value ? '64px' : '240px'))
</script>

<template>
  <div class="system-layout">
    <aside
      class="left-side"
      :class="collapse && 'left-side-collapse'"
      :style="{ width: sidebarWidth }"
    >
      <div class="brand" :class="collapse && 'collapse'">
        <div class="logo">A</div>
        <span v-if="!collapse" class="brand-name ellipsis">Awesome Project</span>
      </div>

      <div v-if="!collapse" class="workspace">
        <el-icon :size="16"><OfficeBuilding /></el-icon>
        <span class="ws-name ellipsis">默认工作空间</span>
        <el-icon :size="12" class="ws-arrow"><CaretBottom /></el-icon>
      </div>

      <Menu :collapse="collapse" />

      <div class="bottom">
        <div class="personal-info">
          <Person :collapse="collapse" />
          <el-icon class="fold" :size="20" @click="toggle">
            <Expand v-if="collapse" />
            <Fold v-else />
          </el-icon>
        </div>
      </div>
    </aside>
    <main class="right-main">
      <div class="content">
        <router-view />
      </div>
    </main>
  </div>
</template>

<style lang="less" scoped>
.system-layout {
  width: 100vw;
  height: 100vh;
  background-color: #f1f4f3;
  display: flex;

  .left-side {
    height: 100%;
    padding: 16px;
    position: relative;
    transition: width 0.15s ease-in-out;
    flex-shrink: 0;

    .brand {
      display: flex;
      align-items: center;
      gap: 8px;
      margin-bottom: 12px;
      cursor: default;
      height: 32px;

      .logo {
        width: 24px;
        height: 24px;
        border-radius: 6px;
        background: var(--el-color-primary);
        color: #fff;
        font-weight: 700;
        font-size: 14px;
        display: flex;
        align-items: center;
        justify-content: center;
      }

      .brand-name {
        font-weight: 600;
        font-size: 16px;
        color: #1f2329;
      }
    }

    .workspace {
      display: flex;
      align-items: center;
      gap: 8px;
      height: 36px;
      padding: 0 12px;
      margin-bottom: 16px;
      background: rgba(31, 35, 41, 0.06);
      border-radius: 6px;
      cursor: pointer;
      font-size: 14px;
      color: #1f2329;

      .ws-name {
        flex: 1;
      }

      .ws-arrow {
        color: #646a73;
      }

      &:hover {
        background: rgba(31, 35, 41, 0.08);
      }
    }

    .bottom {
      position: absolute;
      bottom: 16px;
      left: 16px;
      width: calc(100% - 32px);

      .personal-info {
        display: flex;
        align-items: center;

        .fold {
          margin-left: auto;
          width: 32px;
          height: 32px;
          border-radius: 6px;
          cursor: pointer;
          display: flex;
          align-items: center;
          justify-content: center;
          color: #646a73;

          &:hover {
            background: rgba(31, 35, 41, 0.06);
          }
        }
      }
    }

    &.left-side-collapse {
      padding: 16px 12px;

      .brand {
        justify-content: center;
      }

      .bottom {
        left: 12px;
        width: calc(100% - 24px);

        .personal-info {
          flex-direction: column;
          gap: 8px;
        }
      }
    }
  }

  .right-main {
    flex: 1;
    padding: 8px 8px 8px 0;
    max-height: 100vh;
    min-width: 0;

    .content {
      width: 100%;
      height: 100%;
      background-color: #fff;
      border-radius: 12px;
      box-shadow: 0 2px 4px rgba(31, 35, 41, 0.08);
      overflow: hidden;
    }
  }
}
</style>
