<template>
  <div class="settings-view">
    <el-tabs v-model="activeTab">
      <el-tab-pane label="通用" name="general" lazy>
        <GeneralSettings />
      </el-tab-pane>
      <el-tab-pane label="模式" name="mode" lazy>
        <ModeSettings />
      </el-tab-pane>
      <el-tab-pane label="媒体" name="media" lazy>
        <MediaSettings />
      </el-tab-pane>
      <el-tab-pane label="OpenCode" name="opencode" lazy>
        <OpenCodeSettings />
      </el-tab-pane>
      <el-tab-pane label="Codex" name="codex" lazy>
        <CodexSettings />
      </el-tab-pane>
      <el-tab-pane label="Rakazo" name="rakazo" lazy>
        <RakazoSettings />
      </el-tab-pane>
      <el-tab-pane label="Obsidian" name="obsidian" lazy>
        <ObsidianSettings />
      </el-tab-pane>
      <el-tab-pane label="通知" name="notification" lazy>
        <NotificationSettings />
      </el-tab-pane>
      <el-tab-pane label="飞书" name="lark" lazy>
        <LarkSettings />
      </el-tab-pane>
      <el-tab-pane label="邮箱" name="email" lazy>
        <EmailSettings />
      </el-tab-pane>
      <el-tab-pane label="技能" name="skills" lazy>
        <SkillsSettings />
      </el-tab-pane>
      <el-tab-pane label="备份" name="backup" lazy>
        <BackupSettings />
      </el-tab-pane>
      <el-tab-pane label="集成" name="integration" lazy>
        <IntegrationSettings />
      </el-tab-pane>
      <el-tab-pane label="安全" name="security" lazy>
        <SecuritySettings />
      </el-tab-pane>
      <el-tab-pane label="沙箱" name="sandbox" lazy>
        <SandboxSettings />
      </el-tab-pane>
      <el-tab-pane label="文档" name="docs" lazy>
        <Docs />
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup>
import { ref, defineAsyncComponent } from 'vue'
import { useRoute } from 'vue-router'
const GeneralSettings = defineAsyncComponent(() => import('@/components/GeneralSettings.vue'))
const ModeSettings = defineAsyncComponent(() => import('@/components/ModeSettings.vue'))
const MediaSettings = defineAsyncComponent(() => import('@/components/MediaSettings.vue'))
const CodexSettings = defineAsyncComponent(() => import('@/components/CodexSettings.vue'))
const OpenCodeSettings = defineAsyncComponent(() => import('@/components/OpenCodeSettings.vue'))
const RakazoSettings = defineAsyncComponent(() => import('@/components/RakazoSettings.vue'))
const ObsidianSettings = defineAsyncComponent(() => import('@/components/ObsidianSettings.vue'))
const NotificationSettings = defineAsyncComponent(() => import('@/components/NotificationSettings.vue'))
const LarkSettings = defineAsyncComponent(() => import('@/components/LarkSettings.vue'))
const EmailSettings = defineAsyncComponent(() => import('@/components/EmailSettings.vue'))
const SkillsSettings = defineAsyncComponent(() => import('@/components/SkillsSettings.vue'))
const BackupSettings = defineAsyncComponent(() => import('@/components/BackupSettings.vue'))
const IntegrationSettings = defineAsyncComponent(() => import('@/components/IntegrationSettings.vue'))
const SecuritySettings = defineAsyncComponent(() => import('@/components/SecuritySettings.vue'))
const SandboxSettings = defineAsyncComponent(() => import('@/components/SandboxSettings.vue'))
const Docs = defineAsyncComponent(() => import('@/views/Docs.vue'))

const route = useRoute()
// 支持通过设置链接直接定位 OpenCode 权限开关。
const tabNames = new Set(['general', 'mode', 'media', 'codex', 'rakazo', 'obsidian', 'notification', 'lark', 'email', 'skills', 'backup', 'integration', 'security', 'sandbox', 'docs'])
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
.settings-view :deep(.el-tabs__item) {
  padding: 0 12px;
}
@media (max-width: 720px) {
  .settings-view { padding: 12px; }
  .settings-view :deep(.el-tabs__item) { padding: 0 9px; }
}
</style>
