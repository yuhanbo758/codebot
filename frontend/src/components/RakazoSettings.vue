<template>
  <div class="rakazo-settings" v-loading="loading">
    <section v-if="!setupOptIn" class="optional-feature-card">
      <div class="optional-feature-copy">
        <div class="eyebrow">OPTIONAL LOCAL RUNTIME</div>
        <h2>Rakazo 是完全可选的</h2>
        <p>不使用 Rakazo 时，Codebot 继续只运行 OpenCode/Codex，不检查、不下载、不安装 Docker，也不会启用 WSL、启动容器或占用 Rakazo 磁盘空间。</p>
        <ul>
          <li>打开安装向导本身不会修改系统。</li>
          <li>只有你主动选择目录并点击“一键安装 Docker”后，才会下载安装环境。</li>
          <li>Docker 就绪后仍由你主动安装并启动 Rakazo 运行时和本机授权。</li>
        </ul>
      </div>
      <div class="optional-feature-actions">
        <el-tag type="info" size="large">默认不启用</el-tag>
        <el-button type="primary" size="large" @click="openSetupWizard">启用 Rakazo 安装向导</el-button>
      </div>
    </section>

    <template v-else>
    <section class="setup-hero">
      <div>
        <div class="eyebrow">RAKAZO LOCAL RUNTIME</div>
        <h2>本机运行环境</h2>
        <p>Codebot 负责 Docker 安装、私网桥接、加密授权，并直接复用 OpenCode 当前模型连接；你无需为 Rakazo 重复配置 Provider。</p>
      </div>
      <div class="setup-hero-actions">
        <el-tag :type="setupReady ? 'success' : 'warning'" size="large">
          {{ setupReady ? '运行环境已就绪' : `待完成 ${incompleteStepCount} 项` }}
        </el-tag>
        <el-button
          type="primary"
          size="large"
          :loading="stackStarting"
          :disabled="!canStartAll"
          @click="startAll"
        >一键启动全部</el-button>
        <span class="field-tip start-all-tip">{{ stackProgress.message || '首次安装完成后，一次恢复 Docker、模型连接、Rakazo 运行时和本机授权。' }}</span>
      </div>
    </section>

    <div class="setup-steps">
      <article class="setup-step" :class="{ complete: dockerReady }">
        <div class="step-index">1</div>
        <div class="step-copy">
          <strong>Docker Desktop</strong>
          <span>{{ dockerReady ? `Engine ${desktopDocker?.dockerExecutable || status?.docker?.serverVersion || '已就绪'}` : dockerInstalled ? '已安装，等待 WSL 2 Engine 启动' : '选择磁盘后由 Codebot 一键安装' }}</span>
        </div>
        <el-tag :type="dockerReady ? 'success' : dockerInstalled ? 'info' : 'warning'">{{ dockerReady ? '完成' : dockerInstalled ? '已安装待启动' : '待安装' }}</el-tag>
      </article>
      <article class="setup-step" :class="{ complete: modelsReady }">
        <div class="step-index">2</div>
        <div class="step-copy">
          <strong>OpenCode 模型连接</strong>
          <span>{{ modelsReady ? `已同步 ${status?.selectableModelCount} 个可选模型，可直接使用` : '等待 OpenCode 当前连接的可选模型' }}</span>
        </div>
        <el-tag :type="modelsReady ? 'success' : 'warning'">{{ modelsReady ? '已同步' : '待连接' }}</el-tag>
      </article>
      <article class="setup-step" :class="{ complete: runtimeReady }">
        <div class="step-index">3</div>
        <div class="step-copy">
          <strong>Rakazo 运行时</strong>
          <span>{{ runtimeReady ? `已连接 ${status?.runtimeVersion || status?.runtimeSourceRevision?.slice(0, 12) || ''}` : runtimeStepText }}</span>
        </div>
        <el-tag :type="runtimeReady ? 'success' : status?.experimentalRuntimeAvailable ? 'warning' : 'info'">{{ runtimeReady ? '完成' : status?.experimentalRuntimeAvailable ? '实验版可用' : '受门禁保护' }}</el-tag>
      </article>
      <article class="setup-step" :class="{ complete: authReady }">
        <div class="step-index">4</div>
        <div class="step-copy">
          <strong>本机授权</strong>
          <span>{{ authReady ? '凭据已由系统安全存储加密托管' : '运行时启动后可自动创建本机账号' }}</span>
        </div>
        <el-tag :type="authReady ? 'success' : 'warning'">{{ authReady ? '完成' : '待授权' }}</el-tag>
      </article>
    </div>

    <el-divider content-position="left">安装、启动与存储</el-divider>
    <div class="install-grid">
      <section class="surface-card primary-card">
        <div class="card-heading">
          <div>
            <h3>Docker 与 Rakazo 大文件</h3>
            <p>Docker 程序、镜像、WSL 虚拟磁盘和 Rakazo 容器数据优先放在你选择的磁盘。</p>
          </div>
          <el-tag :type="dockerReady ? 'success' : dockerInstalled ? 'info' : 'warning'">{{ dockerReady ? 'Docker 已就绪' : dockerInstalled ? 'Docker 已安装' : '尚未安装' }}</el-tag>
        </div>
        <div class="directory-row">
          <el-input v-model="dockerStorageRoot" readonly placeholder="请选择剩余空间不少于 20 GiB 的目录" />
          <el-button :disabled="dockerBusy" @click="selectDockerStorage">选择目录</el-button>
          <el-button
            v-if="!dockerInstalled"
            type="primary"
            :loading="dockerInstalling"
            :disabled="!desktopDocker?.supported || !dockerStorageRoot"
            @click="installDocker"
          >一键安装 Docker</el-button>
          <el-button
            v-else-if="!dockerReady"
            type="primary"
            :loading="dockerStarting"
            :disabled="!desktopDocker?.supported"
            @click="startDocker"
          >启动 Docker</el-button>
        </div>
        <el-progress
          v-if="dockerProgress.phase"
          class="install-progress"
          :percentage="dockerProgress.percent || 0"
          :status="dockerProgress.phase === 'error' ? 'exception' : dockerProgress.phase === 'done' ? 'success' : undefined"
        />
        <div v-if="dockerProgress.message" class="field-tip progress-copy">{{ dockerProgress.message }}</div>
        <el-alert
          v-if="!isElectronDesktop"
          class="section-alert"
          title="请在 Codebot Windows 桌面版中安装或启动 Docker"
          description="浏览器页面不能获得系统安装或启动权限；桌面版会按实际状态分别显示“一键安装 Docker”或“启动 Docker”，只有安装时才校验 Docker Inc 数字签名并显示 Windows 管理员确认。"
          type="info"
          show-icon
          :closable="false"
        />
        <div class="storage-facts">
          <div><span>Codebot 程序</span><code>{{ desktopDocker?.codebotInstallDir || '由安装器选择' }}</code></div>
          <div><span>Codebot 用户数据</span><code>{{ desktopDocker?.codebotDataDir || '系统应用数据目录' }}</code></div>
          <div><span>Docker 大文件</span><code>{{ dockerStorageRoot ? `${dockerStorageRoot}\\DockerDesktop` : '尚未选择' }}</code></div>
        </div>
        <div class="field-tip">Codebot Windows 安装器已改为向导模式，可选择程序安装目录。Docker Desktop 会使用自己的 WSL 2 Linux Engine，无需也不应在 Ubuntu 中重复安装 Docker Engine。Chromium 配置和 Windows/WSL 系统组件仍保留在系统要求的位置；不会把可移动的 Docker 镜像和虚拟磁盘强制塞入系统盘。</div>
      </section>

      <section class="surface-card">
        <div class="card-heading">
          <div>
            <h3>Rakazo 官方运行时</h3>
            <p>{{ runtimeReady ? '合同与镜像身份均已通过，可恢复项目主会话。' : runtimeStepText }}</p>
          </div>
          <el-tag :type="runtimeReady ? 'success' : status?.experimentalRuntimeAvailable ? 'warning' : 'info'">
            {{ runtimeReady ? '已连接' : status?.experimentalRuntimeAvailable ? '固定实验版可用' : '等待兼容版本' }}
          </el-tag>
        </div>
        <el-alert
          v-if="status?.experimentalRuntimeAvailable && !runtimeReady"
          title="可以运行：使用官方 main 的固定提交实验版"
          :description="`Codebot 将只使用提交 ${shortRevision} 及兼容清单中的不可变镜像摘要，不会跟踪会漂移的 edge。该通道用于本机实验验收，尚不标记为生产可用。${!modelsReady ? ' 请先确认 OpenCode 已连接至少一个可用模型。' : ''}`"
          type="warning"
          show-icon
          :closable="false"
        />
        <el-alert
          v-else-if="status && !status.runtimeStartAllowed && !runtimeReady"
          title="当前稳定通道仍受生产门禁保护"
          :description="status.runtimeStartReason || '兼容清单尚未批准任何稳定版本。'"
          type="info"
          show-icon
          :closable="false"
        />
        <div v-if="status?.runtimeSourceRevision || status?.runtimeImages?.app" class="runtime-identity">
          <div><span>上游提交</span><code>{{ status?.runtimeSourceRevision || '-' }}</code></div>
          <div><span>App 镜像</span><code>{{ status?.runtimeImages?.app || '-' }}</code></div>
          <div><span>Computer 镜像</span><code>{{ status?.runtimeImages?.computer || '-' }}</code></div>
        </div>
        <div class="action-row">
          <el-button
            v-if="canOfferExperimentalInstall"
            type="primary"
            :disabled="!canInstallExperimental"
            :loading="runtimeAction === 'install_experimental'"
            @click="controlRuntime('install_experimental')"
          >安装并启动固定实验版</el-button>
          <el-button v-else type="primary" :disabled="!canStartRuntime" :loading="runtimeAction === 'start'" @click="controlRuntime('start')">启动运行时</el-button>
          <el-button :disabled="!canStartRuntime" :loading="runtimeAction === 'restart'" @click="controlRuntime('restart')">重启</el-button>
          <el-button type="danger" plain :loading="runtimeAction === 'stop'" @click="controlRuntime('stop')">停止</el-button>
          <el-button :loading="loading" @click="loadAll">重新检查</el-button>
          <el-button text @click="openExternal('https://github.com/elie222/rakazo/releases')">查看官方 Releases</el-button>
        </div>
      </section>
    </div>

    <el-divider content-position="left">运行时</el-divider>
    <div class="status-grid">
      <div><span>启用状态</span><el-tag :type="status?.enabled ? 'success' : 'info'">{{ status?.enabled ? '已启用' : '未启用' }}</el-tag></div>
      <div><span>Rakazo</span><el-tag :type="status?.connected ? 'success' : 'danger'">{{ status?.connected ? '已连接' : '未连接' }}</el-tag></div>
      <div><span>Docker</span><el-tag :type="status?.docker?.ready ? 'success' : 'warning'">{{ status?.docker?.ready ? `可用 ${status?.docker?.serverVersion || ''}` : '不可用' }}</el-tag></div>
      <div><span>运行通道</span><el-tag :type="status?.runtimeChannel === 'experimental' ? 'warning' : 'success'">{{ status?.runtimeChannel === 'experimental' ? '固定实验版' : '稳定版' }}</el-tag></div>
      <div><span>运行时版本</span><code>{{ status?.runtimeVersion || '-' }}</code></div>
      <div><span>契约版本</span><code>{{ status?.contractVersion || '-' }}</code></div>
      <div><span>生产状态</span><el-tag :type="status?.productionReady ? 'success' : 'warning'">{{ status?.productionReady ? '生产可用' : '实验验收中' }}</el-tag></div>
      <div><span>授权</span><el-tag :type="status?.sessionAuthorized ? 'success' : 'warning'">{{ status?.sessionAuthorized ? '已配置' : '待配置' }}</el-tag></div>
      <div><span>项目</span><code>{{ status?.projectCount ?? 0 }}</code></div>
      <div><span>OpenCode 已启用</span><code>{{ status?.openCodeEnabledModelCount ?? 0 }}</code></div>
      <div><span>Rakazo 可选择</span><code>{{ status?.selectableModelCount ?? 0 }}</code></div>
      <div><span>可用模型</span><code>{{ status?.selectableModelCount ?? 0 }}</code></div>
    </div>
    <el-alert v-if="displayRuntimeError" class="section-alert" :title="displayRuntimeError" type="error" show-icon :closable="false" />
    <el-alert
      v-if="status?.samplingBridge && !status.samplingBridge.productionReady"
      class="section-alert"
      title="模型采样桥尚未达到生产门槛"
      :description="status.samplingBridge.reason"
      type="warning"
      show-icon
      :closable="false"
    />

    <el-divider content-position="left">配置</el-divider>
    <el-form :model="form" label-width="150px">
      <el-form-item label="启用 Rakazo"><el-switch v-model="form.enabled" /></el-form-item>
      <el-form-item label="完全访问">
        <el-switch v-model="form.full_access" />
        <div class="field-tip">保存后从下一轮生效：允许项目读写、专属 Computer 内命令及外网，自动允许权限请求。关闭后下一轮恢复项目原权限；普通问题仍需回答。</div>
      </el-form-item>
      <el-form-item label="随 Codebot 启动">
        <el-switch v-model="form.auto_start" :loading="autoStartSaving" @change="saveAutoStart" />
        <div class="field-tip">切换后立即保存；下次启动 Codebot 时自动恢复已有 Docker、运行时和加密授权，不会自动安装系统组件或创建新账号。</div>
      </el-form-item>
      <el-form-item label="本机 API 地址">
        <el-input v-model="form.api_url" placeholder="http://127.0.0.1:3100" />
        <div class="field-tip">仅接受 127.0.0.1、localhost 或 ::1，不向局域网/公网暴露。</div>
      </el-form-item>
      <el-form-item label="受管 Compose">
        <el-input v-model="form.compose_file" readonly placeholder="点击安装固定实验版后由 Codebot 自动准备" />
        <div class="field-tip">Codebot 自动复制并校验官方 Compose，再叠加外置适配器；不修改 Rakazo 源码，也不要求用户手工下载或填写路径。</div>
      </el-form-item>
      <el-form-item label="Docker 项目名"><el-input v-model="form.docker_project_name" /></el-form-item>
      <el-form-item label="资源上限">
        <div class="field-tip">Codebot model-adapter 使用固定最小权限与 256 MiB / 0.5 CPU 上限；官方 Rakazo 服务栈和 Computer 尚无已验证的统一资源合同，因此当前不提供会误导用户的可编辑资源参数。</div>
      </el-form-item>
      <el-form-item label="新项目默认权限">
        <el-checkbox v-model="form.default_project_read" @change="(value) => saveDefaultPermission('default_project_read', value)">读取</el-checkbox>
        <el-checkbox v-model="form.default_project_write" @change="(value) => saveDefaultPermission('default_project_write', value)">写入</el-checkbox>
        <el-checkbox v-model="form.default_command_execution" @change="(value) => saveDefaultPermission('default_command_execution', value)">容器内命令</el-checkbox>
        <el-checkbox v-model="form.default_external_network" @change="(value) => saveDefaultPermission('default_external_network', value)">外部网络</el-checkbox>
        <div class="field-tip">切换后立即保存，只影响以后新建的项目。命令运行在项目专属 Computer 容器内；外网关闭时使用 Docker internal 私有网络真实阻断。</div>
      </el-form-item>
      <el-form-item>
        <el-button type="primary" :loading="saving" @click="saveConfig">保存 Rakazo 设置</el-button>
      </el-form-item>
    </el-form>

    <el-divider content-position="left">已有项目权限</el-divider>
    <section class="surface-card project-permissions-card">
      <div class="card-heading">
        <div>
          <h3>逐项目运行权限</h3>
          <p>修改立即生效。项目文件仍受根目录、符号链接、敏感文件和 2 MiB 上限保护；切换外网会重建该项目的 Computer 容器与私有网络，但保留 home 数据。</p>
        </div>
        <div class="table-actions">
          <el-tag type="info">{{ projectPermissions.length }} 个项目</el-tag>
          <el-button size="small" :disabled="Boolean(projectPermissionSaving)" @click="loadProjectPermissions">刷新</el-button>
        </div>
      </div>
      <el-table v-if="projectPermissions.length" :data="projectPermissions" size="small" row-key="projectId">
        <el-table-column label="项目" min-width="360">
          <template #default="{ row }">
            <div class="project-identity">
              <strong>{{ projectDisplayName(row) }}</strong>
              <code>{{ row.projectDir }}</code>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="读取" width="100" align="center">
          <template #default="{ row }">
            <el-switch
              v-model="row.projectRead"
              :disabled="projectPermissionSaving === row.projectId"
              @change="(value) => saveProjectPermission(row, 'projectRead', value)"
            />
          </template>
        </el-table-column>
        <el-table-column label="写入" width="100" align="center">
          <template #default="{ row }">
            <el-switch
              v-model="row.projectWrite"
              :disabled="projectPermissionSaving === row.projectId"
              @change="(value) => saveProjectPermission(row, 'projectWrite', value)"
            />
          </template>
        </el-table-column>
        <el-table-column label="命令执行" width="120" align="center">
          <template #default="{ row }">
            <el-switch
              v-model="row.commandExecution"
              :disabled="projectPermissionSaving === row.projectId || row.commandSupported === false"
              @change="(value) => saveProjectPermission(row, 'commandExecution', value)"
            />
          </template>
        </el-table-column>
        <el-table-column label="外部网络" width="120" align="center">
          <template #default="{ row }">
            <el-switch
              v-model="row.externalNetwork"
              :disabled="projectPermissionSaving === row.projectId || row.externalNetworkSupported === false"
              @change="(value) => saveProjectPermission(row, 'externalNetwork', value)"
            />
          </template>
        </el-table-column>
      </el-table>
      <el-empty v-else description="尚未创建 Rakazo 项目主会话" :image-size="72" />
    </section>

    <el-divider content-position="left">Rakazo 本机授权</el-divider>
    <section class="surface-card authorization-card">
      <div class="card-heading">
        <div>
          <h3>自动创建并托管本机账号</h3>
          <p>Electron 主进程通过 Windows safeStorage 加密邮箱、随机密码和会话；令牌不会进入 config.json、页面状态或日志。</p>
        </div>
        <el-tag :type="authReady ? 'success' : 'warning'">{{ authReady ? '已授权' : '未授权' }}</el-tag>
      </div>
      <div class="action-row auth-actions">
        <el-button type="primary" :loading="authSaving" :disabled="!isElectronDesktop || !runtimeReady || desktopAuthorization?.encryptionAvailable === false" @click="bootstrapAuthorization">一键本机授权</el-button>
        <el-button type="danger" plain :loading="authSaving" :disabled="!authReady" @click="clearAuthorization">清除授权</el-button>
      </div>
      <el-collapse class="advanced-auth">
        <el-collapse-item title="高级：使用外部提供的会话令牌" name="manual-token">
          <div class="field-tip token-tip">仅用于自管 Rakazo 账号。令牌只驻留当前后端进程；桌面一键授权才支持 safeStorage 跨重启恢复。</div>
          <div class="token-row">
            <el-input v-model="sessionToken" type="password" show-password autocomplete="off" placeholder="粘贴 Rakazo 会话令牌；保存后不会再回显" />
            <el-button :loading="authSaving" :disabled="sessionToken.trim().length < 16" @click="saveAuthorization">保存并验证</el-button>
          </div>
        </el-collapse-item>
      </el-collapse>
    </section>

    <el-divider content-position="left">OpenCode 已启用模型</el-divider>
    <div class="table-toolbar">
      <span>OpenCode 当前已启用 {{ modelCatalogTotal }} 个；复用已有授权，可代理模型可直接在聊天中选择。</span>
      <div class="table-actions">
        <el-select v-model="statusFilter" clearable placeholder="全部状态" style="width: 180px">
          <el-option label="可直接使用" value="available" />
          <el-option label="当前连接不可安全代理" value="authorization_required" />
          <el-option label="协议待适配" value="protocol_pending" />
        </el-select>
        <el-input v-model="modelSearch" clearable placeholder="搜索模型或 Provider" style="width: 240px" />
        <el-button :loading="modelsLoading" @click="loadModels(true)">刷新目录</el-button>
      </div>
    </div>
    <el-table :data="filteredModels" max-height="460" stripe>
      <el-table-column prop="name" label="模型" min-width="230" show-overflow-tooltip />
      <el-table-column prop="provider" label="Provider" width="140" />
      <el-table-column prop="protocol" label="协议" width="150" />
      <el-table-column label="状态" width="150">
        <template #default="scope">
          <el-tag :type="modelStatusType(scope.row)">{{ modelStatusLabel(scope.row) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="incompatibilityReason" label="说明" min-width="300" show-overflow-tooltip />
    </el-table>
    <div class="model-pagination">
      <span>显示 {{ filteredModels.length }} / {{ modelCatalogTotal }} 个模型，可滚动查看</span>
    </div>

    <el-divider content-position="left">版本观察</el-divider>
    <el-alert
      title="实验版固定不自动更新；正式更新仍等待稳定 Release"
      description="当前实验运行时固定到 Codebot 兼容清单中的上游提交与镜像摘要，edge 后续变化不会影响本机。稳定 Release 出现后仍需通过候选环境、数据库副本迁移和故障回滚门禁，才会开放正式更新按钮。"
      type="info"
      show-icon
      :closable="false"
    />
    <div class="action-row update-row">
      <el-button :loading="updateLoading" @click="checkUpdate">检查官方稳定版</el-button>
      <el-button text @click="openExternal('https://github.com/elie222/rakazo/releases')">官方 Releases</el-button>
    </div>
    <pre v-if="updateInfo" class="update-detail">{{ JSON.stringify(updateInfo, null, 2) }}</pre>
    </template>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import axios from 'axios'

const loading = ref(false)
const saving = ref(false)
const authSaving = ref(false)
const modelsLoading = ref(false)
const runtimeAction = ref('')
const updateLoading = ref(false)
const status = ref(null)
const models = ref([])
const statusFilter = ref('')
const modelSearch = ref('')
// 全目录本地搜索，避免只搜索当前分页导致漏掉已授权模型。
const filteredModels = computed(() => {
  const query = modelSearch.value.trim().toLowerCase()
  return models.value.filter(model =>
    (!statusFilter.value || model.compatibilityStatus === statusFilter.value) &&
    (!query || [model.id, model.name, model.provider].some(value => String(value || '').toLowerCase().includes(query)))
  )
})
const modelCatalogTotal = ref(0)
const sessionToken = ref('')
const updateInfo = ref(null)
const desktopDocker = ref(null)
const desktopAuthorization = ref(null)
const dockerInstalling = ref(false)
const dockerStarting = ref(false)
const dockerProgress = ref({ phase: '', percent: 0, message: '' })
const stackStarting = ref(false)
const stackProgress = ref({ phase: '', percent: 0, message: '' })
const autoStartSaving = ref(false)
const projectPermissions = ref([])
const projectPermissionSaving = ref('')
const dockerStorageRoot = ref('')
// 未启用用户先看到纯说明页。只有用户主动打开向导后才读取 Docker/Rakazo
// 状态；该动作本身不下载、不安装、不启动任何系统环境。
const setupOptIn = ref(false)
let removeDockerProgressListener = null
let removeDockerStartProgressListener = null
let removeRakazoStartAllProgressListener = null
try {
  dockerStorageRoot.value = window.localStorage.getItem('codebot.rakazoDockerStorageRoot') || ''
} catch (_) {}
const form = ref({
  full_access: false,
  enabled: false,
  auto_start: false,
  api_url: 'http://127.0.0.1:3100',
  compose_file: '',
  docker_project_name: 'codebot-rakazo',
  release_channel: 'stable',
  network_policy: 'ask',
  default_project_read: true,
  default_project_write: false,
  default_command_execution: false,
  default_external_network: false,
})

const detail = (error, fallback) => error?.response?.data?.detail || error?.message || fallback
const isElectronDesktop = computed(() => Boolean(window.electronAPI?.installDockerDesktop))
const dockerBusy = computed(() => dockerInstalling.value || dockerStarting.value)
const dockerInstalled = computed(() => Boolean(desktopDocker.value?.installed || status.value?.docker?.installed))
const dockerReady = computed(() => Boolean(desktopDocker.value?.ready || status.value?.docker?.ready))
const runtimeReady = computed(() => Boolean(status.value?.connected && status.value?.compatibility?.compatible))
const authReady = computed(() => Boolean(status.value?.sessionAuthorized))
const modelsReady = computed(() => Number(status.value?.selectableModelCount || 0) > 0)
const setupReady = computed(() => dockerReady.value && runtimeReady.value && authReady.value && modelsReady.value)
const incompleteStepCount = computed(() => [dockerReady.value, runtimeReady.value, authReady.value, modelsReady.value].filter((value) => !value).length)
const shortRevision = computed(() => String(status.value?.runtimeSourceRevision || 'a4ebad0cae4f').slice(0, 12))
const runtimeStepText = computed(() => {
  if (!dockerReady.value) return dockerInstalled.value ? '先启动 Docker Desktop Engine' : '先安装 Docker Desktop'
  if (!modelsReady.value) return '先在 OpenCode 中连接至少一个可用模型'
  if (status.value?.experimentalRuntimeAvailable && !status.value?.experimentalRuntimeEnabled) return '可安装并启动固定提交实验版'
  if (!status.value?.runtimeStartAllowed) return status.value?.runtimeStartReason || '当前通道未通过启动门禁'
  return '已满足本机门禁，可启动并执行合同握手'
})
const canOfferExperimentalInstall = computed(() => Boolean(status.value?.experimentalRuntimeAvailable && !status.value?.experimentalRuntimeEnabled && !runtimeReady.value))
const canInstallExperimental = computed(() => Boolean(dockerReady.value && modelsReady.value && canOfferExperimentalInstall.value))
const canStartRuntime = computed(() => Boolean(dockerReady.value && modelsReady.value && status.value?.runtimeStartAllowed))
const canStartAll = computed(() => Boolean(
  isElectronDesktop.value
  && form.value.enabled
  && dockerInstalled.value
  && (status.value?.experimentalRuntimeEnabled || status.value?.runtimeChannel === 'stable'),
))
const displayRuntimeError = computed(() => {
  // Docker、稳定运行时或授权尚未完成属于安装步骤，不应在同一页面重复渲染成
  // 红色运行故障；只有前置条件齐全后的异常才需要升级为 error。
  if (!dockerReady.value || !modelsReady.value || !status.value?.runtimeStartAllowed || !authReady.value) return ''
  return String(status.value?.lastError || '')
})
const formatTime = (value) => value ? new Date(value).toLocaleString('zh-CN') : '-'
const modelStatusLabel = (model) => {
  const statusValue = model?.compatibilityStatus
  if (statusValue === 'available') return '可直接使用'
  if (statusValue === 'verified') return '已自动验证'
  if (statusValue === 'probe_failed' && model?.probeState === 'unprobed') return '首次使用自动验证'
  if (statusValue === 'probe_failed') return '上次验证失败'
  return ({
    authorization_required: '连接不可代理',
    protocol_pending: '协议待适配',
  }[statusValue] || statusValue || '等待验证')
}
const modelStatusType = (model) => {
  if (model?.compatibilityStatus === 'available') return 'success'
  if (model?.compatibilityStatus === 'verified') return 'success'
  if (model?.compatibilityStatus === 'probe_failed' && model?.probeState === 'unprobed') return 'warning'
  return ({ authorization_required: 'warning', protocol_pending: 'info', probe_failed: 'danger' }[model?.compatibilityStatus] || 'info')
}

const loadModels = async (refresh = false) => {
  modelsLoading.value = true
  try {
    const response = await axios.get('/api/rakazo/models', {
      params: {
        refresh,
      },
    })
    const payload = response.data?.data || {}
    models.value = Array.isArray(payload) ? payload : (payload.models || [])
    modelCatalogTotal.value = Array.isArray(payload)
      ? payload.length
      : Number(payload.openCodeEnabledTotal ?? payload.catalogTotal ?? 0)
  } catch (error) {
    ElMessage.error(detail(error, '加载 Rakazo 模型失败'))
  } finally {
    modelsLoading.value = false
  }
}

const normalizeProjectPermissions = (items) => (Array.isArray(items) ? items : []).map((item) => ({
  projectId: String(item?.project?.project_id || item?.permissions?.projectId || ''),
  projectDir: String(item?.project?.project_dir || ''),
  conversationId: Number(item?.project?.conversation_id || 0),
  projectRead: Boolean(item?.permissions?.projectRead),
  projectWrite: Boolean(item?.permissions?.projectWrite),
  commandExecution: Boolean(item?.permissions?.commandExecution),
  externalNetwork: Boolean(item?.permissions?.externalNetwork),
  commandSupported: Boolean(item?.permissions?.commandSupported),
  externalNetworkSupported: Boolean(item?.permissions?.externalNetworkSupported),
}))

const projectDisplayName = (row) => {
  const normalized = String(row?.projectDir || '').replace(/[\\/]+$/, '')
  return normalized.split(/[\\/]/).pop() || `项目 ${row?.conversationId || ''}`
}

const loadProjectPermissions = async () => {
  try {
    const response = await axios.get('/api/rakazo/projects')
    projectPermissions.value = normalizeProjectPermissions(response.data?.data)
  } catch (error) {
    projectPermissions.value = []
    ElMessage.error(detail(error, '加载 Rakazo 项目权限失败'))
  }
}

const loadAll = async () => {
  loading.value = true
  try {
    const configResponse = await axios.get('/api/config/rakazo')
    form.value = { ...form.value, ...(configResponse.data?.data || {}) }
    if (form.value.enabled) setupOptIn.value = true
    if (!setupOptIn.value) {
      // 关闭状态只读取轻量配置，绝不触发 Docker CLI、运行时健康检查、模型
      // 目录或桌面授权探测。用户现有 OpenCode/Codex 启动链保持不变。
      status.value = null
      desktopDocker.value = null
      desktopAuthorization.value = null
      models.value = []
      modelTotal.value = 0
      modelCatalogTotal.value = 0
      return
    }

    const [statusResult, desktopResult, authResult, projectsResult] = await Promise.allSettled([
      axios.get('/api/rakazo/status'),
      window.electronAPI?.dockerInstallerStatus?.({ storageRoot: dockerStorageRoot.value }),
      window.electronAPI?.rakazoAuthorizationStatus?.(),
      axios.get('/api/rakazo/projects'),
    ])
    if (statusResult.status === 'fulfilled') status.value = statusResult.value.data?.data || null
    if (desktopResult.status === 'fulfilled' && desktopResult.value) desktopDocker.value = desktopResult.value
    if (authResult.status === 'fulfilled' && authResult.value) desktopAuthorization.value = authResult.value
    if (projectsResult.status === 'fulfilled') projectPermissions.value = normalizeProjectPermissions(projectsResult.value.data?.data)
    await loadModels(false)
  } finally {
    loading.value = false
  }
}

const openSetupWizard = async () => {
  setupOptIn.value = true
  await loadAll()
}

const saveConfig = async () => {
  saving.value = true
  try {
    // 只提交设置页真正允许编辑的白名单字段。服务端返回的运行态或实验确认字段
    // 不应被原样回传，否则任一受保护字段报错会让 auto_start 等无关设置一起失败。
    const payload = {
      enabled: Boolean(form.value.enabled),
      auto_start: Boolean(form.value.auto_start),
      api_url: String(form.value.api_url || '').trim(),
      compose_file: String(form.value.compose_file || '').trim(),
      docker_project_name: String(form.value.docker_project_name || '').trim(),
      release_channel: form.value.release_channel,
      network_policy: form.value.default_external_network ? 'allow' : 'deny',
      default_project_read: Boolean(form.value.default_project_read),
      full_access: Boolean(form.value.full_access),
      default_project_write: Boolean(form.value.default_project_write),
      default_command_execution: Boolean(form.value.default_command_execution),
      default_external_network: Boolean(form.value.default_external_network),
    }
    const response = await axios.patch('/api/config/rakazo', payload)
    form.value = { ...form.value, ...(response.data?.data || {}) }
    ElMessage.success('Rakazo 设置已保存')
    await loadAll()
  } catch (error) {
    ElMessage.error(detail(error, '保存 Rakazo 设置失败'))
  } finally {
    saving.value = false
  }
}

const saveAutoStart = async (value) => {
  const requested = Boolean(value)
  autoStartSaving.value = true
  try {
    const response = await axios.patch('/api/config/rakazo', {
      enabled: requested ? true : Boolean(form.value.enabled),
      auto_start: requested,
    })
    form.value.enabled = Boolean(response.data?.data?.enabled)
    form.value.auto_start = Boolean(response.data?.data?.auto_start)
    ElMessage.success(requested ? '已开启随 Codebot 一键恢复 Rakazo' : '已关闭 Rakazo 自动启动')
  } catch (error) {
    form.value.auto_start = !requested
    ElMessage.error(detail(error, '保存 Rakazo 自动启动设置失败'))
  } finally {
    autoStartSaving.value = false
  }
}

const saveDefaultPermission = async (field, value) => {
  const allowed = new Set([
    'default_project_read',
    'default_project_write',
    'default_command_execution',
    'default_external_network',
  ])
  if (!allowed.has(field)) return
  const requested = Boolean(value)
  if (requested && field !== 'default_project_read') {
    const labels = {
      default_project_write: '默认允许新项目写入宿主项目文件',
      default_command_execution: '默认允许新项目在专属 Computer 容器内执行命令',
      default_external_network: '默认允许新项目的 Computer 访问外部网络',
    }
    try {
      await ElMessageBox.confirm(
        `${labels[field]}。该设置只影响以后新建的 Rakazo 项目，是否继续？`,
        '确认新项目默认权限',
        { confirmButtonText: '确认开启', cancelButtonText: '取消', type: 'warning' },
      )
    } catch {
      form.value[field] = false
      return
    }
  }
  try {
    const payload = { [field]: requested }
    if (field === 'default_external_network') payload.network_policy = requested ? 'allow' : 'deny'
    const response = await axios.patch('/api/config/rakazo', payload)
    form.value[field] = Boolean(response.data?.data?.[field])
    form.value.network_policy = response.data?.data?.network_policy || form.value.network_policy
    ElMessage.success('新项目默认权限已保存')
  } catch (error) {
    form.value[field] = !requested
    ElMessage.error(detail(error, '保存新项目默认权限失败'))
  }
}

const saveProjectPermission = async (row, field, value) => {
  if (!row?.projectId || !['projectRead', 'projectWrite', 'commandExecution', 'externalNetwork'].includes(field)) return
  const requested = Boolean(value)
  if (requested && ['projectWrite', 'commandExecution', 'externalNetwork'].includes(field)) {
    const descriptions = {
      projectWrite: '允许 Rakazo 通过受控项目工具写入该项目',
      commandExecution: '允许 Rakazo 在项目专属 Computer 容器内执行命令',
      externalNetwork: '允许该项目 Computer 访问外部网络；切换会重建容器与私有网络，但保留 home 数据',
    }
    try {
      await ElMessageBox.confirm(
        `${descriptions[field]}。是否继续？`,
        '确认项目权限',
        { confirmButtonText: '确认开启', cancelButtonText: '取消', type: 'warning' },
      )
    } catch {
      row[field] = false
      return
    }
  }
  projectPermissionSaving.value = row.projectId
  try {
    const response = await axios.put(`/api/rakazo/projects/${encodeURIComponent(row.projectId)}/permissions`, {
      projectRead: field === 'projectRead' ? requested : Boolean(row.projectRead),
      projectWrite: field === 'projectWrite' ? requested : Boolean(row.projectWrite),
      commandExecution: field === 'commandExecution' ? requested : Boolean(row.commandExecution),
      externalNetwork: field === 'externalNetwork' ? requested : Boolean(row.externalNetwork),
      networkPolicy: (field === 'externalNetwork' ? requested : Boolean(row.externalNetwork)) ? 'allow' : 'deny',
    })
    const saved = response.data?.data || {}
    row.projectRead = Boolean(saved.projectRead)
    row.projectWrite = Boolean(saved.projectWrite)
    row.commandExecution = Boolean(saved.commandExecution)
    row.externalNetwork = Boolean(saved.externalNetwork)
    ElMessage.success(`${projectDisplayName(row)} 权限已保存`)
  } catch (error) {
    row[field] = !requested
    ElMessage.error(detail(error, '保存 Rakazo 项目权限失败'))
  } finally {
    projectPermissionSaving.value = ''
  }
}

const startAll = async () => {
  if (!window.electronAPI?.startAllRakazo) {
    ElMessage.error('Rakazo 一键启动只在 Codebot Windows 桌面版可用')
    return
  }
  stackStarting.value = true
  stackProgress.value = { phase: 'checking', percent: 1, message: '正在启动四项 Rakazo 环境' }
  try {
    // 一键启动属于明确的启用动作；先持久化 enabled，auto_start 则完全服从用户
    // 当前开关，不会因为一次手动启动就偷偷改为开机自动启动。
    await axios.patch('/api/config/rakazo', {
      enabled: true,
      auto_start: Boolean(form.value.auto_start),
    })
    form.value.enabled = true
    const result = await window.electronAPI.startAllRakazo({ storageRoot: dockerStorageRoot.value || undefined })
    ElMessage.success(`Rakazo 四项环境已就绪，可用模型 ${Number(result?.selectableModelCount || 0)} 个`)
    await loadAll()
  } catch (error) {
    const message = detail(error, 'Rakazo 一键启动失败')
    stackProgress.value = { phase: 'error', percent: stackProgress.value.percent || 0, message }
    ElMessage.error(message)
  } finally {
    stackStarting.value = false
  }
}

const controlRuntime = async (action) => {
  runtimeAction.value = action
  try {
    await axios.post('/api/rakazo/runtime', { action })
    const label = action === 'install_experimental' ? '安装并启动固定实验版' : action === 'start' ? '启动' : action === 'stop' ? '停止' : '重启'
    ElMessage.success(`Rakazo 已${label}`)
    await loadAll()
  } catch (error) {
    ElMessage.error(detail(error, 'Rakazo 运行时操作失败'))
  } finally {
    runtimeAction.value = ''
  }
}

const saveAuthorization = async () => {
  authSaving.value = true
  try {
    await axios.put('/api/rakazo/authorization', { token: sessionToken.value.trim() })
    sessionToken.value = ''
    ElMessage.success('Rakazo 授权已保存并验证')
    await loadAll()
  } catch (error) {
    ElMessage.error(detail(error, 'Rakazo 授权验证失败'))
  } finally {
    authSaving.value = false
  }
}

const bootstrapAuthorization = async () => {
  if (!window.electronAPI?.bootstrapRakazoAuthorization) {
    ElMessage.error('一键授权只在 Codebot 桌面版可用')
    return
  }
  authSaving.value = true
  try {
    await window.electronAPI.bootstrapRakazoAuthorization()
    ElMessage.success('本机 Rakazo 账号已创建，凭据已由 Windows 安全存储加密')
    await loadAll()
  } catch (error) {
    ElMessage.error(detail(error, 'Rakazo 一键授权失败'))
  } finally {
    authSaving.value = false
  }
}

const clearAuthorization = async () => {
  authSaving.value = true
  try {
    if (window.electronAPI?.clearRakazoAuthorization) await window.electronAPI.clearRakazoAuthorization()
    else await axios.delete('/api/rakazo/authorization')
    sessionToken.value = ''
    ElMessage.success('Rakazo 授权已清除')
    await loadAll()
  } catch (error) {
    ElMessage.error(detail(error, '清除授权失败'))
  } finally {
    authSaving.value = false
  }
}

const selectDockerStorage = async () => {
  if (!window.electronAPI?.selectFolder) {
    ElMessage.error('目录选择与 Docker 安装只在 Codebot 桌面版可用')
    return
  }
  const selected = await window.electronAPI.selectFolder({
    title: '选择 Docker、WSL 与 Rakazo 大文件存储目录',
    defaultPath: dockerStorageRoot.value || undefined,
  })
  if (!selected) return
  dockerStorageRoot.value = selected
  try { window.localStorage.setItem('codebot.rakazoDockerStorageRoot', selected) } catch (_) {}
  if (window.electronAPI?.dockerInstallerStatus) {
    try {
      desktopDocker.value = await window.electronAPI.dockerInstallerStatus({ storageRoot: selected })
    } catch (_) {}
  }
}

const installDocker = async () => {
  if (!window.electronAPI?.installDockerDesktop) {
    ElMessage.error('Docker 一键安装只在 Codebot Windows 桌面版可用')
    return
  }
  dockerInstalling.value = true
  dockerProgress.value = { phase: 'preflight', percent: 1, message: '正在检查系统与安装目录' }
  try {
    const result = await window.electronAPI.installDockerDesktop({ storageRoot: dockerStorageRoot.value })
    if (result?.cancelled) return
    if (result?.rebootRequired) {
      dockerProgress.value = { phase: 'reboot-required', percent: 6, message: result.message }
      ElMessage.warning(result.message)
    } else {
      ElMessage.success(result?.alreadyInstalled ? '已识别现有 Docker Desktop 并启动 Engine' : 'Docker Desktop 已安装并启动')
    }
    await loadAll()
  } catch (error) {
    const message = detail(error, 'Docker Desktop 安装失败')
    dockerProgress.value = { phase: 'error', percent: dockerProgress.value.percent || 0, message }
    ElMessage.error(message)
  } finally {
    dockerInstalling.value = false
  }
}

const startDocker = async () => {
  if (!window.electronAPI?.startDockerDesktop) {
    ElMessage.error('启动 Docker 只在 Codebot Windows 桌面版可用')
    return
  }
  dockerStarting.value = true
  dockerProgress.value = { phase: 'starting', percent: 1, message: '正在定位并启动已有 Docker Desktop' }
  try {
    const result = await window.electronAPI.startDockerDesktop({ storageRoot: dockerStorageRoot.value || undefined })
    ElMessage.success(result?.alreadyReady ? 'Docker Engine 已经就绪' : 'Docker Desktop 已启动，Engine 已就绪')
    await loadAll()
  } catch (error) {
    const message = detail(error, 'Docker Desktop 启动失败')
    dockerProgress.value = { phase: 'error', percent: dockerProgress.value.percent || 0, message }
    ElMessage.error(message)
  } finally {
    dockerStarting.value = false
  }
}

const checkUpdate = async () => {
  updateLoading.value = true
  try {
    const response = await axios.get('/api/rakazo/update/check')
    updateInfo.value = response.data?.data || null
    ElMessage.info(updateInfo.value?.message || '检查完成')
  } catch (error) {
    ElMessage.error(detail(error, '检查更新失败'))
  } finally {
    updateLoading.value = false
  }
}

const openExternal = async (url) => {
  if (window.electronAPI?.openExternal) await window.electronAPI.openExternal(url)
  else window.open(url, '_blank', 'noopener,noreferrer')
}

onMounted(() => {
  if (window.electronAPI?.onDockerInstallProgress) {
    removeDockerProgressListener = window.electronAPI.onDockerInstallProgress((progress) => {
      dockerProgress.value = {
        phase: progress?.phase || '',
        percent: Math.max(0, Math.min(100, Number(progress?.percent || 0))),
        message: progress?.message || '',
      }
    })
  }
  if (window.electronAPI?.onDockerStartProgress) {
    removeDockerStartProgressListener = window.electronAPI.onDockerStartProgress((progress) => {
      dockerProgress.value = {
        phase: progress?.phase || '',
        percent: Math.max(0, Math.min(100, Number(progress?.percent || 0))),
        message: progress?.message || '',
      }
    })
  }
  if (window.electronAPI?.onRakazoStartAllProgress) {
    removeRakazoStartAllProgressListener = window.electronAPI.onRakazoStartAllProgress((progress) => {
      stackProgress.value = {
        phase: progress?.phase || '',
        percent: Math.max(0, Math.min(100, Number(progress?.percent || 0))),
        message: progress?.message || '',
      }
    })
  }
  loadAll()
})

onUnmounted(() => {
  if (typeof removeDockerProgressListener === 'function') removeDockerProgressListener()
  if (typeof removeDockerStartProgressListener === 'function') removeDockerStartProgressListener()
  if (typeof removeRakazoStartAllProgressListener === 'function') removeRakazoStartAllProgressListener()
})
</script>

<style scoped>
.rakazo-settings { padding: 20px; }
.optional-feature-card { display: flex; align-items: center; justify-content: space-between; gap: 40px; min-height: 300px; padding: 42px; border: 1px solid var(--el-border-color-light); border-radius: 16px; background: linear-gradient(135deg, var(--el-fill-color-extra-light), var(--el-bg-color)); }
.optional-feature-copy { max-width: 760px; }
.optional-feature-copy h2 { margin: 5px 0 12px; font-size: 26px; color: var(--el-text-color-primary); }
.optional-feature-copy p { margin: 0; color: var(--el-text-color-regular); line-height: 1.75; }
.optional-feature-copy ul { margin: 18px 0 0; padding-left: 20px; color: var(--el-text-color-secondary); line-height: 1.9; }
.optional-feature-actions { display: flex; min-width: 230px; flex-direction: column; align-items: stretch; gap: 14px; }
.setup-hero { display: flex; align-items: flex-start; justify-content: space-between; gap: 24px; padding: 24px; border: 1px solid var(--el-border-color-light); border-radius: 14px; background: linear-gradient(135deg, var(--el-fill-color-extra-light), var(--el-bg-color)); }
.setup-hero h2 { margin: 3px 0 7px; font-size: 22px; color: var(--el-text-color-primary); }
.setup-hero p { max-width: 760px; margin: 0; color: var(--el-text-color-regular); line-height: 1.65; }
.setup-hero-actions { display: flex; min-width: 220px; flex-direction: column; align-items: stretch; gap: 10px; }
.start-all-tip { max-width: 280px; margin-top: 0; line-height: 1.55; }
.eyebrow { color: var(--el-color-primary); font-size: 11px; font-weight: 700; letter-spacing: .12em; }
.setup-steps { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; margin-top: 14px; }
.setup-step { display: grid; grid-template-columns: 32px minmax(0, 1fr) auto; align-items: center; gap: 10px; min-height: 70px; padding: 12px; border: 1px solid var(--el-border-color-light); border-radius: 12px; background: var(--el-bg-color); }
.setup-step.complete { border-color: var(--el-color-success-light-5); background: var(--el-color-success-light-9); }
.step-index { display: grid; place-items: center; width: 28px; height: 28px; border-radius: 50%; background: var(--el-fill-color); color: var(--el-text-color-regular); font-weight: 700; }
.setup-step.complete .step-index { background: var(--el-color-success); color: white; }
.step-copy { display: flex; min-width: 0; flex-direction: column; gap: 4px; }
.step-copy strong { font-size: 14px; }
.step-copy span { overflow: hidden; color: var(--el-text-color-secondary); font-size: 12px; text-overflow: ellipsis; white-space: nowrap; }
.install-grid { display: grid; grid-template-columns: minmax(0, 1.25fr) minmax(340px, .75fr); gap: 16px; }
.surface-card { padding: 18px; border: 1px solid var(--el-border-color-light); border-radius: 12px; background: var(--el-bg-color); }
.primary-card { box-shadow: 0 8px 24px rgba(31, 94, 255, .06); }
.card-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; margin-bottom: 14px; }
.card-heading h3 { margin: 0 0 6px; font-size: 16px; }
.card-heading p { margin: 0; color: var(--el-text-color-secondary); font-size: 13px; line-height: 1.55; }
.directory-row { display: grid; grid-template-columns: minmax(240px, 1fr) auto auto; gap: 10px; }
.install-progress { margin-top: 14px; }
.progress-copy { margin-top: 6px; }
.storage-facts { display: grid; gap: 8px; margin: 16px 0 8px; padding: 12px; border-radius: 9px; background: var(--el-fill-color-light); }
.storage-facts > div { display: grid; grid-template-columns: 112px minmax(0, 1fr); gap: 10px; align-items: baseline; }
.storage-facts span { color: var(--el-text-color-secondary); font-size: 12px; }
.storage-facts code { overflow-wrap: anywhere; color: var(--el-text-color-primary); font-size: 12px; }
.runtime-identity { display: grid; gap: 7px; margin: 14px 0; padding: 11px 12px; border-radius: 9px; background: var(--el-fill-color-light); }
.runtime-identity > div { display: grid; grid-template-columns: 86px minmax(0, 1fr); gap: 8px; align-items: baseline; }
.runtime-identity span { color: var(--el-text-color-secondary); font-size: 12px; }
.runtime-identity code { overflow-wrap: anywhere; color: var(--el-text-color-primary); font-size: 11px; }
.status-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: 10px 18px; }
.status-grid > div { display: grid; grid-template-columns: 100px minmax(0, 1fr); align-items: center; min-height: 30px; }
.status-grid code { word-break: break-all; }
.section-alert { margin-top: 14px; }
.action-row { display: flex; gap: 10px; flex-wrap: wrap; margin-top: 16px; }
.field-tip { width: 100%; margin-top: 4px; color: var(--el-text-color-secondary); font-size: 12px; }
.token-row { display: grid; grid-template-columns: minmax(260px, 1fr) auto; gap: 10px; }
.token-tip { margin-bottom: 10px; }
.authorization-card { max-width: 980px; }
.project-permissions-card { margin-top: 4px; }
.project-identity { display: flex; min-width: 0; flex-direction: column; gap: 4px; }
.project-identity strong { color: var(--el-text-color-primary); }
.project-identity code { overflow: hidden; color: var(--el-text-color-secondary); font-size: 12px; text-overflow: ellipsis; white-space: nowrap; }
.auth-actions { margin-top: 0; }
.advanced-auth { margin-top: 14px; border-top: 1px solid var(--el-border-color-lighter); }
.table-toolbar { display: flex; align-items: center; justify-content: space-between; gap: 16px; margin-bottom: 10px; color: var(--el-text-color-secondary); }
.table-actions { display: flex; align-items: center; gap: 10px; }
.model-pagination { display: flex; align-items: center; justify-content: space-between; gap: 16px; margin-top: 12px; color: var(--el-text-color-secondary); }
.update-row { margin-top: 14px; }
.update-detail { max-height: 260px; overflow: auto; padding: 12px; background: var(--el-fill-color-light); border-radius: 8px; white-space: pre-wrap; word-break: break-all; }
@media (max-width: 760px) {
  .rakazo-settings { padding: 12px; }
  .optional-feature-card { align-items: stretch; min-height: 0; padding: 24px 20px; flex-direction: column; gap: 24px; }
  .optional-feature-actions { min-width: 0; }
  .setup-hero, .card-heading { flex-direction: column; }
  .setup-hero-actions { width: 100%; min-width: 0; }
  .setup-steps, .install-grid { grid-template-columns: 1fr; }
  .directory-row, .token-row { grid-template-columns: 1fr; }
  .setup-step { grid-template-columns: 32px minmax(0, 1fr); }
  .setup-step > .el-tag { grid-column: 2; justify-self: start; }
  .storage-facts > div { grid-template-columns: 1fr; gap: 3px; }
  .table-toolbar, .model-pagination { align-items: flex-start; flex-direction: column; }
  .table-actions { width: 100%; flex-wrap: wrap; }
}
@media (min-width: 761px) and (max-width: 1180px) {
  .setup-steps { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .install-grid { grid-template-columns: 1fr; }
}
</style>
