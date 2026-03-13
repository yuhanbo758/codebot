<template>
  <div class="memory-view">
    <el-tabs v-model="activeTab">
      <el-tab-pane label="活跃记忆" name="active" />
      <el-tab-pane label="归档记忆" name="archived" />
      <el-tab-pane label="备份恢复" name="backup" />
      <el-tab-pane label="配置" name="config" />
    </el-tabs>
    <div class="memory-content">
      <router-view />
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'

const route = useRoute()
const router = useRouter()

const tabRoutes = {
  active: '/memory/active',
  archived: '/memory/archived',
  backup: '/memory/backup',
  config: '/memory/config'
}

const activeTab = computed({
  get() {
    const current = route.path.split('/').pop()
    return tabRoutes[current] ? current : 'active'
  },
  set(value) {
    const target = tabRoutes[value] || tabRoutes.active
    if (route.path !== target) {
      router.push(target)
    }
  }
})
</script>

<style scoped>
.memory-view {
  padding: 20px;
}

.memory-content {
  margin-top: 12px;
}
</style>
