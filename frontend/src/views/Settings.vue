<template>
  <div class="settings-view">
    <el-tabs v-model="activeTab">
      <el-tab-pane label="通用设置" name="general">
        <GeneralSettings />
      </el-tab-pane>
      <el-tab-pane label="OpenCode" name="opencode">
        <OpenCodeSettings />
      </el-tab-pane>
      <el-tab-pane label="Codex" name="codex">
        <CodexSettings />
      </el-tab-pane>
      <el-tab-pane label="Rakazo" name="rakazo">
        <RakazoSettings />
      </el-tab-pane>
      <el-tab-pane label="Obsidian" name="obsidian">
        <ObsidianSettings />
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
      <el-tab-pane label="访问安全" name="security">
        <SecuritySettings />
      </el-tab-pane>
      <el-tab-pane label="沙箱配置" name="sandbox">
        <SandboxSettings />
      </el-tab-pane>
      <el-tab-pane label="文档" name="docs">
        <Docs />
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { useRoute } from 'vue-router'
import GeneralSettings from '@/components/GeneralSettings.vue'
import CodexSettings from '@/components/CodexSettings.vue'
import OpenCodeSettings from '@/components/OpenCodeSettings.vue'
import RakazoSettings from '@/components/RakazoSettings.vue'
import ObsidianSettings from '@/components/ObsidianSettings.vue'
import NotificationSettings from '@/components/NotificationSettings.vue'
import LarkSettings from '@/components/LarkSettings.vue'
import EmailSettings from '@/components/EmailSettings.vue'
import SkillsSettings from '@/components/SkillsSettings.vue'
import BackupSettings from '@/components/BackupSettings.vue'
import IntegrationSettings from '@/components/IntegrationSettings.vue'
import SecuritySettings from '@/components/SecuritySettings.vue'
import SandboxSettings from '@/components/SandboxSettings.vue'
import Docs from '@/views/Docs.vue'

const route = useRoute()
// 支持通过设置链接直接定位 OpenCode 权限开关。
const tabNames = new Set(['general', 'codex', 'rakazo', 'obsidian', 'notification', 'lark', 'email', 'skills', 'backup', 'integration', 'security', 'sandbox', 'docs'])
// 兼容旧收藏链接，但页面本身不再保留 Hermes 标签或接口。
const requestedTab = route.query.tab === 'hermes' ? 'codex' : route.query.tab
tabNames.add('opencode')
const initialTab = typeof requestedTab === 'string' && tabNames.has(requestedTab) ? requestedTab : 'general'
const activeTab = ref(initialTab)
</script>

<style scoped>
.settings-view {
  padding: 20px;
}
</style>
