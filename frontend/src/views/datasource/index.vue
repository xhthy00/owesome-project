<script lang="ts" setup>
import { ref, reactive, onMounted, computed } from 'vue'
import { ElMessage, ElMessageBox, type FormInstance, type FormRules } from 'element-plus'
import { Plus, Edit, Delete, Connection, Refresh } from '@element-plus/icons-vue'
import {
  datasourceApi,
  type DatasourceItem,
  type DatasourceCreatePayload,
} from '@/api/datasource'
import { datetimeFormat } from '@/utils/utils'

const list = ref<DatasourceItem[]>([])
const loading = ref(false)
const dialogVisible = ref(false)
const editingId = ref<number | null>(null)
const submitting = ref(false)
const testing = ref(false)
const formRef = ref<FormInstance>()

const blankForm = (): DatasourceCreatePayload => ({
  name: '',
  description: '',
  type: 'pg',
  config: {
    host: '',
    port: 5432,
    username: '',
    password: '',
    database: '',
    timeout: 30,
    ssl: false,
  },
})

const form = reactive<DatasourceCreatePayload>(blankForm())

const rules: FormRules = {
  name: [{ required: true, message: '请输入名称', trigger: 'blur' }],
  type: [{ required: true, message: '请选择类型', trigger: 'change' }],
  'config.host': [{ required: true, message: '请输入主机', trigger: 'blur' }],
  'config.port': [{ required: true, message: '请输入端口', trigger: 'blur' }],
  'config.username': [{ required: true, message: '请输入用户名', trigger: 'blur' }],
  'config.database': [{ required: true, message: '请输入数据库', trigger: 'blur' }],
}

const dialogTitle = computed(() =>
  editingId.value ? '编辑数据源' : '新增数据源'
)

const reload = async () => {
  loading.value = true
  try {
    const res = await datasourceApi.list({ limit: 200 })
    list.value = res.items
  } catch (e: any) {
    ElMessage.error(e?.message || '加载失败')
  } finally {
    loading.value = false
  }
}

const onAdd = () => {
  Object.assign(form, blankForm())
  editingId.value = null
  dialogVisible.value = true
}

const onEdit = async (row: DatasourceItem) => {
  try {
    const detail = await datasourceApi.get(row.id)
    Object.assign(form, {
      name: detail.name,
      description: detail.description || '',
      type: detail.type,
      config: { ...blankForm().config, ...detail.config, password: '' },
    })
    editingId.value = row.id
    dialogVisible.value = true
  } catch (e: any) {
    ElMessage.error(e?.message || '加载失败')
  }
}

const onDelete = (row: DatasourceItem) => {
  ElMessageBox.confirm(`确定删除数据源 ${row.name}？`, '提示', { type: 'warning' })
    .then(async () => {
      await datasourceApi.delete(row.id)
      ElMessage.success('已删除')
      await reload()
    })
    .catch(() => {})
}

const onTest = async (row: DatasourceItem) => {
  try {
    const res = await datasourceApi.testConnection(row.id)
    if (res.success) {
      ElMessage.success(res.message || '连接成功')
    } else {
      ElMessage.error(res.message || '连接失败')
    }
  } catch (e: any) {
    ElMessage.error(e?.message || '连接失败')
  }
}

const onSubmit = async () => {
  if (!formRef.value) return
  await formRef.value.validate()
  submitting.value = true
  try {
    if (editingId.value) {
      await datasourceApi.update(editingId.value, form)
      ElMessage.success('已更新')
    } else {
      await datasourceApi.add(form)
      ElMessage.success('已新增')
    }
    dialogVisible.value = false
    await reload()
  } catch (e: any) {
    ElMessage.error(e?.message || '保存失败')
  } finally {
    submitting.value = false
  }
}

const onTestForm = async () => {
  if (!formRef.value) return
  await formRef.value.validate()
  testing.value = true
  try {
    let id = editingId.value
    if (!id) {
      const created = await datasourceApi.add(form)
      id = created.id
      editingId.value = id
      ElMessage.success('已新增，正在测试连接')
      await reload()
    }
    const res = await datasourceApi.testConnection(id)
    res.success ? ElMessage.success(res.message || '连接成功') : ElMessage.error(res.message || '连接失败')
  } catch (e: any) {
    ElMessage.error(e?.message || '连接失败')
  } finally {
    testing.value = false
  }
}

onMounted(reload)
</script>

<template>
  <div class="datasource-page">
    <div class="page-header">
      <div class="title-box">
        <h2>数据源管理</h2>
        <span class="muted">配置可用于 SQL 问答的数据库连接</span>
      </div>
      <div class="actions">
        <el-button :icon="Refresh" @click="reload">刷新</el-button>
        <el-button type="primary" :icon="Plus" @click="onAdd">新增数据源</el-button>
      </div>
    </div>

    <el-table v-loading="loading" :data="list" stripe border>
      <el-table-column label="ID" prop="id" width="80" />
      <el-table-column label="名称" prop="name" min-width="160" show-overflow-tooltip />
      <el-table-column label="类型" prop="type" width="100" />
      <el-table-column label="描述" prop="description" min-width="200" show-overflow-tooltip />
      <el-table-column label="状态" prop="status" width="100" />
      <el-table-column label="创建时间" width="180">
        <template #default="{ row }">{{ datetimeFormat(row.create_time) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="240" fixed="right">
        <template #default="{ row }">
          <el-button type="primary" link :icon="Connection" @click="onTest(row)">测试连接</el-button>
          <el-button type="primary" link :icon="Edit" @click="onEdit(row)">编辑</el-button>
          <el-button type="danger" link :icon="Delete" @click="onDelete(row)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="dialogVisible" :title="dialogTitle" width="640" destroy-on-close>
      <el-form ref="formRef" :model="form" :rules="rules" label-width="100px">
        <el-form-item label="名称" prop="name">
          <el-input v-model="form.name" />
        </el-form-item>
        <el-form-item label="类型" prop="type">
          <el-select v-model="form.type" style="width: 100%">
            <el-option label="PostgreSQL" value="pg" />
            <el-option label="MySQL" value="mysql" />
          </el-select>
        </el-form-item>
        <el-form-item label="主机" prop="config.host">
          <el-input v-model="form.config.host" />
        </el-form-item>
        <el-form-item label="端口" prop="config.port">
          <el-input-number v-model="form.config.port" :min="1" :max="65535" style="width: 100%" />
        </el-form-item>
        <el-form-item label="数据库" prop="config.database">
          <el-input v-model="form.config.database" />
        </el-form-item>
        <el-form-item label="用户名" prop="config.username">
          <el-input v-model="form.config.username" />
        </el-form-item>
        <el-form-item label="密码">
          <el-input v-model="form.config.password" type="password" show-password placeholder="编辑时留空表示不修改" />
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="form.description" type="textarea" :rows="2" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button :loading="testing" @click="onTestForm">测试连接</el-button>
        <el-button type="primary" :loading="submitting" @click="onSubmit">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style lang="less" scoped>
.datasource-page {
  padding: 16px 24px;

  .page-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 16px;

    .title-box {
      h2 {
        font-size: 18px;
        font-weight: 500;
        margin: 0 0 4px;
      }
    }

    .actions {
      display: flex;
      gap: 8px;
    }
  }
}
</style>
