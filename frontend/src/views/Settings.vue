<template>
  <div class="settings-view">
    <el-tabs v-model="activeTab">
      <el-tab-pane label="通用设置" name="general">
        <GeneralSettings />
      </el-tab-pane>
      <el-tab-pane label="记忆配置" name="memory">
        <MemorySettings />
      </el-tab-pane>
      <el-tab-pane label="通知配置" name="notification">
        <NotificationSettings />
      </el-tab-pane>
      <el-tab-pane label="飞书配置" name="lark">
        <LarkSettings />
      </el-tab-pane>
      <el-tab-pane label="邮箱配置" name="email">
        <EmailSettings />
      </el-tab-pane>
      <el-tab-pane label="技能目录" name="skills">
        <SkillsSettings />
      </el-tab-pane>
      <el-tab-pane label="备份恢复" name="backup">
        <BackupSettings />
      </el-tab-pane>
      <el-tab-pane label="集成配置" name="integration">
        <IntegrationSettings />
      </el-tab-pane>
      <el-tab-pane label="沙箱配置" name="sandbox">
        <SandboxSettings />
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import GeneralSettings from '@/components/GeneralSettings.vue'
import MemorySettings from '@/components/MemorySettings.vue'
import NotificationSettings from '@/components/NotificationSettings.vue'
import LarkSettings from '@/components/LarkSettings.vue'
import EmailSettings from '@/components/EmailSettings.vue'
import SkillsSettings from '@/components/SkillsSettings.vue'
import BackupSettings from '@/components/BackupSettings.vue'
import IntegrationSettings from '@/components/IntegrationSettings.vue'
import SandboxSettings from '@/components/SandboxSettings.vue'

const activeTab = ref('general')
</script>

<style scoped>
.settings-view {
  padding: 20px;
}
</style>
