<template>
  <div class="chat-container" :class="{ 'compact-mode': compactMode }">
    <el-container>
      <!-- 侧边栏 - 对话列表 -->
      <el-aside width="280px">
        <div class="conversation-header">
          <el-button type="primary" @click="openNewConversationDialog" class="new-conv-btn">
            <el-icon><Plus /></el-icon>
            新建对话
          </el-button>
          <el-button 
            :type="batchMode ? 'warning' : 'default'" 
            @click="toggleBatchMode"
            class="batch-btn"
          >
            <el-icon><Grid /></el-icon>
            {{ batchMode ? '退出' : '批量处理' }}
          </el-button>
        </div>

        <!-- 批量操作工具栏 -->
        <div v-if="batchMode" class="batch-toolbar">
          <el-checkbox 
            v-model="selectAll" 
            :indeterminate="isIndeterminate"
            @change="handleSelectAll"
          >全选 ({{ selectedConvIds.length }}/{{ deletableConversations.length }})</el-checkbox>
          <div class="batch-actions">
            <el-button 
              size="small" 
              type="danger" 
              :disabled="selectedConvIds.length === 0"
              @click="batchDeleteConversations"
            >
              <el-icon><Delete /></el-icon>
              删除所选 ({{ selectedConvIds.length }})
            </el-button>
          </div>
        </div>

        <!-- 多 Agent 是低频协作入口，与普通对话分区并默认折叠。 -->
        <div v-if="multiAgentHubConversation" class="multi-agent-entry">
          <button
            type="button"
            class="multi-agent-entry-toggle"
            :aria-expanded="multiAgentEntryExpanded"
            @click="multiAgentEntryExpanded = !multiAgentEntryExpanded"
          >
            <span>多Agent群聊</span>
            <span class="multi-agent-entry-meta">{{ multiAgentMembers.length }} 位成员 {{ multiAgentEntryExpanded ? '▴' : '▾' }}</span>
          </button>
          <div v-if="multiAgentEntryExpanded" class="multi-agent-entry-body">
            <button
              type="button"
              class="multi-agent-hub-link"
              :class="{ active: currentConversationId === multiAgentHubConversation.id }"
              @click="selectConversation(multiAgentHubConversation)"
            >
              <span class="multi-agent-hub-title">{{ multiAgentHubConversation.title || '多Agent群聊' }}</span>
              <span class="conversation-time">{{ formatDate(multiAgentHubConversation.updated_at) }}</span>
            </button>
          </div>
        </div>

        <!-- 对话搜索框 -->
        <div class="conversation-search">
          <el-input
            v-model="conversationSearchQuery"
            placeholder="搜索对话..."
            clearable
            size="small"
            :prefix-icon="Search"
          />
        </div>
        
        <div class="conversation-list">
          <div
            v-for="conv in filteredConversations"
            :key="conv.id"
            class="conversation-item"
            :class="{ active: currentConversationId === conv.id, 'batch-selected': selectedConvIds.includes(conv.id), 'multi-agent-hub-item': isMultiAgentHub(conv) }"
            @click="batchMode ? (conv.executor !== 'rakazo' && toggleConvSelection(conv.id)) : selectConversation(conv.id)"
          >
            <div v-if="batchMode" class="batch-checkbox" @click.stop>
              <el-checkbox
                :model-value="selectedConvIds.includes(conv.id)"
                :disabled="conv.executor === 'rakazo'"
                @change="toggleConvSelection(conv.id)"
              />
            </div>
            <div class="conversation-info">
              <div class="conversation-title">
                <span class="conversation-title-text">{{ conv.title }}</span>
                <el-tag v-if="conv.is_pinned" size="small" type="info">置顶</el-tag>
                <el-tag v-if="isMultiAgentHub(conv)" size="small" type="danger">多Agent</el-tag>
                <el-tag v-else-if="conv.is_group" size="small" type="success">{{ conv.group_role || 'Agent' }}</el-tag>
                <el-tag v-if="conv.executor === 'rakazo'" size="small" type="primary">Rakazo</el-tag>
                <el-tag v-else-if="conv.executor === 'codex'" size="small" type="success">Codex</el-tag>
                <el-tag v-if="conv.project_dir" size="small" type="warning" :title="conv.project_dir">📁</el-tag>
              </div>
              <div class="conversation-time">{{ formatDate(conv.updated_at) }}</div>
            </div>
            <div v-if="!batchMode" class="conversation-actions">
              <el-dropdown @command="(command) => handleConversationCommand(conv, command)" trigger="click">
                <el-button text size="small">•••</el-button>
                <template #dropdown>
                  <el-dropdown-menu>
                    <el-dropdown-item command="share">分享</el-dropdown-item>
                    <el-dropdown-item v-if="!isMultiAgentHub(conv)" command="handoff">交接到其他执行器</el-dropdown-item>
                    <el-dropdown-item v-if="!isMultiAgentHub(conv) && conv.executor !== 'rakazo'" :command="conv.is_group ? 'ungroup' : 'group'">
                      {{ conv.is_group ? '退出多Agent群聊' : '加入多Agent群聊' }}
                    </el-dropdown-item>
                    <el-dropdown-item v-if="!isMultiAgentHub(conv)" command="rename">重命名</el-dropdown-item>
                    <el-dropdown-item v-if="!isMultiAgentHub(conv)" :command="conv.is_pinned ? 'unpin' : 'pin'">
                      {{ conv.is_pinned ? '取消置顶' : '置顶聊天' }}
                    </el-dropdown-item>
                    <el-dropdown-item v-if="!isMultiAgentHub(conv)" command="archive">归档</el-dropdown-item>
                    <el-dropdown-item :command="isMultiAgentHub(conv) ? 'clear' : 'delete'" divided>
                      {{ isMultiAgentHub(conv) ? '清空' : (conv.executor === 'rakazo' ? '删除并清理项目' : '删除') }}
                    </el-dropdown-item>
                  </el-dropdown-menu>
                </template>
              </el-dropdown>
            </div>
          </div>
          
          <el-empty v-if="filteredConversations.length === 0 && !conversationSearchQuery" description="暂无对话" />
          <el-empty v-else-if="filteredConversations.length === 0 && conversationSearchQuery" description="无匹配对话" />
        </div>
      </el-aside>
      
      <!-- 主聊天区域 -->
      <el-main>
          <div class="chat-main" v-if="currentConversationId">
          <div v-if="currentConversation?.conversation_type === 'multi_agent_hub'" class="multi-agent-members">
            <el-tag v-for="member in multiAgentMembers" :key="member.id" type="success" effect="plain">
              {{ member.group_role || member.title }} #{{ member.id }}
            </el-tag>
            <span v-if="multiAgentMembers.length === 0" class="empty-members">暂无成员，请在普通对话菜单中选择“加入多Agent群聊”。</span>
            <el-button size="small" text class="refresh-members-btn" @click="loadMultiAgentMembers">刷新成员</el-button>
          </div>
          <div v-if="isRakazoConversation" class="rakazo-session-bar">
            <div class="rakazo-session-identity">
              <strong>Rakazo 主会话</strong>
              <el-tag size="small" :type="rakazoProjectState?.active ? 'warning' : 'success'">{{ rakazoProjectState?.active ? '运行中' : '空闲' }}</el-tag>
              <el-tag size="small" :type="rakazoProjectState?.computer?.state === 'running' ? 'success' : 'info'">{{ rakazoComputerStateLabel }}</el-tag>
            </div>
            <div class="rakazo-session-controls">
              <span class="rakazo-model-label">模型</span>
              <el-select
                v-model="selectedModel"
                class="rakazo-model-select"
                size="small"
                filterable
                :filter-method="filterModels"
                :loading="modelsLoading || rakazoModelSwitching"
                :disabled="currentLoading || rakazoProjectState?.active || rakazoModelSwitching"
                no-data-text="OpenCode 当前没有可用模型"
                popper-class="model-select-popper"
                @visible-change="onModelDropdownOpen"
              >
                <template v-if="modelSearchQuery">
                  <el-option
                    v-for="model in filteredModels"
                    :key="model.id"
                    :label="model.name"
                    :value="model.id"
                    :disabled="model.selectable === false || model.runnable === false"
                  >
                    <span class="model-option-name">{{ model.model }}</span>
                    <span class="model-option-provider">{{ modelProviderLabel(model) }}</span>
                  </el-option>
                </template>
                <template v-else>
                  <el-option-group
                    v-for="group in groupedModels"
                    :key="group.provider"
                    :label="group.providerLabel"
                  >
                    <el-option
                      v-for="model in group.models"
                      :key="model.id"
                      :label="model.name"
                      :value="model.id"
                      :disabled="model.selectable === false || model.runnable === false"
                    >
                      <span class="model-option-name">{{ model.model }}</span>
                      <span class="model-option-provider">{{ modelProviderLabel(model) }}</span>
                    </el-option>
                  </el-option-group>
                </template>
              </el-select>
              <el-button
                size="small"
                text
                :loading="modelsLoading"
                :disabled="currentLoading || rakazoModelSwitching"
                title="同步 OpenCode 当前模型"
                @click="loadModels({ manual: true, rakazo: true })"
              >
                <el-icon><Refresh /></el-icon>
              </el-button>
              <el-button size="small" @click="openRakazoDrawer">Rakazo 状态</el-button>
            </div>
          </div>
          <el-alert
            v-if="isRakazoConversation && rakazoProjectState?.routeDrift"
            class="rakazo-route-alert"
            :title="rakazoProjectState.routeDriftReason"
            type="error"
            show-icon
            :closable="false"
          />
          <div v-if="thirdPartyStatus && !isRakazoConversation" class="third-party-banner">
            <el-tag size="small" :type="thirdPartyStatus.opencode_connected ? 'success' : 'info'">
              {{ thirdPartyStatus.opencode_connected ? 'OpenCode 已连接' : 'OpenCode 未连接' }}
            </el-tag>
            <el-tag size="small" :type="thirdPartyStatus.bridge_status?.registered ? 'success' : 'warning'">
              {{ thirdPartyStatus.bridge_status?.registered ? 'Bridge 已注册' : 'Bridge 待注册' }}
            </el-tag>
            <el-tag size="small" type="info">
              代理 {{ thirdPartyStatus.proxied_server_count || 0 }} 个 MCP
            </el-tag>
            <el-tag size="small" type="info">
              工具 {{ thirdPartyStatus.proxied_tool_count || 0 }} 个
            </el-tag>
            <el-button
              size="small"
              text
              :loading="thirdPartyStatusRefreshing"
              @click="refreshThirdPartyConnections"
              title="刷新 OpenCode / Bridge / MCP 连接状态"
              class="refresh-btn"
            >
              <el-icon><Refresh /></el-icon>
            </el-button>
          </div>
          <!-- 消息列表 -->
          <div class="message-list" ref="messageListRef" @scroll.passive="onMessageListScroll">
            <template v-for="msg in messages" :key="msg.id">
              <!-- 工具步骤事件：独立气泡展示，不显示头像 -->
              <div v-if="msg.role === 'event'" class="message event-message">
                <div class="tool-event-item" :class="classifyCodexEventClass(msg.event)">
                  <div class="tool-event-header" @click="toggleEventDetails(msg)">
                    <span class="tool-event-type">{{ toolEventLabel(msg.event) }}</span>
                    <span class="tool-event-summary">{{ toolEventSummary(msg.event) }}</span>
                    <span v-if="toolEventDetail(msg.event)" class="tool-event-toggle">
                      {{ msg.expanded ? '收起' : '详情' }}
                    </span>
                  </div>
                  <el-collapse-transition>
                    <div v-if="msg.expanded && toolEventDetail(msg.event)" class="tool-event-detail markdown-body" v-html="renderMarkdown(toolEventDetail(msg.event))"></div>
                  </el-collapse-transition>
                  <div v-if="shouldShowQuestionPanel(msg.event)" class="question-panel-host structured-question-panel-host">
                    <div class="question-panel">
                      <div class="question-panel-header">
                        <div class="question-panel-title">{{ questionPanelProgress(msg.event) }}</div>
                        <div class="question-panel-progress">
                          <button
                            v-for="(question, index) in getQuestionEventQuestions(msg.event)"
                            :key="`${msg.event.request_id || 'question'}-${index}`"
                            type="button"
                            class="question-progress-dot"
                            :class="{
                              active: getQuestionPanelTab(msg.event) === index,
                              answered: isQuestionPanelAnswered(msg.event, index)
                            }"
                            :disabled="isQuestionPanelSending(msg.event)"
                            @click="setQuestionPanelTab(msg.event, index)"
                          />
                        </div>
                      </div>
                      <div class="question-panel-body">
                        <div class="question-panel-question">{{ currentQuestionPanelQuestion(msg.event)?.question || msg.event?.question }}</div>
                        <div class="question-panel-hint">
                          {{ currentQuestionPanelQuestion(msg.event)?.multiple ? '可多选，完成后统一提交' : '单选，选择后可继续下一题或直接提交' }}
                        </div>
                        <div class="question-panel-options">
                          <button
                            v-for="option in currentQuestionPanelQuestion(msg.event)?.options || []"
                            :key="option.value || option.label"
                            type="button"
                            class="question-option"
                            :class="{ picked: isQuestionOptionPicked(msg.event, option.value || option.label) }"
                            :disabled="isQuestionPanelSending(msg.event)"
                            @click="toggleQuestionOption(msg.event, option.value || option.label)"
                          >
                            <span class="question-option-mark" :class="{ multi: currentQuestionPanelQuestion(msg.event)?.multiple, picked: isQuestionOptionPicked(msg.event, option.value || option.label) }"></span>
                            <span class="question-option-main">
                              <span class="question-option-label">{{ option.label }}</span>
                              <span v-if="option.description" class="question-option-description">{{ option.description }}</span>
                            </span>
                          </button>
                          <button
                            v-if="currentQuestionPanelQuestion(msg.event)?.custom"
                            type="button"
                            class="question-option"
                            :class="{ picked: isQuestionCustomEnabled(msg.event) }"
                            :disabled="isQuestionPanelSending(msg.event)"
                            @click="toggleQuestionCustom(msg.event)"
                          >
                            <span class="question-option-mark" :class="{ multi: currentQuestionPanelQuestion(msg.event)?.multiple, picked: isQuestionCustomEnabled(msg.event) }"></span>
                            <span class="question-option-main">
                              <span class="question-option-label">自定义回答</span>
                              <span class="question-option-description">{{ currentQuestionCustomValue(msg.event) || '输入你自己的回答' }}</span>
                            </span>
                          </button>
                          <el-input
                            v-if="currentQuestionPanelQuestion(msg.event)?.custom && isQuestionCustomEnabled(msg.event) && currentQuestionPanelQuestion(msg.event)?.input_type === 'password'"
                            :model-value="currentQuestionCustomValue(msg.event)"
                            type="password"
                            show-password
                            placeholder="输入自定义回答"
                            :disabled="isQuestionPanelSending(msg.event)"
                            @update:model-value="updateQuestionCustomValue(msg.event, $event)"
                          />
                          <el-input
                            v-else-if="currentQuestionPanelQuestion(msg.event)?.custom && isQuestionCustomEnabled(msg.event)"
                            :model-value="currentQuestionCustomValue(msg.event)"
                            type="textarea"
                            :rows="2"
                            resize="none"
                            :placeholder="currentQuestionPanelQuestion(msg.event)?.placeholder || '输入自定义回答'"
                            :disabled="isQuestionPanelSending(msg.event)"
                            @update:model-value="updateQuestionCustomValue(msg.event, $event)"
                          />
                        </div>
                      </div>
                      <div class="question-panel-footer">
                        <el-button size="small" :disabled="isQuestionPanelSending(msg.event)" @click="cancelQuestionPanel(msg.event)">取消</el-button>
                        <div class="question-panel-footer-actions">
                          <el-button
                            v-if="getQuestionPanelTab(msg.event) > 0"
                            size="small"
                            :disabled="isQuestionPanelSending(msg.event)"
                            @click="setQuestionPanelTab(msg.event, getQuestionPanelTab(msg.event) - 1)"
                          >上一题</el-button>
                          <el-button
                            v-if="getQuestionPanelTab(msg.event) < getQuestionEventQuestions(msg.event).length - 1"
                            size="small"
                            :disabled="isQuestionPanelSending(msg.event)"
                            @click="setQuestionPanelTab(msg.event, getQuestionPanelTab(msg.event) + 1)"
                          >下一题</el-button>
                          <el-button
                            type="primary"
                            size="small"
                            :loading="isQuestionPanelSending(msg.event)"
                            @click="submitQuestionPanel(msg.event)"
                          >提交</el-button>
                        </div>
                      </div>
                    </div>
                  </div>
                  <div v-if="toolEventActions(msg.event).length > 0" class="tool-event-actions">
                    <el-button
                      v-for="action in toolEventActions(msg.event)"
                      :key="`${action.reply}:${action.label}`"
                      size="small"
                      :type="action.type || 'primary'"
                      :loading="msg.event.replying === toolEventActionKey(action)"
                      :disabled="Boolean(msg.event.replied)"
                      @click.stop="replyToolAction(msg.event, action)"
                    >
                      {{ action.label }}
                    </el-button>
                  </div>
                </div>
              </div>

              <!-- 普通用户/助手消息气泡 -->
              <div v-else class="message" :class="[msg.role, { 'cli-message': isCliDisplayMessage(msg) }]">
                <div class="message-avatar">
                  <el-avatar v-if="msg.role === 'user'" icon="User" />
                  <el-avatar v-else :src="assistantAvatarUrl" />
                </div>
                <div class="message-content">
                  <div v-if="msg.role === 'assistant' && msg.source === 'codex'" class="message-source-badge">Codex Agent Harness</div>
                  <div v-else-if="msg.role === 'assistant' && msg.source === 'rakazo'" class="message-source-badge rakazo-source-badge">Rakazo</div>
                  <div v-if="isCliDisplayMessage(msg)" class="cli-output">{{ msg.content }}</div>
                  <div v-else-if="msg.streaming" class="message-text streaming-text">{{ msg.content }}</div>
                  <div v-else class="message-text markdown-body" v-html="renderMarkdown(msg.content)"></div>
                  <div v-if="msg.role === 'assistant' && toolEventActions(msg.pendingActionEvent).length > 0" class="cli-action-bar">
                    <el-button
                      v-for="action in toolEventActions(msg.pendingActionEvent)"
                      :key="`${action.reply}:${action.label}`"
                      size="small"
                      :type="action.type || 'primary'"
                      :loading="msg.pendingActionEvent.replying === toolEventActionKey(action)"
                      :disabled="Boolean(msg.pendingActionEvent.replied)"
                      @click.stop="replyToolAction(msg.pendingActionEvent, action)"
                    >
                      {{ action.label }}
                    </el-button>
                  </div>
                  <div
                    v-if="msg.role === 'assistant' && shouldShowQuestionPanel(msg.pendingActionEvent)"
                    class="question-panel-host"
                  >
                    <div class="question-panel">
                      <div class="question-panel-header">
                        <div class="question-panel-title">{{ questionPanelProgress(msg.pendingActionEvent) }}</div>
                        <div class="question-panel-progress">
                          <button
                            v-for="(question, index) in getQuestionEventQuestions(msg.pendingActionEvent)"
                            :key="`${msg.pendingActionEvent.request_id || 'question'}-${index}`"
                            type="button"
                            class="question-progress-dot"
                            :class="{
                              active: getQuestionPanelTab(msg.pendingActionEvent) === index,
                              answered: isQuestionPanelAnswered(msg.pendingActionEvent, index)
                            }"
                            :disabled="isQuestionPanelSending(msg.pendingActionEvent)"
                            @click="setQuestionPanelTab(msg.pendingActionEvent, index)"
                          />
                        </div>
                      </div>
                      <div class="question-panel-body">
                        <div class="question-panel-question">{{ currentQuestionPanelQuestion(msg.pendingActionEvent)?.question || msg.pendingActionEvent?.question }}</div>
                        <div class="question-panel-hint">
                          {{ currentQuestionPanelQuestion(msg.pendingActionEvent)?.multiple ? '可多选，完成后统一提交' : '单选，选择后可继续下一题或直接提交' }}
                        </div>
                        <div class="question-panel-options">
                          <button
                            v-for="option in currentQuestionPanelQuestion(msg.pendingActionEvent)?.options || []"
                            :key="option.value || option.label"
                            type="button"
                            class="question-option"
                            :class="{ picked: isQuestionOptionPicked(msg.pendingActionEvent, option.value || option.label) }"
                            :disabled="isQuestionPanelSending(msg.pendingActionEvent)"
                            @click="toggleQuestionOption(msg.pendingActionEvent, option.value || option.label)"
                          >
                            <span class="question-option-mark" :class="{ multi: currentQuestionPanelQuestion(msg.pendingActionEvent)?.multiple, picked: isQuestionOptionPicked(msg.pendingActionEvent, option.value || option.label) }"></span>
                            <span class="question-option-main">
                              <span class="question-option-label">{{ option.label }}</span>
                              <span v-if="option.description" class="question-option-description">{{ option.description }}</span>
                            </span>
                          </button>
                          <button
                            v-if="currentQuestionPanelQuestion(msg.pendingActionEvent)?.custom"
                            type="button"
                            class="question-option"
                            :class="{ picked: isQuestionCustomEnabled(msg.pendingActionEvent) }"
                            :disabled="isQuestionPanelSending(msg.pendingActionEvent)"
                            @click="toggleQuestionCustom(msg.pendingActionEvent)"
                          >
                            <span class="question-option-mark" :class="{ multi: currentQuestionPanelQuestion(msg.pendingActionEvent)?.multiple, picked: isQuestionCustomEnabled(msg.pendingActionEvent) }"></span>
                            <span class="question-option-main">
                              <span class="question-option-label">自定义回答</span>
                              <span class="question-option-description">{{ currentQuestionCustomValue(msg.pendingActionEvent) || '输入你自己的回答' }}</span>
                            </span>
                          </button>
                          <el-input
                            v-if="currentQuestionPanelQuestion(msg.pendingActionEvent)?.custom && isQuestionCustomEnabled(msg.pendingActionEvent) && currentQuestionPanelQuestion(msg.pendingActionEvent)?.input_type === 'password'"
                            :model-value="currentQuestionCustomValue(msg.pendingActionEvent)"
                            type="password"
                            show-password
                            placeholder="输入自定义回答"
                            :disabled="isQuestionPanelSending(msg.pendingActionEvent)"
                            @update:model-value="updateQuestionCustomValue(msg.pendingActionEvent, $event)"
                          />
                          <el-input
                            v-else-if="currentQuestionPanelQuestion(msg.pendingActionEvent)?.custom && isQuestionCustomEnabled(msg.pendingActionEvent)"
                            :model-value="currentQuestionCustomValue(msg.pendingActionEvent)"
                            type="textarea"
                            :rows="2"
                            resize="none"
                            :placeholder="currentQuestionPanelQuestion(msg.pendingActionEvent)?.placeholder || '输入自定义回答'"
                            :disabled="isQuestionPanelSending(msg.pendingActionEvent)"
                            @update:model-value="updateQuestionCustomValue(msg.pendingActionEvent, $event)"
                          />
                        </div>
                      </div>
                      <div class="question-panel-footer">
                        <el-button size="small" :disabled="isQuestionPanelSending(msg.pendingActionEvent)" @click="cancelQuestionPanel(msg.pendingActionEvent)">取消</el-button>
                        <div class="question-panel-footer-actions">
                          <el-button
                            v-if="getQuestionPanelTab(msg.pendingActionEvent) > 0"
                            size="small"
                            :disabled="isQuestionPanelSending(msg.pendingActionEvent)"
                            @click="setQuestionPanelTab(msg.pendingActionEvent, getQuestionPanelTab(msg.pendingActionEvent) - 1)"
                          >上一题</el-button>
                          <el-button
                            v-if="getQuestionPanelTab(msg.pendingActionEvent) < getQuestionEventQuestions(msg.pendingActionEvent).length - 1"
                            size="small"
                            :disabled="isQuestionPanelSending(msg.pendingActionEvent)"
                            @click="setQuestionPanelTab(msg.pendingActionEvent, getQuestionPanelTab(msg.pendingActionEvent) + 1)"
                          >下一题</el-button>
                          <el-button
                            type="primary"
                            size="small"
                            :loading="isQuestionPanelSending(msg.pendingActionEvent)"
                            @click="submitQuestionPanel(msg.pendingActionEvent)"
                          >提交</el-button>
                        </div>
                      </div>
                    </div>
                  </div>
                  <!-- Plan 模式交互选项 -->
                  <div v-if="!isCliDisplayMessage(msg) && !msg.streaming && msg.role === 'assistant' && extractOptions(msg.content).length > 0" class="plan-options">
                    <div class="plan-options-label">请选择：</div>
                    <div class="plan-options-btns">
                      <el-button
                        v-for="(opt, oi) in extractOptions(msg.content)"
                        :key="oi"
                        size="small"
                        round
                        @click="onOptionClick(opt)"
                      >{{ opt }}</el-button>
                    </div>
                  </div>
                  <div class="message-footer">
                    <div class="message-time">{{ formatDate(msg.created_at) }}</div>
                    <div class="message-actions">
                      <el-button 
                        class="copy-btn" 
                        link 
                        size="small" 
                        @click="copyMessage(msg.content)"
                        title="复制内容"
                      >
                        <el-icon><CopyDocument /></el-icon>
                      </el-button>
                      <el-button
                        v-if="!isRakazoConversation"
                        class="undo-btn"
                        link
                        size="small"
                        @click="undoFromMessage(msg)"
                        title="撤销此消息及之后的所有消息"
                      >
                        <el-icon><Delete /></el-icon>
                      </el-button>
                    </div>
                  </div>
                </div>
              </div>
            </template>
            
            <div v-if="currentLoading" class="message assistant">
              <div class="message-avatar">
                <el-avatar :src="assistantAvatarUrl" />
              </div>
              <div class="message-content">
                <el-skeleton :rows="3" animated />
              </div>
            </div>
          </div>
          
          <!-- 记忆提示区 -->
          <div v-if="memoryHints.length > 0" class="memory-hints">
            <span class="memory-hints-label">
              <el-icon style="vertical-align: middle; margin-right: 4px;"><MagicStick /></el-icon>相关记忆
            </span>
            <div class="memory-hints-list">
              <el-tag
                v-for="(hint, idx) in memoryHints"
                :key="idx"
                size="small"
                :type="hintTagType(hint.category)"
                class="memory-hint-tag"
                :title="`[${hint.category_label}] ${hint.content}`"
              >
                <span class="hint-cat">{{ hint.category_label }}</span>{{ hint.content }}
              </el-tag>
            </div>
          </div>

          <!-- 输入区域 -->
          <div
            class="message-input"
            :class="{ 'drag-over': isDragOver }"
            @dragenter.prevent="onDragEnter"
            @dragover.prevent="onDragOver"
            @dragleave.prevent="onDragLeave"
            @drop.prevent="onDrop"
          >
            <!-- 拖拽遮罩 -->
            <div v-if="isDragOver" class="drag-overlay">
              <el-icon class="drag-icon"><Upload /></el-icon>
              <span>释放以上传文件</span>
            </div>

            <!-- 工具栏：agent模式 + 模型选择 -->
            <div v-if="!isRakazoConversation" class="input-toolbar">
              <div class="toolbar-left">
                <span class="toolbar-label">模式</span>
                <el-select
                  v-model="agentMode"
                  size="small"
                  class="agent-mode-select"
                  popper-class="agent-mode-popper"
                  :placeholder="''"
                  :disabled="isRakazoConversation"
                >
                  <el-option value="build" label="Build">
                    <span class="agent-option-name">Build</span>
                    <span class="agent-option-desc">构建模式，直接实现代码</span>
                  </el-option>
                  <el-option value="plan" label="Plan">
                    <span class="agent-option-name">Plan</span>
                    <span class="agent-option-desc">规划模式，拆解步骤后输出计划</span>
                  </el-option>
                  <el-option value="agent" label="Agent">
                    <span class="agent-option-name">Agent</span>
                    <span class="agent-option-desc">智能体模式，自我反思与专家协作</span>
                  </el-option>
                </el-select>
                <el-divider direction="vertical" />
                <span class="toolbar-label">模型</span>
                <el-select
                  v-model="selectedModel"
                  placeholder="默认模型"
                  size="small"
                  clearable
                  filterable
                  :filter-method="filterModels"
                  class="model-select"
                  :loading="modelsLoading"
                  :disabled="isRakazoConversation && currentLoading"
                  no-data-text="暂无模型（OpenCode 未连接）"
                  popper-class="model-select-popper"
                  @visible-change="onModelDropdownOpen"
                >
                  <template v-if="modelSearchQuery">
                    <el-option
                      v-for="m in filteredModels"
                      :key="m.id"
                      :label="m.name"
                      :value="m.id"
                      :disabled="m.runnable === false"
                    >
                      <span class="model-option-name">{{ m.model }}</span>
                      <span class="model-option-provider">{{ modelProviderLabel(m) }}</span>
                    </el-option>
                  </template>
                  <template v-else>
                    <el-option-group
                      v-for="group in groupedModels"
                      :key="group.provider"
                      :label="group.providerLabel"
                    >
                      <el-option
                        v-for="m in group.models"
                        :key="m.id"
                        :label="m.name"
                        :value="m.id"
                        :disabled="m.runnable === false"
                      >
                        <span class="model-option-name">{{ m.model }}</span>
                        <span class="model-option-provider">{{ modelProviderLabel(m) }}</span>
                      </el-option>
                    </el-option-group>
                  </template>
                </el-select>
                <el-button
                  size="small"
                  text
                  :loading="modelsLoading"
                  @click="loadModels({ manual: true })"
                  title="刷新模型列表"
                  class="refresh-btn"
                >
                  <el-icon><Refresh /></el-icon>
                </el-button>
                <template v-if="codexEnabled && !isRakazoConversation">
                  <el-divider direction="vertical" />
                  <span class="toolbar-label">推理</span>
                  <el-select
                    v-model="selectedReasoningEffort"
                    placeholder="Codex 默认"
                    size="small"
                    clearable
                    class="agent-mode-select"
                  >
                    <el-option
                      v-for="effort in supportedReasoningEfforts"
                      :key="effort"
                      :label="reasoningEffortLabel(effort)"
                      :value="effort"
                    />
                  </el-select>
                </template>
              </div>
            </div>

            <!-- 附件预览区 -->
            <div v-if="attachedFiles.length > 0" class="attached-files">
              <div
                v-for="(f, idx) in attachedFiles"
                :key="idx"
                class="attached-file-chip"
                :title="f.name"
              >
                <el-icon class="file-chip-icon">
                  <component :is="fileIcon(f.name)" />
                </el-icon>
                <span class="file-chip-name">{{ f.name }}</span>
                <el-icon class="file-chip-remove" @click="removeAttachedFile(idx)"><Close /></el-icon>
              </div>
            </div>

            <!-- 命令面板 (/) -->
            <div v-if="showCommandPanel" class="command-panel" ref="commandPanelRef">
              <div class="command-panel-header">
                <span>命令</span>
                <span class="command-search-hint">{{ commandQuery || '输入以过滤' }}</span>
              </div>
              <div class="command-panel-list">
                <div
                  v-for="(cmd, idx) in filteredCommands"
                  :key="cmd.name"
                  class="command-item"
                  :class="{ active: idx === commandActiveIndex }"
                  :data-command-index="idx"
                  @mousedown.prevent="selectCommand(cmd)"
                >
                  <span class="command-label">{{ cmd.label }}</span>
                  <span class="command-desc">{{ cmd.description }}</span>
                </div>
                <div v-if="filteredCommands.length === 0" class="command-empty">无匹配命令</div>
              </div>
            </div>

            <!-- @ 文件搜索面板 -->
            <div v-if="showAtPanel" class="command-panel at-panel" ref="atPanelRef">
              <div class="command-panel-header">
                <span>Skill</span>
                <span class="command-search-hint">{{ atQuery || "搜索 skill 名称或描述" }}</span>
              </div>
              <div class="command-panel-list">
                <div
                  v-for="(skill, idx) in atSkills"
                  :key="skill.id"
                  class="command-item"
                  :class="{ active: idx === atActiveIndex }"
                  :data-at-index="idx"
                  @mousedown.prevent="selectAtSkill(skill)"
                >
                  <el-icon class="command-file-icon"><MagicStick /></el-icon>
                  <span class="command-label">{{ skill.name }}</span>
                  <span class="command-desc">[{{ skill.sourceLabel || skill.source }}] {{ skill.description }}</span>
                </div>
                <div v-if="atSkills.length === 0 && !atLoading" class="command-empty">未找到 skill</div>
                <div v-if="atLoading" class="command-empty">搜索中...</div>
              </div>
            </div>

            <div v-if="showKnowledgePanel" class="command-panel knowledge-panel" ref="knowledgePanelRef">
              <div class="command-panel-header">
                <span>Obsidian 知识库</span>
                <span class="command-search-hint">{{ knowledgeQuery || "搜索知识库路径或描述" }}</span>
              </div>
              <div class="command-panel-list">
                <div
                  v-for="(kb, idx) in knowledgeResults"
                  :key="kb.id || kb.path"
                  class="command-item"
                  :class="{ active: idx === knowledgeActiveIndex }"
                  :data-knowledge-index="idx"
                  @mousedown.prevent="toggleKnowledgeBase(kb)"
                >
                  <el-icon class="command-file-icon"><Document /></el-icon>
                  <span class="command-label">{{ kb.name }}</span>
                  <span class="command-desc">{{ kb.description || kb.path }}</span>
                </div>
                <div v-if="knowledgeResults.length === 0 && !knowledgeLoading" class="command-empty">未找到知识库</div>
                <div v-if="knowledgeLoading" class="command-empty">搜索中...</div>
              </div>
            </div>

            <el-input
              ref="inputRef"
              v-model="inputMessage"
              type="textarea"
              :rows="3"
              placeholder="输入消息... (Shift+Enter 换行，/ 命令，@ skill，# 知识库)"
              @keydown="onInputKeydown"
              @paste="onInputPaste"
              @input="onInputChange"
              resize="none"
            />
            <div class="input-actions">
              <div class="input-actions-left">
                <el-tooltip :content="currentProjectDir ? ('项目: ' + currentProjectDir) : '选择项目文件夹'" placement="top">
                  <el-button @click="selectProjectFolder" :type="currentProjectDir ? 'success' : 'default'" :disabled="isRakazoConversation">
                    <el-icon><FolderOpened /></el-icon>
                    {{ currentProjectDir ? projectDirName : '项目' }}
                  </el-button>
                </el-tooltip>
                <!-- 隐藏的文件输入：拖拽、粘贴仍复用附件处理能力 -->
                <input
                  ref="fileInputRef"
                  type="file"
                  multiple
                  accept="image/*,.md,.txt,.pdf,.docx,.xlsx,.xls,.pptx,.csv,.py,.js,.ts,.json,.yaml,.yml,.html,.css,.sh,.sql,.vue,.jsx,.tsx"
                  style="display:none"
                  @change="onFileInputChange"
                />
                <el-button v-if="!isRakazoConversation" @click="showSkillDialog = true">
                  <el-icon><MagicStick /></el-icon>
                  生成技能
                </el-button>
                <el-tag v-if="isConversationExecutorLocked" size="large" effect="plain" :type="conversationExecutor === 'rakazo' ? 'primary' : conversationExecutor === 'codex' ? 'success' : 'info'">
                  {{ executorLabel(conversationExecutor) }}
                </el-tag>
                <el-button v-else :type="codexEnabled ? 'primary' : 'default'" @click="toggleCodexMode">Codex</el-button>
                <el-button :type="obsidianEnabled ? 'primary' : 'default'" @click="toggleObsidianMode">
                  Obsidian
                </el-button>
                <el-tooltip content="在 VS Code 中打开当前项目，并使用 Codebot Chat 扩展继续对话" placement="top">
                  <el-button :disabled="!currentProjectDir" @click="openCurrentProjectInVSCode">
                    VS Code
                  </el-button>
                </el-tooltip>
                <el-tag
                  v-for="kb in selectedKnowledgeBases"
                  :key="kb.id || kb.path"
                  closable
                  size="small"
                  type="success"
                  @close="removeKnowledgeBase(kb)"
                >
                  # {{ kb.name }}
                </el-tag>
                <span v-if="queuedCount > 0" class="queue-indicator">
                  <el-tag size="small" type="warning">{{ queuedCount }} 条待处理</el-tag>
                </span>
              </div>
              <div class="input-actions-right">
                <el-button
                  v-if="currentLoading"
                  type="danger"
                  plain
                  @click="abortTask"
                  :loading="aborting"
                  title="终止当前任务"
                  class="abort-btn"
                >
                  <el-icon><VideoPause /></el-icon>
                  终止
                </el-button>
                <el-button type="primary" @click="sendMessage" :loading="currentLoading && !aborting">
                  <el-icon><Promotion /></el-icon>
                  {{ currentLoading ? '处理中...' : '发送' }}
                </el-button>
              </div>
            </div>
          </div>

          <el-dialog v-model="showNewConversationDialog" title="新建对话" width="760px" :close-on-click-modal="false">
            <div class="executor-picker">
              <button
                v-for="option in executorOptions"
                :key="option.id"
                type="button"
                class="executor-card"
                :class="{ active: newConversationForm.executor === option.id }"
                @click="selectNewExecutor(option.id)"
              >
                <span class="executor-card-title">{{ option.name }}</span>
                <span class="executor-card-desc">{{ option.description }}</span>
              </button>
            </div>
            <el-form label-width="110px" class="new-conversation-form">
              <el-form-item label="项目目录" :required="newConversationForm.executor === 'rakazo'">
                <el-input v-model="newConversationForm.project_dir" readonly placeholder="选择项目后，对话将固定绑定到该目录">
                  <template #append><el-button @click="selectNewConversationProject">选择</el-button></template>
                </el-input>
              </el-form-item>
            </el-form>
            <el-alert
              v-if="newConversationForm.executor === 'rakazo'"
              :title="newConversationRakazoExists ? '该项目已有 Rakazo 主会话，将直接恢复原 Bot、Thread、Computer、当前模型与记忆。' : `每个项目只创建一个 Rakazo 主会话；模型不与对话永久绑定。初始使用 ${newConversationInitialModelLabel}，创建后可在聊天顶部空闲时切换。`"
              :type="newConversationRakazoExists ? 'success' : 'info'"
              show-icon
              :closable="false"
            />
            <template #footer>
              <el-button @click="showNewConversationDialog = false">取消</el-button>
              <el-button type="primary" :loading="creatingConversation" :disabled="!canCreateConversation" @click="createNewConversation">
                {{ newConversationForm.executor === 'rakazo' ? (newConversationRakazoExists ? '打开 Rakazo 主会话' : '创建 Rakazo 主会话') : `创建 ${executorLabel(newConversationForm.executor)} 对话` }}
              </el-button>
            </template>
          </el-dialog>

          <el-dialog
            v-model="handoffDialogVisible"
            class="handoff-dialog"
            title="显式交接执行器"
            width="820px"
            align-center
            :close-on-click-modal="false"
          >
            <el-alert
              title="原对话保持不变；只有点击“确认并交接”后，下面可编辑的摘要才会写入目标会话。"
              type="info"
              show-icon
              :closable="false"
            />
            <div class="executor-picker handoff-executor-picker">
              <button
                v-for="option in handoffTargetOptions"
                :key="option.id"
                type="button"
                class="executor-card"
                :class="{ active: handoffForm.target_executor === option.id }"
                @click="selectHandoffExecutor(option.id)"
              >
                <span class="executor-card-title">{{ option.name }}</span>
                <span class="executor-card-desc">{{ option.description }}</span>
              </button>
            </div>
            <el-form label-width="110px" class="new-conversation-form">
              <el-form-item label="项目目录" :required="handoffForm.target_executor === 'rakazo'">
                <el-input v-model="handoffForm.project_dir" readonly placeholder="目标会话使用的项目目录">
                  <template #append><el-button @click="selectHandoffProject">选择</el-button></template>
                </el-input>
              </el-form-item>
              <el-form-item v-if="handoffForm.target_executor === 'rakazo'" label="初始模型" :required="!handoffRakazoExists">
                <el-select v-model="handoffForm.route_id" filterable style="width: 100%" placeholder="选择 OpenCode 当前可用模型；首次使用自动验证" :loading="handoffModelsLoading">
                  <el-option
                    v-for="model in handoffModels"
                    :key="model.id"
                    :label="model.displayName || model.name || model.id"
                    :value="model.id"
                    :disabled="model.selectable === false"
                  />
                </el-select>
                <div class="field-tip">{{ handoffRakazoExists ? '该项目已有 Rakazo 主会话，将恢复原 Thread；不会创建第二个。' : '这里只选择首次进入时使用的模型；交接后仍可在聊天顶部切换，不会改写历史轮次。' }}</div>
              </el-form-item>
              <el-form-item label="交接摘要" required>
                <el-input
                  v-model="handoffForm.summary"
                  type="textarea"
                  :rows="12"
                  resize="vertical"
                  placeholder="正在生成只包含可见消息的脱敏摘要…"
                  :disabled="handoffPreviewLoading"
                />
                <div class="field-tip">请删除不希望带入目标执行器的内容；摘要不含隐藏系统提示、审批内部对象、凭据或完整工具输出。</div>
              </el-form-item>
            </el-form>
            <template #footer>
              <el-button @click="handoffDialogVisible = false">取消</el-button>
              <el-button :loading="handoffPreviewLoading" @click="loadHandoffPreview">重新生成摘要</el-button>
              <el-button type="primary" :loading="handoffCommitting" :disabled="!canCommitHandoff" @click="commitHandoff">确认并交接</el-button>
            </template>
          </el-dialog>

          <el-drawer v-model="rakazoDrawerVisible" title="Rakazo 状态" size="520px" @open="loadRakazoDrawer">
            <div v-loading="rakazoDrawerLoading" class="rakazo-drawer-content">
              <el-alert
                v-if="rakazoProjectState?.routeDrift"
                :title="rakazoProjectState.routeDriftReason"
                type="error"
                show-icon
                :closable="false"
              />
              <el-descriptions :column="1" border size="small">
                <el-descriptions-item label="Bot">{{ rakazoProjectState?.mapping?.bot_id || '-' }}</el-descriptions-item>
                <el-descriptions-item label="Thread">{{ rakazoProjectState?.mapping?.thread_id || '-' }}</el-descriptions-item>
                <el-descriptions-item label="Computer">{{ rakazoProjectState?.mapping?.computer_id || rakazoProjectState?.computer?.botId || '-' }}</el-descriptions-item>
                <el-descriptions-item label="模型路由">{{ rakazoProjectState?.mapping?.model_route_id || '-' }}</el-descriptions-item>
                <el-descriptions-item label="路由指纹"><code>{{ rakazoProjectState?.mapping?.model_route_fingerprint || '-' }}</code></el-descriptions-item>
                <el-descriptions-item label="运行时版本">{{ rakazoProjectState?.mapping?.runtime_version || '-' }}</el-descriptions-item>
              </el-descriptions>

              <el-divider content-position="left">Computer</el-divider>
              <pre class="drawer-json">{{ JSON.stringify(rakazoProjectState?.computer || {}, null, 2) }}</pre>
              <div class="drawer-actions">
                <el-button size="small" @click="rakazoComputerAction('boot')">启动</el-button>
                <el-button size="small" @click="rakazoComputerAction('restart')">重启</el-button>
                <el-button size="small" type="danger" plain @click="rakazoComputerAction('stop')">停止</el-button>
              </div>

              <el-divider content-position="left">Memory</el-divider>
              <pre class="drawer-json">{{ JSON.stringify(rakazoMemory || {}, null, 2) }}</pre>

              <el-divider content-position="left">项目权限</el-divider>
              <el-form v-if="rakazoPermissions" label-width="120px">
                <el-form-item label="项目读取"><el-switch v-model="rakazoPermissions.projectRead" :disabled="rakazoPermissionSaving" @change="(value) => saveRakazoPermissions('projectRead', value)" /></el-form-item>
                <el-form-item label="项目写入"><el-switch v-model="rakazoPermissions.projectWrite" :disabled="rakazoPermissionSaving" @change="(value) => saveRakazoPermissions('projectWrite', value)" /></el-form-item>
                <el-form-item label="命令执行">
                  <el-switch
                    v-model="rakazoPermissions.commandExecution"
                    :disabled="rakazoPermissionSaving || rakazoPermissions.commandSupported === false"
                    @change="(value) => saveRakazoPermissions('commandExecution', value)"
                  />
                  <span class="permission-note">{{ rakazoPermissions.commandExecution ? '容器内命令已授权' : '关闭时每次 shell 调用需审批' }}</span>
                </el-form-item>
                <el-form-item label="外部网络">
                  <el-switch
                    v-model="rakazoPermissions.externalNetwork"
                    :disabled="rakazoPermissionSaving || rakazoPermissions.externalNetworkSupported === false"
                    @change="(value) => saveRakazoPermissions('externalNetwork', value)"
                  />
                  <span class="permission-note">{{ rakazoPermissions.externalNetwork ? '项目私有网络可访问外网' : 'Docker internal 网络已阻断外网' }}</span>
                </el-form-item>
                <div class="permission-note">{{ rakazoPermissions.permissionNote }}</div>
              </el-form>

              <el-divider content-position="left">最近审计日志</el-divider>
              <el-timeline>
                <el-timeline-item v-for="item in rakazoLogs" :key="item.id" :timestamp="formatDate(item.created_at)" :type="item.status === 'success' ? 'success' : 'danger'">
                  <strong>{{ item.action }}</strong>
                  <pre class="drawer-log-detail">{{ JSON.stringify(item.detail || {}, null, 2) }}</pre>
                </el-timeline-item>
              </el-timeline>
            </div>
          </el-drawer>

          <!-- 生成技能对话框（优先 find-skills，再按需走 skill-creator） -->
          <el-dialog v-model="showSkillDialog" title="生成技能" width="520px">
            <p style="margin: 0 0 12px; color: #606266; font-size: 14px;">
              描述你需要的技能功能。Codebot 会先调用 `find-skills` 搜索并评估现有 skill，
              贴合度足够时直接下载并改造；否则改走 `skill-creator` 创建新 skill，最终统一保存到“自动生成”目录。
            </p>
            <el-input
              v-model="skillGenDescription"
              type="textarea"
              :rows="5"
              placeholder="例如：一个可以读取 Excel 文件并提取指定列数据的技能，支持多种格式转换..."
              :disabled="generatingSkill"
            />
            <template #footer>
              <el-button @click="showSkillDialog = false" :disabled="generatingSkill">取消</el-button>
              <el-button type="primary" @click="generateSkill" :loading="generatingSkill">
                {{ generatingSkill ? 'AI 生成中...' : '生成技能' }}
              </el-button>
            </template>
          </el-dialog>
        </div>
        
        <el-empty
          v-else
          description="请选择或创建一个对话"
          :image-size="200"
        />
      </el-main>
    </el-container>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted, nextTick, computed, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Grid, Delete, Refresh, VideoPlay, VideoPause, Upload, Close, Document, Paperclip, Picture, Search, FolderOpened, MagicStick, Promotion } from '@element-plus/icons-vue'
import axios from 'axios'
import MarkdownIt from 'markdown-it'
import hljs from 'highlight.js'
import 'highlight.js/styles/github.css'

const assistantAvatarUrl = '/logo.png'

const md = new MarkdownIt({
  html: false,
  linkify: true,
  typographer: true,
  highlight: function (str, lang) {
    if (lang && hljs.getLanguage(lang)) {
      try {
        return '<pre class="hljs"><code>' +
               hljs.highlight(str, { language: lang, ignoreIllegals: true }).value +
               '</code></pre>';
      } catch (__) {}
    }
    return '<pre class="hljs"><code>' + md.utils.escapeHtml(str) + '</code></pre>';
  }
})

// 自定义链接渲染：添加 target="_blank" 和 rel 属性，以及外部链接标记
const defaultLinkRender = md.renderer.rules.link_open || function(tokens, idx, options, env, self) {
  return self.renderToken(tokens, idx, options)
}
md.renderer.rules.link_open = function(tokens, idx, options, env, self) {
  tokens[idx].attrSet('target', '_blank')
  tokens[idx].attrSet('rel', 'noopener noreferrer')
  tokens[idx].attrSet('class', 'external-link')
  return defaultLinkRender(tokens, idx, options, env, self)
}

const renderMarkdown = (content) => {
  if (!content) return ''
  // 渲染前移除 options 注释块，避免在 HTML 中残留
  const cleaned = (content || '').replace(/<!--\s*options\s*\n[\s\S]*?-->/g, '')
  return md.render(cleaned)
}

/**
 * 从消息内容中提取 <!-- options\n- 选项A\n- 选项B\n--> 中的选项列表
 */
const extractOptions = (content) => {
  if (!content) return []
  const match = content.match(/<!--\s*options\s*\n([\s\S]*?)-->/)
  if (!match) return []
  const lines = match[1].split('\n').map(l => l.trim()).filter(l => l.startsWith('- '))
  return lines.map(l => l.replace(/^-\s*/, '').trim()).filter(Boolean)
}

/**
 * 点击选项按钮：将选项内容填入输入框并发送
 */
const onOptionClick = (optionText) => {
  inputMessage.value = optionText
  nextTick(() => sendMessage())
}

// 前端流式内容清洗：镜像后端 _sanitize_assistant_output 的核心逻辑
// 在流式传输过程中实时剥除 think/reasoning 标签及系统提示词泄露内容
const sanitizeStreamContent = (text, userMessage = '') => {
  if (!text) return ''

  // 移除回复开头对用户问题的回显（如"你好，你能干吗？ 你好！..."）
  if (userMessage) {
    const um = userMessage.trim()
    const stripped = text.trimStart()
    if (stripped.toLowerCase().startsWith(um.toLowerCase())) {
      const after = stripped.slice(um.length).replace(/^[\s，。！？,.!? ]+/, '')
      text = after
    }
  }

  // 移除完整的思考/推理标签块
  text = text.replace(/<think>[\s\S]*?<\/think>/gi, '')
  text = text.replace(/<thinking>[\s\S]*?<\/thinking>/gi, '')
  text = text.replace(/<reasoning>[\s\S]*?<\/reasoning>/gi, '')
  text = text.replace(/<reflection>[\s\S]*?<\/reflection>/gi, '')

  // 移除未闭合的思考标签（流式中标签还未结束时）
  text = text.replace(/<think>[\s\S]*$/gi, '')
  text = text.replace(/<thinking>[\s\S]*$/gi, '')
  text = text.replace(/<reasoning>[\s\S]*$/gi, '')
  text = text.replace(/<reflection>[\s\S]*$/gi, '')

  // 移除残留的单个标签
  text = text.replace(/<\/?(think|thinking|reasoning|reflection)>/gi, '')

  // 移除 codebot 注入的 XML 包装标签块
  text = text.replace(/<(system_policy|conversation_context)>[\s\S]*?<\/\1>/gi, '')
  text = text.replace(/<\/?(system_policy|conversation_context)>/gi, '')
  text = text.replace(/<internal_context>[\s\S]*?<\/internal_context>/gi, '')
  text = text.replace(/<user_message>[\s\S]*?<\/user_message>/gi, '')

  // 移除内部 prompt 标记行
  text = text.replace(/^__RUN_ONCE__.*$/gm, '')
  text = text.replace(/^__REMINDER__.*$/gm, '')

  // 移除【用户输入/消息/请求】标记行
  text = text.replace(/^【用户(?:输入|消息|请求)】.*$/gm, '')

  const _SYSTEM_LEAK_MARKERS = [
    '你正在 OpenCode 中处理用户消息',
    '请自主决策并持续执行',
    '当前聊天由 OpenCode 统一处理',
    '除非用户明确询问架构细节',
    '请直接输出给用户的最终结果',
    '直接输出给用户的最终回复',
    '不要输出你的思考过程、推理步骤',
    '不要复述系统指令或提示词内容',
    '当前是规划模式',
    '以下是与当前问题相关的用户记忆',
    '请只输出给用户的最终结果',
    '你是意图识别',
    '你是 Cron 表达式生成器',
    '你是Cron表达式生成器',
    '任务：从用户输入中提取结构化指令',
    '【用户消息】',
    '【用户事实记忆',
    '【用户个人信息】',
    '【用户偏好】',
    '【用户习惯】',
    '【用户长期记忆',
    '请直接输出给用户的最终结果，不要输出推理过程',
    'you must answer concisely',
    'You MUST answer concisely',
    'fewer than 4 lines of text',
    'not including tool use or code generation',
    'unless user asks for detail',
    'do not use emoji',
    '根据我的指示',
    '根据我的指令',
    '根据指示，我应该',
    '根据指令，我应该',
    '根据系统提示',
    '根据系统指令',
    '根据提示词',
    '根据我的风格指导',
    '根据我的风格',
    '根据风格指导',
    '根据我的设定',
    '用户指定的文件存储目录为',
  ]

  const _REASONING_MARKERS = [
    '我应该', '我需要简洁', '我需要直接', '我需要礼貌', '我需要简短',
    '我需要友好', '我需要回应', '我需要回复',
    '我需要注意', '我需要遵循', '我需要记住',
    '我可以简单地', '我可以直接',
    '我不需要使用任何工具', '我不需要加载任何技能',
    '不需要长篇大论', '不要过于啰嗦', '不需要解释',
    '用户只是简单地说', '用户只是在问候', '这是一个简单的问候',
    '这是一个问候', '让我先自我反思',
    '所以我的回答应该', '我的回答应该',
    '不要解释内部架构', '不要输思考过程', '直接给出最终回复',
    '不要输出推理', '直接给出最终', '直接输出最终',
    '简洁、直接', '避免不必要的前言或后言', '保持简短',
    '一个词的答案最好', '少于4行', 'fewer than 4 lines',
    '根据我的风格指导', '按照我的风格指导',
  ]

  const hasSystemLeak = (t) => {
    const tl = t.toLowerCase()
    return _SYSTEM_LEAK_MARKERS.some((m) => tl.includes(m.toLowerCase()))
  }

  if (hasSystemLeak(text)) {
    const isCleanText = (t) => {
      if (!t.trim()) return false
      if (hasSystemLeak(t)) return false
      const tl = t.toLowerCase()
      return !_REASONING_MARKERS.some((m) => tl.includes(m))
    }
    const paragraphs = text.split(/\n\s*\n/)
    const cleanParas = paragraphs.filter((p) => isCleanText(p))
    if (cleanParas.length > 0) {
      text = cleanParas.join('\n\n')
    } else {
      const sentences = text.split(/(?<=[。！？!?])/)
      const cleanSents = sentences.filter((s) => isCleanText(s))
      text = cleanSents.length > 0 ? cleanSents.join('').trim() : ''
    }
  }

  return text
}

const copyToClipboard = async (text) => {
  if (window?.electronAPI?.copyText) {
    const success = await window.electronAPI.copyText(text)
    if (success) return
  }
  if (navigator.clipboard && window.isSecureContext) {
    await navigator.clipboard.writeText(text)
    return
  }
  const textArea = document.createElement('textarea')
  textArea.value = text
  textArea.setAttribute('readonly', '')
  textArea.style.position = 'fixed'
  textArea.style.top = '-9999px'
  textArea.style.left = '-9999px'
  document.body.appendChild(textArea)
  textArea.select()
  textArea.setSelectionRange(0, textArea.value.length)
  const success = document.execCommand('copy')
  document.body.removeChild(textArea)
  if (!success) throw new Error('copy_failed')
}

const copyMessage = async (content) => {
  try {
    await copyToClipboard(content)
    ElMessage.success('复制成功')
  } catch (err) {
    ElMessage.error('复制失败')
  }
}

const LAST_CONVERSATION_KEY = 'codebot:lastConversationId'
const CONVERSATION_TARGETS_KEY = 'codebot:conversationTargets'

const conversations = ref([])
const messages = ref([])
const currentConversationId = ref(null)
const currentConversation = ref(null)
const inputMessage = ref('')
const questionPanelState = ref({})
const loadingCounts = ref({})
const messageListRef = ref(null)
const inputRef = ref(null)
const fileInputRef = ref(null)
const conversationTitleRefreshTimers = new Map()

const parseRuntimeMetadata = (conversation) => {
  const raw = conversation?.runtime_metadata
  if (raw && typeof raw === 'object') return raw
  if (typeof raw === 'string' && raw.trim()) {
    try {
      const parsed = JSON.parse(raw)
      return parsed && typeof parsed === 'object' ? parsed : {}
    } catch {}
  }
  return {}
}
const conversationRuntimeMetadata = computed(() => parseRuntimeMetadata(currentConversation.value))
const conversationExecutor = computed(() => String(currentConversation.value?.executor || 'opencode').toLowerCase())
const isRakazoConversation = computed(() => conversationExecutor.value === 'rakazo')
const isConversationExecutorLocked = computed(() => Boolean(conversationRuntimeMetadata.value.executorLocked) || isRakazoConversation.value)
const rakazoProjectId = computed(() => String(conversationRuntimeMetadata.value.projectId || ''))
const executorLabel = (executor) => ({ opencode: 'OpenCode', codex: 'Codex', rakazo: 'Rakazo' }[executor] || executor || 'OpenCode')

const showNewConversationDialog = ref(false)
const creatingConversation = ref(false)
const newConversationRakazoExists = ref(false)
const newConversationForm = ref({ executor: 'opencode', project_dir: '', route_id: '' })
const executorOptions = [
  { id: 'opencode', name: 'OpenCode', description: '默认主链，使用 OpenCode Agent 与完整工具生态。' },
  { id: 'codex', name: 'Codex', description: '官方 Codex Agent Harness，执行器在创建后固定。' },
  { id: 'rakazo', name: 'Rakazo', description: '恢复项目唯一 Bot、Thread、Computer 与长期记忆。' },
]
const handoffDialogVisible = ref(false)
const handoffPreviewLoading = ref(false)
const handoffCommitting = ref(false)
const handoffModelsLoading = ref(false)
const handoffModels = ref([])
const handoffRakazoExists = ref(false)
const handoffForm = ref({
  source_conversation_id: null,
  source_title: '',
  source_executor: 'opencode',
  target_executor: 'codex',
  project_dir: '',
  route_id: '',
  summary: '',
})
const handoffTargetOptions = computed(() => executorOptions.filter((item) => item.id !== handoffForm.value.source_executor))
const pickInitialRakazoModel = (items, preferredId = '') => {
  const selectable = (Array.isArray(items) ? items : []).filter((item) => item?.selectable !== false && item?.runnable !== false)
  const preferred = normalizeModelId(preferredId)
  return selectable.find((item) => normalizeModelId(item.id) === preferred)
    || selectable.find((item) => item.compatibilityStatus === 'verified')
    || selectable[0]
    || null
}
const newConversationInitialModel = computed(() => {
  if (newConversationRakazoExists.value) {
    return availableModels.value.find((item) => item.id === newConversationForm.value.route_id) || null
  }
  return pickInitialRakazoModel(availableModels.value, newConversationForm.value.route_id || selectedModel.value)
})
const newConversationInitialModelLabel = computed(() => {
  const model = newConversationInitialModel.value
  return model?.model || model?.name || model?.id || 'OpenCode 当前已验证模型'
})
const canCreateConversation = computed(() => {
  if (newConversationForm.value.executor !== 'rakazo') return true
  if (!newConversationForm.value.project_dir) return false
  if (newConversationRakazoExists.value) return true
  return Boolean(newConversationInitialModel.value)
})
const canCommitHandoff = computed(() => {
  if (!handoffForm.value.source_conversation_id || !handoffForm.value.summary.trim()) return false
  if (handoffForm.value.target_executor === handoffForm.value.source_executor) return false
  if (handoffForm.value.target_executor !== 'rakazo') return true
  if (!handoffForm.value.project_dir) return false
  return handoffRakazoExists.value || Boolean(handoffForm.value.route_id)
})

const rakazoDrawerVisible = ref(false)
const rakazoDrawerLoading = ref(false)
const rakazoModelSwitching = ref(false)
const rakazoProjectState = ref(null)
const rakazoMemory = ref(null)
const rakazoLogs = ref([])
const rakazoPermissions = ref(null)
const rakazoPermissionSaving = ref(false)
const rakazoComputerStateLabel = computed(() => {
  const state = String(rakazoProjectState.value?.computer?.state || '').toLowerCase()
  return ({
    running: 'Computer 运行中',
    suspended: 'Computer 已挂起',
    stopped: 'Computer 已停止',
    starting: 'Computer 启动中',
    busy: 'Computer 忙碌',
  })[state] || 'Computer 状态未知'
})

const isPlaceholderConversationTitle = (title) => {
  const normalized = String(title || '').trim()
  return !normalized || normalized === '新对话'
}

const stopConversationTitleRefresh = (conversationId) => {
  const timer = conversationTitleRefreshTimers.get(conversationId)
  if (!timer) return
  clearTimeout(timer)
  conversationTitleRefreshTimers.delete(conversationId)
}

const patchConversation = (conversationId, patch) => {
  const idx = conversations.value.findIndex((item) => item.id === conversationId)
  if (idx === -1) return
  const updated = [...conversations.value]
  updated[idx] = { ...updated[idx], ...patch }
  conversations.value = updated
}

const isMultiAgentHub = (conv) => conv?.conversation_type === 'multi_agent_hub'
const multiAgentEntryExpanded = ref(false)
const multiAgentHubConversation = computed(() => conversations.value.find(isMultiAgentHub) || null)

const refreshConversationTitleOnce = async (conversationId) => {
  const response = await axios.get(`/api/chat/conversations/${conversationId}`)
  const conversation = response.data?.data
  if (!conversation) return false
  patchConversation(conversationId, conversation)
  return !isPlaceholderConversationTitle(conversation.title)
}

const scheduleConversationTitleRefresh = (conversationId, attempts = 20, delay = 1500) => {
  if (!conversationId || attempts <= 0) return
  const currentConversation = conversations.value.find((item) => item.id === conversationId)
  if (currentConversation && !isPlaceholderConversationTitle(currentConversation.title)) {
    stopConversationTitleRefresh(conversationId)
    return
  }
  if (conversationTitleRefreshTimers.has(conversationId)) return

  const poll = async (remaining) => {
    try {
      const resolved = await refreshConversationTitleOnce(conversationId)
      if (resolved) {
        stopConversationTitleRefresh(conversationId)
        return
      }
    } catch {}

    if (remaining <= 1) {
      stopConversationTitleRefresh(conversationId)
      return
    }

    const timer = window.setTimeout(() => {
      conversationTitleRefreshTimers.delete(conversationId)
      poll(remaining - 1)
    }, delay)
    conversationTitleRefreshTimers.set(conversationId, timer)
  }

  const timer = window.setTimeout(() => {
    conversationTitleRefreshTimers.delete(conversationId)
    poll(attempts)
  }, 800)
  conversationTitleRefreshTimers.set(conversationId, timer)
}

// 对话搜索
const conversationSearchQuery = ref('')
const filteredConversations = computed(() => {
  const q = conversationSearchQuery.value.trim()
  const normalConversations = conversations.value.filter(conv => !isMultiAgentHub(conv))
  if (!q) return normalConversations
  // 按空格分词（支持中英文），所有词必须同时匹配标题（AND 逻辑）
  const tokens = q.split(/[\s\u3000]+/).filter(Boolean).map(t => t.toLowerCase())
  if (tokens.length === 0) return normalConversations
  return normalConversations.filter(conv => {
    const title = (conv.title || '').toLowerCase()
    return tokens.every(tok => title.includes(tok))
  })
})

// 生成技能
const showSkillDialog = ref(false)
const generatingSkill = ref(false)
const skillGenDescription = ref('')

// Agent 模式：'build' = 默认, 'plan', 'build'
const AGENT_MODE_KEY = 'codebot:agentMode'
const agentMode = ref(localStorage.getItem(AGENT_MODE_KEY) || 'build')
let applyingConversationUiState = false
watch(agentMode, (val) => {
  if (!applyingConversationUiState) {
    localStorage.setItem(AGENT_MODE_KEY, val || 'build')
    saveCurrentConversationTargetState()
  }
})

// 项目文件夹（每个对话独立）
const currentProjectDir = ref('')
const projectDirName = computed(() => {
  if (!currentProjectDir.value) return ''
  const parts = currentProjectDir.value.replace(/[\\/]+$/, '').split(/[\\/]/)
  return parts[parts.length - 1] || ''
})
watch(currentProjectDir, async (val, oldVal) => {
  // 当项目目录变化时，同步到后端对话记录
  if (currentConversationId.value && val !== oldVal && !isRakazoConversation.value) {
    try {
      await axios.patch(`/api/chat/conversations/${currentConversationId.value}/project_dir`, {
        project_dir: val || null
      })
    } catch (e) {
      console.warn('同步项目目录到后端失败', e)
    }
  }
})

async function selectProjectFolder() {
  // 如果已选择项目，点击时提供取消选项
  if (currentProjectDir.value) {
    try {
      await ElMessageBox.confirm(
        `当前项目: ${currentProjectDir.value}`,
        '项目文件夹',
        { confirmButtonText: '重新选择', cancelButtonText: '取消选择', distinguishCancelAndClose: true }
      )
      // 用户点了"重新选择"
    } catch (action) {
      if (action === 'cancel') {
        currentProjectDir.value = ''
        ElMessage.info('已取消项目选择')
      }
      return
    }
  }
  // 调用 Electron 文件夹选择对话框
  if (window.electronAPI && window.electronAPI.selectFolder) {
    const dir = await window.electronAPI.selectFolder({ title: '选择项目文件夹' })
    if (dir) {
      currentProjectDir.value = dir
      ElMessage.success(`项目已设置: ${projectDirName.value}`)
    }
  } else {
    // 非 Electron 环境（浏览器）- 使用输入框手动输入
    ElMessageBox.prompt('请输入项目文件夹完整路径', '选择项目', {
      inputValue: currentProjectDir.value,
      confirmButtonText: '确定',
      cancelButtonText: '取消'
    }).then(({ value }) => {
      if (value && value.trim()) {
        currentProjectDir.value = value.trim()
        ElMessage.success(`项目已设置: ${projectDirName.value}`)
      }
    }).catch(() => {})
  }
}

// 终止任务
const aborting = ref(false)
const queuedCount = ref(0)
const serverRunningStatus = ref({})
const queueStatusSyncing = ref({})
const runtimeSeqByConversation = ref({})
const runtimeEventSeqSeen = ref({})
const runtimeAssistantIdByConversation = ref({})
const activeStreamByConversation = ref({})
const runtimeReloadDoneByConversation = ref({})
const actionRequiredEventSeen = ref({})
const opencodeCliDisplay = ref(true)
const compactMode = ref(false)
// 每个对话的 event 气泡缓存，用于切换对话后恢复推理过程展示
const perConversationEventMessages = ref({})
let queueStatusTimer = null
const shouldAutoScroll = ref(true)
const nowTick = ref(Date.now())
let timeRefreshTimer = null
let thirdPartyStatusTimer = null
const thirdPartyStatus = ref(null)
const thirdPartyStatusRefreshing = ref(false)
const multiAgentMembers = ref([])

const loadMultiAgentMembers = async () => {
  try {
    const params = {}
    if (currentProjectDir.value) params.project_dir = currentProjectDir.value
    const res = await axios.get('/api/chat/multi-agent/members', { params })
    multiAgentMembers.value = res.data?.data?.items || []
  } catch {
    multiAgentMembers.value = []
  }
}

// ── 附件管理 ─────────────────────────────────────────────────────────────────
const attachedFiles = ref([])  // [{name, type, content, is_text}]
const uploadingFile = ref(false)
const isDragOver = ref(false)
let _dragCounter = 0

const fileIcon = (name) => {
  const ext = (name || '').split('.').pop().toLowerCase()
  if (['jpg', 'jpeg', 'png', 'gif', 'webp', 'svg', 'bmp'].includes(ext)) return Picture
  return Document
}

const removeAttachedFile = (idx) => {
  attachedFiles.value.splice(idx, 1)
}

const processFiles = async (files) => {
  if (!files || files.length === 0) return
  uploadingFile.value = true
  const errors = []
  for (const file of Array.from(files)) {
    try {
      const formData = new FormData()
      formData.append('file', file)
      const res = await axios.post('/api/chat/upload_file', formData, {
        headers: { 'Content-Type': 'multipart/form-data' }
      })
      if (res.data?.success) {
        attachedFiles.value.push(res.data.data)
        ElMessage.success(`已附加：${file.name}`)
      }
    } catch (e) {
      errors.push(file.name)
    }
  }
  if (errors.length > 0) {
    ElMessage.error(`上传失败：${errors.join(', ')}`)
  }
  uploadingFile.value = false
}

const onInputPaste = async (e) => {
  const items = Array.from(e.clipboardData?.items || [])
  const imageFiles = items
    .filter((item) => item.kind === 'file' && item.type.startsWith('image/'))
    .map((item, idx) => {
      const file = item.getAsFile()
      if (!file) return null
      if (file.name) return file
      const ext = (file.type.split('/')[1] || 'png').replace('jpeg', 'jpg')
      return new File([file], `screenshot-${Date.now()}-${idx + 1}.${ext}`, { type: file.type })
    })
    .filter(Boolean)

  if (imageFiles.length === 0) return
  e.preventDefault()
  await processFiles(imageFiles)
}

const triggerFileUpload = () => {
  fileInputRef.value?.click()
}

const onFileInputChange = async (e) => {
  await processFiles(e.target.files)
  // 重置 input 以允许重复选同一文件
  e.target.value = ''
}

const onDragEnter = (e) => {
  _dragCounter++
  if (e.dataTransfer?.types?.includes('Files')) {
    isDragOver.value = true
  }
}

const onDragOver = (e) => {
  if (e.dataTransfer?.types?.includes('Files')) {
    e.dataTransfer.dropEffect = 'copy'
    isDragOver.value = true
  }
}

const onDragLeave = () => {
  _dragCounter--
  if (_dragCounter <= 0) {
    _dragCounter = 0
    isDragOver.value = false
  }
}

const onDrop = async (e) => {
  _dragCounter = 0
  isDragOver.value = false
  const files = e.dataTransfer?.files
  if (files && files.length > 0) {
    await processFiles(files)
  }
}

// ── / 命令面板 ────────────────────────────────────────────────────────────────
const showCommandPanel = ref(false)
const commandQuery = ref('')
const commandActiveIndex = ref(0)
const commandPanelRef = ref(null)
const allCommands = ref([])
const allSkillCommands = ref([])

const splitSearchTokens = (value) =>
  String(value || '').trim().toLowerCase().split(/[\s\u3000]+/).filter(Boolean)

const matchSearchTokens = (query, ...values) => {
  const tokens = splitSearchTokens(query)
  if (tokens.length === 0) return true
  const haystack = values.map((item) => String(item || '')).join(' ').toLowerCase()
  return tokens.every((token) => haystack.includes(token))
}

const filteredCommands = computed(() => {
  const q = commandQuery.value
  const base = allCommands.value.filter(c =>
    matchSearchTokens(q, c.label, c.description, c.name)
  )
  const skills = allSkillCommands.value.filter(c =>
    matchSearchTokens(q, c.label, c.description, c.skill_name, c.skill_id)
  )
  return [...base, ...skills].slice(0, 10)
})

const loadCommands = async () => {
  try {
    const res = await axios.get('/api/chat/commands')
    if (res.data?.success) {
      allCommands.value = res.data.data.commands || []
      allSkillCommands.value = res.data.data.skills || []
    }
  } catch (e) {
    // 静默失败
  }
}

const selectCommand = (cmd) => {
  showCommandPanel.value = false
  commandQuery.value = ''

  // 删除已输入的 /xxx 前缀
  const slashIdx = inputMessage.value.lastIndexOf('/')
  if (slashIdx !== -1) {
    inputMessage.value = inputMessage.value.substring(0, slashIdx)
  }

  if (cmd.type === 'action') {
    if (cmd.name === 'plan') { agentMode.value = 'plan'; ElMessage.success('已切换到 Plan 模式') }
    else if (cmd.name === 'build') { agentMode.value = 'build'; ElMessage.success('已切换到 Build 模式') }
    else if (cmd.name === 'agent') { agentMode.value = 'agent'; ElMessage.success('已切换到 Agent 模式') }
    else if (cmd.name === 'clear') { inputMessage.value = '' }
    else if (cmd.name === 'memory') {
      inputMessage.value += '显示与当前话题相关的记忆'
    }
  } else if (cmd.type === 'skill' || cmd.type === 'category') {
    const label = cmd.skill_name || cmd.label
    inputMessage.value += `使用技能[${cmd.skill_id}] ${label} `
  }
  nextTick(() => inputRef.value?.focus())
}

// ── @ skill 面板 ─────────────────────────────────────────────────────────────
const showAtPanel = ref(false)
const atQuery = ref('')
const atActiveIndex = ref(0)
const atSkills = ref([])
const atLoading = ref(false)
const atPanelRef = ref(null)
let _atSearchTimer = null

const searchAtSkills = async (query) => {
  atLoading.value = true
  try {
    const params = { query, limit: 80 }
    const res = await axios.get('/api/chat/skills/search', { params })
    atSkills.value = res.data?.data?.skills || []
  } catch (e) {
    atSkills.value = []
  } finally {
    atLoading.value = false
    scrollActivePanelItem(atPanelRef, 'data-at-index', atActiveIndex.value)
  }
}

const selectAtSkill = async (skill) => {
  showAtPanel.value = false
  atQuery.value = ''

  const atIdx = inputMessage.value.lastIndexOf('@')
  if (atIdx !== -1) {
    inputMessage.value = inputMessage.value.substring(0, atIdx)
  }

  inputMessage.value += `使用技能 @[${skill.id}] ${skill.name} `
  ElMessage.success(`已选择 skill：${skill.name}`)
  nextTick(() => inputRef.value?.focus())
}

// ── # Obsidian 知识库面板 ────────────────────────────────────────────────────
const showKnowledgePanel = ref(false)
const knowledgeQuery = ref('')
const knowledgeActiveIndex = ref(0)
const knowledgeResults = ref([])
const knowledgeLoading = ref(false)
const knowledgePanelRef = ref(null)
const selectedKnowledgeBases = ref([])
let _knowledgeSearchTimer = null

const loadConversationTargetMap = () => {
  try {
    const map = JSON.parse(localStorage.getItem(CONVERSATION_TARGETS_KEY) || '{}') || {}
    let changed = false
    for (const state of Object.values(map)) {
      if (!state || typeof state !== 'object') continue
      const legacyTarget = String(state.target || '').toLowerCase()
      const migratedTarget = {
        hermes: 'codex',
        hermes_cli: 'codex',
        hermes_agent: 'codex',
        hermes_obsidian: 'codex_obsidian',
      }[legacyTarget]
      if (migratedTarget) {
        state.target = migratedTarget
        changed = true
      }
      if (Object.prototype.hasOwnProperty.call(state, 'hermes_enabled')) {
        if (!Object.prototype.hasOwnProperty.call(state, 'codex_enabled')) {
          state.codex_enabled = Boolean(state.hermes_enabled)
        }
        delete state.hermes_enabled
        changed = true
      }
    }
    if (changed) saveConversationTargetMap(map)
    return map
  } catch {
    return {}
  }
}

const saveConversationTargetMap = (map) => {
  localStorage.setItem(CONVERSATION_TARGETS_KEY, JSON.stringify(map || {}))
}

const codexEnabled = ref(false)
const obsidianEnabled = ref(false)

const chatTarget = computed(() => {
  if (isRakazoConversation.value) return 'rakazo'
  if (isConversationExecutorLocked.value && conversationExecutor.value === 'codex') {
    return obsidianEnabled.value ? 'codex_obsidian' : 'codex'
  }
  if (isConversationExecutorLocked.value && conversationExecutor.value === 'opencode') {
    return obsidianEnabled.value ? 'obsidian' : 'codebot'
  }
  if (codexEnabled.value && obsidianEnabled.value) return 'codex_obsidian'
  if (codexEnabled.value) return 'codex'
  if (obsidianEnabled.value) return 'obsidian'
  return 'codebot'
})

const isCodexChatTarget = (target) => {
  const value = String(target || '').trim().toLowerCase()
  return value === 'codex' || value.startsWith('codex_')
}

const applyConversationTargetState = (conversationId) => {
  const state = loadConversationTargetMap()[String(conversationId)] || {}
  const target = state.target || 'codebot'
  const persistedExecutor = conversationExecutor.value
  const locked = isConversationExecutorLocked.value
  applyingConversationUiState = true
  try {
    codexEnabled.value = locked ? persistedExecutor === 'codex' : Boolean(state.codex_enabled ?? isCodexChatTarget(target))
    obsidianEnabled.value = persistedExecutor === 'rakazo'
      ? false
      : Boolean(state.obsidian_enabled ?? (target === 'obsidian' || String(target || '').includes('obsidian') || (state.knowledge_bases || []).length > 0))
    selectedKnowledgeBases.value = Array.isArray(state.knowledge_bases) ? state.knowledge_bases : []
    agentMode.value = state.mode || localStorage.getItem(AGENT_MODE_KEY) || 'build'
    const model = persistedExecutor === 'rakazo'
      ? ''
      : normalizeModelId(state.model || localStorage.getItem(LAST_MODEL_KEY) || selectedModel.value || '')
    selectedModel.value = model
    selectedReasoningEffort.value = state.reasoning_effort || ''
    ensureSelectedModelOption(model)
  } finally {
    nextTick(async () => {
      applyingConversationUiState = false
      if (persistedExecutor === 'rakazo') {
        await loadRakazoProjectState()
        await loadModels({ rakazo: true })
        const routeId = rakazoProjectState.value?.mapping?.model_route_id || ''
        applyingConversationUiState = true
        selectedModel.value = routeId
        nextTick(() => { applyingConversationUiState = false })
      } else {
        loadModels()
      }
    })
  }
}

const saveCurrentConversationTargetState = () => {
  if (!currentConversationId.value) return
  const map = loadConversationTargetMap()
  map[String(currentConversationId.value)] = {
    target: chatTarget.value || 'codebot',
    codex_enabled: codexEnabled.value,
    obsidian_enabled: obsidianEnabled.value,
    knowledge_bases: selectedKnowledgeBases.value || [],
    mode: agentMode.value || 'build',
    model: selectedModel.value || '',
    reasoning_effort: selectedReasoningEffort.value || '',
  }
  saveConversationTargetMap(map)
}

const resetCurrentConversationTargetState = () => {
  applyingConversationUiState = true
  try {
    codexEnabled.value = false
    obsidianEnabled.value = false
    selectedKnowledgeBases.value = []
    agentMode.value = localStorage.getItem(AGENT_MODE_KEY) || 'build'
    selectedModel.value = normalizeModelId(localStorage.getItem(LAST_MODEL_KEY) || selectedModel.value || '')
    selectedReasoningEffort.value = ''
    ensureSelectedModelOption(selectedModel.value)
  } finally {
    nextTick(() => {
      applyingConversationUiState = false
      saveCurrentConversationTargetState()
    })
  }
}

const toggleCodexMode = async () => {
  if (isConversationExecutorLocked.value) {
    ElMessage.warning('该对话的执行器已固定，请从“新建对话”选择其他执行器')
    return
  }
  codexEnabled.value = !codexEnabled.value
  selectedReasoningEffort.value = ''
  saveCurrentConversationTargetState()
  await loadModels()
}

const toggleObsidianMode = () => {
  obsidianEnabled.value = !obsidianEnabled.value
  saveCurrentConversationTargetState()
}

const openCurrentProjectInVSCode = async () => {
  if (!currentProjectDir.value) {
    ElMessage.warning('请先选择项目文件夹')
    return
  }
  if (!window.electronAPI?.openProjectInVSCode) {
    ElMessage.warning('“在 VS Code 中打开”仅在 Codebot 桌面版可用')
    return
  }
  try {
    await window.electronAPI.openProjectInVSCode(currentProjectDir.value)
    ElMessage.success('已在 VS Code 中打开当前项目')
  } catch (error) {
    ElMessage.error(error?.message || '打开 VS Code 失败')
  }
}

const searchKnowledgeBases = async (query) => {
  knowledgeLoading.value = true
  try {
    const res = await axios.get('/api/chat/knowledge/search', { params: { query, limit: 20 } })
    knowledgeResults.value = res.data?.data?.items || []
  } catch {
    knowledgeResults.value = []
  } finally {
    knowledgeLoading.value = false
    scrollActivePanelItem(knowledgePanelRef, 'data-knowledge-index', knowledgeActiveIndex.value)
  }
}

const toggleKnowledgeBase = (kb) => {
  const key = kb.id || kb.path
  const exists = selectedKnowledgeBases.value.some((item) => (item.id || item.path) === key)
  if (exists) {
    selectedKnowledgeBases.value = selectedKnowledgeBases.value.filter((item) => (item.id || item.path) !== key)
  } else {
    selectedKnowledgeBases.value.push(kb)
  }
  obsidianEnabled.value = true
  saveCurrentConversationTargetState()
  const hashIdx = inputMessage.value.lastIndexOf('#')
  if (hashIdx !== -1) inputMessage.value = inputMessage.value.substring(0, hashIdx)
  showKnowledgePanel.value = false
  knowledgeQuery.value = ''
  nextTick(() => inputRef.value?.focus())
}

const removeKnowledgeBase = (kb) => {
  const key = kb.id || kb.path
  selectedKnowledgeBases.value = selectedKnowledgeBases.value.filter((item) => (item.id || item.path) !== key)
  saveCurrentConversationTargetState()
}

const scrollActivePanelItem = (panelRef, dataAttr, index) => {
  nextTick(() => {
    const panel = panelRef.value
    const list = panel?.querySelector?.('.command-panel-list')
    const item = panel?.querySelector?.(`[${dataAttr}="${index}"]`)
    if (!list || !item) return

    const itemTop = item.offsetTop
    const itemBottom = itemTop + item.offsetHeight
    const visibleTop = list.scrollTop
    const visibleBottom = visibleTop + list.clientHeight

    if (itemTop < visibleTop) {
      list.scrollTop = itemTop
    } else if (itemBottom > visibleBottom) {
      list.scrollTop = itemBottom - list.clientHeight
    }
  })
}

const moveActiveIndex = (current, delta, length) => {
  if (length <= 0) return 0
  return Math.min(Math.max(current + delta, 0), length - 1)
}

// ── 键盘事件处理 ─────────────────────────────────────────────────────────────
const onInputKeydown = (e) => {
  // 命令面板导航
  if (showCommandPanel.value) {
    if (e.key === 'ArrowDown') { e.preventDefault(); commandActiveIndex.value = moveActiveIndex(commandActiveIndex.value, 1, filteredCommands.value.length); scrollActivePanelItem(commandPanelRef, 'data-command-index', commandActiveIndex.value) }
    else if (e.key === 'ArrowUp') { e.preventDefault(); commandActiveIndex.value = moveActiveIndex(commandActiveIndex.value, -1, filteredCommands.value.length); scrollActivePanelItem(commandPanelRef, 'data-command-index', commandActiveIndex.value) }
    else if (e.key === 'Enter') { e.preventDefault(); if (filteredCommands.value[commandActiveIndex.value]) selectCommand(filteredCommands.value[commandActiveIndex.value]) }
    else if (e.key === 'Escape') { e.preventDefault(); showCommandPanel.value = false }
    return
  }

  // @ 面板导航
  if (showAtPanel.value) {
    if (e.key === 'ArrowDown') { e.preventDefault(); atActiveIndex.value = moveActiveIndex(atActiveIndex.value, 1, atSkills.value.length); scrollActivePanelItem(atPanelRef, 'data-at-index', atActiveIndex.value) }
    else if (e.key === 'ArrowUp') { e.preventDefault(); atActiveIndex.value = moveActiveIndex(atActiveIndex.value, -1, atSkills.value.length); scrollActivePanelItem(atPanelRef, 'data-at-index', atActiveIndex.value) }
    else if (e.key === 'Enter') { e.preventDefault(); if (atSkills.value[atActiveIndex.value]) selectAtSkill(atSkills.value[atActiveIndex.value]) }
    else if (e.key === 'Escape') { e.preventDefault(); showAtPanel.value = false }
    return
  }

  if (showKnowledgePanel.value) {
    if (e.key === 'ArrowDown') { e.preventDefault(); knowledgeActiveIndex.value = moveActiveIndex(knowledgeActiveIndex.value, 1, knowledgeResults.value.length); scrollActivePanelItem(knowledgePanelRef, 'data-knowledge-index', knowledgeActiveIndex.value) }
    else if (e.key === 'ArrowUp') { e.preventDefault(); knowledgeActiveIndex.value = moveActiveIndex(knowledgeActiveIndex.value, -1, knowledgeResults.value.length); scrollActivePanelItem(knowledgePanelRef, 'data-knowledge-index', knowledgeActiveIndex.value) }
    else if (e.key === 'Enter') { e.preventDefault(); if (knowledgeResults.value[knowledgeActiveIndex.value]) toggleKnowledgeBase(knowledgeResults.value[knowledgeActiveIndex.value]) }
    else if (e.key === 'Escape') { e.preventDefault(); showKnowledgePanel.value = false }
    return
  }

  // 正常 Enter 发送
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    sendMessage()
  }
}

const onInputChange = (val) => {
  const text = typeof val === 'string' ? val : inputMessage.value

  // 检测最后一次 / 触发命令面板
  const slashIdx = text.lastIndexOf('/')
  if (slashIdx !== -1 && (slashIdx === 0 || text[slashIdx - 1] === '\n' || text[slashIdx - 1] === ' ')) {
    const afterSlash = text.substring(slashIdx + 1)
    if (!afterSlash.includes('\n')) {
      commandQuery.value = afterSlash
      commandActiveIndex.value = 0
      showCommandPanel.value = true
      showAtPanel.value = false
      showKnowledgePanel.value = false
      return
    }
  }
  showCommandPanel.value = false

  // 检测最后一次 @ 触发 skill 面板
  const atIdx = text.lastIndexOf('@')
  if (atIdx !== -1 && (atIdx === 0 || text[atIdx - 1] === '\n' || text[atIdx - 1] === ' ')) {
    const afterAt = text.substring(atIdx + 1)
    if (!afterAt.includes('\n')) {
      atQuery.value = afterAt
      atActiveIndex.value = 0
      showAtPanel.value = true
      showCommandPanel.value = false
      showKnowledgePanel.value = false
      clearTimeout(_atSearchTimer)
      _atSearchTimer = setTimeout(() => searchAtSkills(afterAt), 200)
      return
    }
  }
  showAtPanel.value = false

  const hashIdx = text.lastIndexOf('#')
  if (hashIdx !== -1 && (hashIdx === 0 || text[hashIdx - 1] === '\n' || text[hashIdx - 1] === ' ')) {
    const afterHash = text.substring(hashIdx + 1)
    if (!afterHash.includes('\n')) {
      knowledgeQuery.value = afterHash
      knowledgeActiveIndex.value = 0
      showKnowledgePanel.value = true
      showCommandPanel.value = false
      showAtPanel.value = false
      clearTimeout(_knowledgeSearchTimer)
      _knowledgeSearchTimer = setTimeout(() => searchKnowledgeBases(afterHash), 200)
      return
    }
  }
  showKnowledgePanel.value = false
}

const abortTask = async () => {
  aborting.value = true
  try {
    await axios.post('/api/chat/abort', { conversation_id: currentConversationId.value })
    ElMessage.success('已发送终止信号')
    queuedCount.value = 0
  } catch {
    ElMessage.error('终止失败')
  } finally {
    aborting.value = false
  }
}

// 模型选择
const LAST_MODEL_KEY = 'codebot:selectedModel'
const normalizeModelId = (modelId) => {
  const id = String(modelId || '').trim()
  if (!id || !id.includes('/')) return id
  const [rawProvider, ...modelParts] = id.split('/')
  const rawModel = modelParts.join('/')
  const providerAliases = {
    copilot: 'github-copilot',
  }
  const modelAliases = {
    'GPT-41': 'gpt-4.1',
    'GPT-4.1': 'gpt-4.1',
    'GPT-4o': 'gpt-4o',
  }
  const provider = providerAliases[rawProvider] || rawProvider
  const model = modelAliases[rawModel] || rawModel
  return `${provider}/${model}`
}
const makeModelOption = (id, extra = {}) => ({
  id,
  name: id,
  provider: id.split('/')[0] || '',
  model: id.split('/')[1] || id,
  ...extra
})
const ensureSelectedModelOption = (modelId) => {
  const id = normalizeModelId(modelId)
  if (!id) return
  if (!availableModels.value.find((item) => item.id === id)) {
    availableModels.value = [makeModelOption(id, { runnable: false, source: 'saved' }), ...availableModels.value]
  }
}
const _savedModel = normalizeModelId(localStorage.getItem(LAST_MODEL_KEY) || '')
if (_savedModel) localStorage.setItem(LAST_MODEL_KEY, _savedModel)
const selectedModel = ref(_savedModel)
const selectedReasoningEffort = ref('')
// Pre-populate with the saved model so el-select can resolve its label before the API responds
const availableModels = ref(
  _savedModel
    ? [makeModelOption(_savedModel)]
    : []
)
const modelsLoading = ref(false)
const modelSearchQuery = ref('')

const syncChatDefaultModel = async (modelId) => {
  try {
    await fetch('/api/config/general', {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ chat_default_model: modelId || '' })
    })
  } catch {}
}

watch(selectedModel, async (val, previous) => {
  const normalized = normalizeModelId(val)
  if (normalized && normalized !== val) {
    selectedModel.value = normalized
    return
  }
  ensureSelectedModelOption(normalized)
  if (applyingConversationUiState) return
  if (isRakazoConversation.value) {
    const projectId = rakazoProjectId.value
    const currentRoute = rakazoProjectState.value?.mapping?.model_route_id || ''
    if (!projectId || !val || val === currentRoute) return
    if (currentLoading.value) {
      ElMessage.warning('Rakazo 正在执行任务，模型只能在空闲状态切换')
      applyingConversationUiState = true
      selectedModel.value = currentRoute || previous || ''
      nextTick(() => { applyingConversationUiState = false })
      return
    }
    try {
      rakazoModelSwitching.value = true
      await axios.put(`/api/rakazo/projects/${projectId}/model`, { route_id: val })
      await loadRakazoProjectState()
      await loadModels({ rakazo: true })
      ElMessage.success('Rakazo 模型已切换；历史轮次仍保留原路由快照')
    } catch (error) {
      applyingConversationUiState = true
      selectedModel.value = currentRoute || previous || ''
      nextTick(() => { applyingConversationUiState = false })
      ElMessage.error(error?.response?.data?.detail || '切换 Rakazo 模型失败')
    } finally {
      rakazoModelSwitching.value = false
    }
    return
  }
  if (val) {
    localStorage.setItem(LAST_MODEL_KEY, val)
  } else {
    localStorage.removeItem(LAST_MODEL_KEY)
  }
  saveCurrentConversationTargetState()
  if (!codexEnabled.value) syncChatDefaultModel(val || '')
})

watch(selectedReasoningEffort, () => {
  if (!applyingConversationUiState) saveCurrentConversationTargetState()
})

const supportedReasoningEfforts = computed(() => {
  const selected = availableModels.value.find((item) => item.id === selectedModel.value)
  const raw = selected?.supportedReasoningEfforts || selected?.supported_reasoning_efforts || []
  const values = raw
    .map((item) => typeof item === 'string' ? item : item?.effort || item?.value || item?.reasoningEffort)
    .filter(Boolean)
  if (values.length) return [...new Set(values)]
  // OpenCode 兼容模型只能使用其 provider 明确声明的 reasoning effort；
  // 没有声明时保留 Codex 默认值，避免向第三方模型发送不支持的参数。
  if (selected?.source === 'opencode') return []
  return ['minimal', 'low', 'medium', 'high', 'xhigh', 'max', 'ultra']
})

const reasoningEffortLabel = (effort) => ({
  none: '关闭', minimal: '最少', low: '低', medium: '中', high: '高', xhigh: '超高', max: '最大', ultra: '自动委派'
}[effort] || effort)

const rakazoModelStatusLabel = (status, probeState = '') => {
  if (status === 'verified') return '已自动验证'
  if (status === 'probe_failed' && probeState === 'unprobed') return '首次使用自动验证'
  if (status === 'probe_failed' && probeState === 'failed') return '上次失败，选择后重试'
  return ({
    authorization_required: '当前连接不可安全代理',
    protocol_pending: '协议待适配',
    probe_failed: '首次使用自动验证',
  }[status] || status || '首次使用自动验证')
}

const modelProviderLabel = (model) => {
  if (isRakazoConversation.value) {
    return `${model?.provider || '模型'} · ${rakazoModelStatusLabel(model?.compatibilityStatus, model?.probeState)}`
  }
  if (model?.runnable === false) return `${model?.provider || '模型'} · 当前执行器不可用`
  if (model?.source === 'opencode') {
    const protocol = String(model?.protocol || '')
    const transport = protocol === 'chat-completions-bridge'
      ? 'Chat 兼容桥'
      : protocol === 'anthropic-bridge'
        ? 'Anthropic 兼容桥'
        : 'Responses'
    return `${model?.provider || 'OpenCode'} · ${transport}`
  }
  return model?.provider || ''
}

// 按 provider 分组
const groupedModels = computed(() => {
  const groups = {}
  for (const m of availableModels.value) {
    const p = m.provider || 'other'
    if (!groups[p]) groups[p] = { provider: p, providerLabel: p, models: [] }
    groups[p].models.push(m)
  }
  return Object.values(groups)
})

// 搜索过滤结果
const filteredModels = computed(() => {
  if (!modelSearchQuery.value) return availableModels.value
  const q = modelSearchQuery.value.toLowerCase()
  return availableModels.value.filter(m =>
    m.id.toLowerCase().includes(q) ||
    m.name.toLowerCase().includes(q) ||
    (m.provider || '').toLowerCase().includes(q) ||
    (m.model || '').toLowerCase().includes(q)
  )
})

const filterModels = (query) => {
  modelSearchQuery.value = query || ''
}

const onModelDropdownOpen = (visible) => {
  if (visible) modelSearchQuery.value = ''
}

// ── 记忆提示 ──────────────────────────────────────────────────────────────
const memoryHints = ref([])
let _hintsTimer = null

const hintTagType = (category) => {
  const map = {
    habit: 'success',
    preference: 'warning',
    profile: 'info',
    fact: '',
    contact: 'danger',
    address: 'danger',
    note: 'info',
  }
  return map[category] ?? 'info'
}

const fetchMemoryHints = async (query) => {
  if (!query || query.trim().length < 3) {
    memoryHints.value = []
    return
  }
  try {
    const res = await axios.get('/api/memory/hints', { params: { query: query.trim(), top_k: 5 } })
    memoryHints.value = res.data?.data ?? []
  } catch {
    memoryHints.value = []
  }
}

watch(inputMessage, (val) => {
  clearTimeout(_hintsTimer)
  if (!val || val.trim().length < 3) {
    memoryHints.value = []
    return
  }
  _hintsTimer = setTimeout(() => fetchMemoryHints(val), 600)
})

// 批量处理
const batchMode = ref(false)
const selectedConvIds = ref([])
const selectAll = ref(false)

const isIndeterminate = computed(() => {
  return selectedConvIds.value.length > 0 && selectedConvIds.value.length < deletableConversations.value.length
})

const deletableConversations = computed(() => conversations.value.filter((conv) => !isMultiAgentHub(conv) && conv.executor !== 'rakazo'))

watch(selectedConvIds, (val) => {
  selectAll.value = val.length === deletableConversations.value.length && deletableConversations.value.length > 0
})

const toggleBatchMode = () => {
  batchMode.value = !batchMode.value
  selectedConvIds.value = []
  selectAll.value = false
}

const toggleConvSelection = (id) => {
  const conv = conversations.value.find((item) => item.id === id)
  if (isMultiAgentHub(conv)) return
  const idx = selectedConvIds.value.indexOf(id)
  if (idx === -1) {
    selectedConvIds.value.push(id)
  } else {
    selectedConvIds.value.splice(idx, 1)
  }
}

const handleSelectAll = (val) => {
  if (val) {
    selectedConvIds.value = deletableConversations.value.map(c => c.id)
  } else {
    selectedConvIds.value = []
  }
}

const batchDeleteConversations = async () => {
  if (selectedConvIds.value.length === 0) return
  try {
    await ElMessageBox.confirm(
      `确定删除选中的 ${selectedConvIds.value.length} 个对话吗？此操作不可恢复。`,
      '批量删除',
      {
        confirmButtonText: '删除',
        cancelButtonText: '取消',
        type: 'warning'
      }
    )
    const hubIds = new Set(conversations.value.filter(isMultiAgentHub).map((item) => item.id))
    const ids = [...selectedConvIds.value].filter((id) => !hubIds.has(id))
    let successCount = 0
    for (const id of ids) {
      try {
        await axios.delete(`/api/chat/conversations/${id}`)
        successCount++
        if (currentConversationId.value === id) {
          currentConversationId.value = null
          messages.value = []
        }
      } catch {}
    }
    selectedConvIds.value = []
    selectAll.value = false
    await loadConversations()
    ElMessage.success(`已删除 ${successCount} 个对话`)
  } catch (error) {
    if (error !== 'cancel') {
      ElMessage.error('批量删除失败')
    }
  }
}

const getLoadingCount = (conversationId) => {
  if (!conversationId) return 0
  return Number(loadingCounts.value[conversationId] || 0)
}

const isConversationLoading = (conversationId) => getLoadingCount(conversationId) > 0

const currentLoading = computed(() => {
  if (!currentConversationId.value) return false
  return isConversationLoading(currentConversationId.value)
})

const incrementLoading = (conversationId) => {
  const next = getLoadingCount(conversationId) + 1
  loadingCounts.value = { ...loadingCounts.value, [conversationId]: next }
}

const decrementLoading = (conversationId) => {
  const next = getLoadingCount(conversationId) - 1
  const updated = { ...loadingCounts.value }
  if (next > 0) {
    updated[conversationId] = next
  } else {
    delete updated[conversationId]
  }
  loadingCounts.value = updated
}

const setConversationLoadingState = (conversationId, running) => {
  const key = Number(conversationId)
  if (!key) return
  const updated = { ...loadingCounts.value }
  if (running) {
    updated[key] = Math.max(1, Number(updated[key] || 0))
  } else {
    delete updated[key]
  }
  loadingCounts.value = updated
}

const notifyActionRequiredEvent = (event) => {
  if (!event?.requires_user_action) return
  const key = event.request_id || event?.data?.id || event?.data?.requestID || `${event.event_type}:${event.summary}`
  if (!key || actionRequiredEventSeen.value[key]) return
  actionRequiredEventSeen.value = { ...actionRequiredEventSeen.value, [key]: true }
  const source = eventSourceName(event)
  const sourceLabel = source === 'rakazo' ? 'Rakazo' : source === 'codex' ? 'Codex' : 'OpenCode'
  ElMessage({
    type: 'warning',
    message: event.summary || `${sourceLabel} 正在等待你的选择`,
    duration: 8000,
    showClose: true,
  })
}

const looksLikeCliOutput = (content = '') => {
  const text = String(content || '')
  return /(^|\n)# Todos\b/.test(text)
    || /(^|\n)([→%*$✎◌!✓@>]|\[[ x.\-]\])\s/.test(text)
}

const isCliDisplayMessage = (msg) => {
  if (msg?.role !== 'assistant') return false
  if (msg?.source === 'codex' || msg?.source === 'rakazo') return false
  return Boolean(msg.cli_display || (opencodeCliDisplay.value && looksLikeCliOutput(msg.content)))
}

const attachCliActionEvent = (assistantMsg, event) => {
  notifyActionRequiredEvent(event)
  if (assistantMsg && event?.requires_user_action) {
    assistantMsg.pendingActionEvent = event
  }
}

const shouldShowStructuredEvent = (event) => {
  if (!event) return false
  if (eventSourceName(event) === 'rakazo') {
    return [
      'model.route', 'reasoning.delta', 'tool.started', 'tool.updated', 'tool.completed',
      'computer.updated', 'memory.updated', 'permission.requested', 'usage'
    ].includes(event?.event_type)
  }
  if (
    eventSourceName(event) === 'codex'
    && ['session.status', 'session.idle', 'session.trace', 'session.retry', 'model.route'].includes(event?.event_type)
  ) {
    return true
  }
  if (event.type === 'tool_event') {
    return ['tool', 'tool-call', 'tool-result', 'patch', 'file', 'subtask'].includes(event.event_type)
  }
  if (event.requires_user_action) return true
  return [
    'permission.asked',
    'permission.updated',
    'permission.replied',
    'permission.local_reply',
    'question.asked',
    'question.replied',
    'question.rejected',
    'question.local_reply',
    'todo.updated'
  ].includes(event.event_type)
}

const createStructuredEventMessage = (conversationId, event, seq) => ({
  id: `runtime-event-${conversationId}-${seq || Date.now()}-${Math.random()}`,
  role: 'event',
  event,
  expanded: false,
  created_at: event?.created_at || new Date().toISOString()
})

const cacheStructuredEventMessage = (conversationId, eventMsg) => {
  const convEvents = perConversationEventMessages.value[conversationId] || []
  const key = eventActionKey(eventMsg?.event)
  if (key) {
    const index = convEvents.findIndex((item) => eventActionKey(item?.event) === key)
    if (index >= 0) {
      const nextEvents = [...convEvents]
      nextEvents[index] = eventMsg
      perConversationEventMessages.value = {
        ...perConversationEventMessages.value,
        [conversationId]: nextEvents
      }
      return
    }
  }
  perConversationEventMessages.value = {
    ...perConversationEventMessages.value,
    [conversationId]: [...convEvents, eventMsg]
  }
}

const eventActionKind = (event) => {
  const type = String(event?.event_type || '')
  if (type.startsWith('question.')) return 'question'
  if (type.startsWith('permission.')) return 'permission'
  return ''
}

const eventRequestId = (event) => {
  const data = event?.data || {}
  return String(
    event?.request_id ||
    data.request_id ||
    data.requestID ||
    data.permissionID ||
    data.id ||
    ''
  ).trim()
}

const eventActionKey = (event) => {
  const kind = eventActionKind(event)
  const id = eventRequestId(event)
  return kind && id ? `${kind}:${id}` : ''
}

const eventSourceName = (event) => String(event?.source || event?.data?.source || '').toLowerCase()

const shouldUseChatEventPanel = (event) => (
  eventSourceName(event) === 'codex'
  && event?.requires_user_action
  && event?.event_type === 'question.asked'
)

const upsertStructuredEventMessage = (conversationId, event, anchorMsg = null) => {
  if (currentConversationId.value !== conversationId || !event) return null
  const key = eventActionKey(event)
  let existing = null
  if (key) {
    existing = messages.value.find((msg) => msg?.role === 'event' && eventActionKey(msg?.event) === key)
  }
  if (existing) {
    existing.event = event
    cacheStructuredEventMessage(conversationId, existing)
    notifyActionRequiredEvent(event)
    return existing
  }

  const eventMsg = createStructuredEventMessage(conversationId, event)
  const anchorIndex = anchorMsg ? messages.value.findIndex((msg) => msg.id === anchorMsg.id) : -1
  if (anchorIndex >= 0) {
    messages.value.splice(anchorIndex + 1, 0, eventMsg)
  } else {
    messages.value.push(eventMsg)
  }
  cacheStructuredEventMessage(conversationId, eventMsg)
  notifyActionRequiredEvent(event)
  return eventMsg
}

const isActionResolvedEvent = (event) => [
  'permission.replied',
  'permission.local_reply',
  'question.replied',
  'question.rejected',
  'question.local_reply',
].includes(event?.event_type)

const markActionResolved = (targetEvent, resolvedEvent) => {
  if (!targetEvent) return
  targetEvent.replied = resolvedEvent?.summary || true
  targetEvent.requires_user_action = false
  targetEvent.actions = []
  if (resolvedEvent?.summary) targetEvent.summary = resolvedEvent.summary
}

const resolvePendingActionEvents = (conversationId, resolvedEvent) => {
  if (!isActionResolvedEvent(resolvedEvent)) return
  const key = eventActionKey(resolvedEvent)
  if (!key) return
  clearQuestionPanelState(resolvedEvent)

  const clearMessage = (msg) => {
    if (eventActionKey(msg?.pendingActionEvent) === key) {
      markActionResolved(msg.pendingActionEvent, resolvedEvent)
      msg.pendingActionEvent = null
    }
    if (eventActionKey(msg?.event) === key) {
      markActionResolved(msg.event, resolvedEvent)
    }
  }

  for (const msg of messages.value) clearMessage(msg)
  const cached = perConversationEventMessages.value[conversationId] || []
  for (const msg of cached) clearMessage(msg)
}

const toggleEventDetails = (msg) => {
  if (!toolEventDetail(msg?.event)) return
  msg.expanded = !msg.expanded
}

const ensureRuntimeAssistant = (conversationId) => {
  if (currentConversationId.value !== conversationId) return null
  const knownId = runtimeAssistantIdByConversation.value[conversationId]
  if (knownId) {
    const found = messages.value.find((m) => m.id === knownId)
    if (found) return found
  }
  const msg = {
    id: `runtime-assistant-${conversationId}`,
    role: 'assistant',
    content: '',
    cli_display: opencodeCliDisplay.value,
    pendingActionEvent: null,
    streaming: true,
    created_at: new Date().toISOString()
  }
  messages.value.push(msg)
  runtimeAssistantIdByConversation.value = {
    ...runtimeAssistantIdByConversation.value,
    [conversationId]: msg.id
  }
  return msg
}

const applyRuntimeEvents = (conversationId, events, runtimeContent, running) => {
  if (currentConversationId.value !== conversationId) return
  const seen = new Set(runtimeEventSeqSeen.value[conversationId] || [])
  const runtimeHasCodex = (events || []).some((event) => eventSourceName(event) === 'codex' || event?.source === 'codex')
  const runtimeHasRakazo = (events || []).some((event) => eventSourceName(event) === 'rakazo' || event?.source === 'rakazo')
  let assistantMsg = null
  const newEventMsgs = []
  for (const event of (events || [])) {
    const seq = Number(event?.seq || 0)
    if (seq > 0 && seen.has(seq)) continue
    if (seq > 0) seen.add(seq)
    if (event?.type === 'tool_event' || event?.type === 'meta_event') {
      resolvePendingActionEvents(conversationId, event)
      const eventSource = String(event?.source || event?.data?.source || '').toLowerCase()
      const forceStructuredEvent = ['codex', 'rakazo'].includes(eventSource) && !event?.requires_user_action
      if (shouldUseChatEventPanel(event)) {
        assistantMsg = assistantMsg || ensureRuntimeAssistant(conversationId)
        upsertStructuredEventMessage(conversationId, event, assistantMsg)
        continue
      }
      if (!forceStructuredEvent && (event?.cli_inline || opencodeCliDisplay.value)) {
        assistantMsg = assistantMsg || ensureRuntimeAssistant(conversationId)
        attachCliActionEvent(assistantMsg, event)
        continue
      }
      if (!shouldShowStructuredEvent(event)) {
        notifyActionRequiredEvent(event)
        continue
      }
      const eventMsg = createStructuredEventMessage(conversationId, event, seq)
      if (assistantMsg) {
        const idx = messages.value.findIndex((m) => m.id === assistantMsg.id)
        if (idx >= 0) {
          messages.value.splice(idx, 0, eventMsg)
        } else {
          messages.value.push(eventMsg)
        }
      } else {
        messages.value.push(eventMsg)
      }
      newEventMsgs.push(eventMsg)
      notifyActionRequiredEvent(event)
      continue
    }
    if (event?.type === 'done' && assistantMsg) {
      assistantMsg.cli_display = Boolean(event?.cli_display || assistantMsg.cli_display)
      if (event?.source) assistantMsg.source = event.source
      assistantMsg.content = assistantMsg.cli_display
        ? (event.content || runtimeContent || assistantMsg.content)
        : sanitizeStreamContent(event.content || runtimeContent || assistantMsg.content)
      assistantMsg.streaming = false
      continue
    }
    if (event?.type === 'error' && assistantMsg) {
      assistantMsg.content = event.message || event.error || '执行失败'
      assistantMsg.streaming = false
    }
    if (running && (event?.type === 'done' || event?.type === 'error') && !assistantMsg) {
      assistantMsg = ensureRuntimeAssistant(conversationId)
      if (!assistantMsg) continue
      if (event?.type === 'done') {
        assistantMsg.cli_display = Boolean(event?.cli_display || assistantMsg.cli_display)
        if (event?.source) assistantMsg.source = event.source
        assistantMsg.content = assistantMsg.cli_display
          ? (event.content || runtimeContent || assistantMsg.content)
          : sanitizeStreamContent(event.content || runtimeContent || assistantMsg.content)
        assistantMsg.streaming = false
      } else {
        assistantMsg.content = event.message || event.error || '执行失败'
        assistantMsg.streaming = false
      }
    }
  }
  // 缓存新增的 event 消息，确保切换对话后可以恢复
  if (newEventMsgs.length > 0) {
    const existing = perConversationEventMessages.value[conversationId] || []
    const existingIds = new Set(existing.map((e) => e.id))
    const toAdd = newEventMsgs.filter((e) => !existingIds.has(e.id))
    if (toAdd.length > 0) {
      perConversationEventMessages.value = {
        ...perConversationEventMessages.value,
        [conversationId]: [...existing, ...toAdd]
      }
    }
  }
  runtimeEventSeqSeen.value = {
    ...runtimeEventSeqSeen.value,
    [conversationId]: Array.from(seen).slice(-300)
  }
  if (running && ((typeof runtimeContent === 'string' && runtimeContent) || running)) {
    assistantMsg = assistantMsg || ensureRuntimeAssistant(conversationId)
  }
  if (assistantMsg) {
    if (runtimeHasCodex) {
      assistantMsg.source = 'codex'
      assistantMsg.cli_display = false
    } else if (runtimeHasRakazo) {
      assistantMsg.source = 'rakazo'
      assistantMsg.cli_display = false
    }
    if (typeof runtimeContent === 'string' && runtimeContent) {
      assistantMsg.content = assistantMsg.cli_display ? runtimeContent : sanitizeStreamContent(runtimeContent)
    }
    assistantMsg.streaming = Boolean(running)
  }
  nextTick(() => scrollToBottom())
}

const fetchQueueStatus = async (conversationId, options = {}) => {
  const key = Number(conversationId)
  if (!key) return
  if (queueStatusSyncing.value[key]) return
  queueStatusSyncing.value = { ...queueStatusSyncing.value, [key]: true }
  try {
    const sinceSeq = Number(runtimeSeqByConversation.value[key] || 0)
    const response = await axios.get(`/api/chat/queue_status/${key}`, { params: { since_seq: sinceSeq } })
    const data = response.data?.data || {}
    const running = Boolean(data.running)
    const queued = Number(data.queued || 0)
    const runtimeEvents = Array.isArray(data.runtime_events) ? data.runtime_events : []
    const runtimeLastSeq = Number(data.runtime_last_seq || sinceSeq || 0)
    const runtimeContent = typeof data.runtime_content === 'string' ? data.runtime_content : ''
    const hasDoneEvent = runtimeEvents.some((e) => e?.type === 'done' || e?.type === 'error')
    const previousRunning = Boolean(serverRunningStatus.value[key])
    serverRunningStatus.value = { ...serverRunningStatus.value, [key]: running }
    runtimeSeqByConversation.value = { ...runtimeSeqByConversation.value, [key]: runtimeLastSeq }
    if (currentConversationId.value === key) {
      queuedCount.value = queued
    }
    setConversationLoadingState(key, running)
    // 跳过 applyRuntimeEvents：流式传输进行中 OR 流刚结束等待 reload OR reload 已完成
    const skipRuntimeApply = activeStreamByConversation.value[key]
      || runtimeReloadDoneByConversation.value[key] === 'pending'
      || runtimeReloadDoneByConversation.value[key] === true
    if (!skipRuntimeApply) {
      applyRuntimeEvents(key, runtimeEvents, runtimeContent, running)
    }
    const shouldReloadAfterRuntime = options.reloadOnFinish
      && !running
      && currentConversationId.value === key
      && !activeStreamByConversation.value[key]
      && runtimeReloadDoneByConversation.value[key] !== true
      && (previousRunning || hasDoneEvent || Boolean(runtimeAssistantIdByConversation.value[key])
        || runtimeReloadDoneByConversation.value[key] === 'pending')
    if (shouldReloadAfterRuntime) {
      const msgResp = await axios.get(`/api/chat/conversations/${key}/messages`)
      const dbMessages = msgResp.data?.data?.items || []
      // 流结束后从 DB 加载最终消息，不再重新注入缓存的 event 气泡。
      // event 气泡是流式传输期间的临时 UI 元素，流结束后不需要保留。
      // 清除该对话的 event 缓存，防止重复注入。
      messages.value = dbMessages
      const updatedCache = { ...perConversationEventMessages.value }
      delete updatedCache[key]
      perConversationEventMessages.value = updatedCache
      runtimeAssistantIdByConversation.value = { ...runtimeAssistantIdByConversation.value, [key]: null }
      runtimeEventSeqSeen.value = { ...runtimeEventSeqSeen.value, [key]: [] }
      runtimeReloadDoneByConversation.value = { ...runtimeReloadDoneByConversation.value, [key]: true }
      scheduleConversationTitleRefresh(key, 20, 1500)
      await nextTick()
      scrollToBottom()
    } else if (running) {
      runtimeReloadDoneByConversation.value = { ...runtimeReloadDoneByConversation.value, [key]: false }
    }
  } catch (error) {
  } finally {
    const updated = { ...queueStatusSyncing.value }
    delete updated[key]
    queueStatusSyncing.value = updated
  }
}

// 加载对话列表
const getMostRecentConversationId = (items) => {
  if (!Array.isArray(items) || items.length === 0) return null
  let bestId = items[0]?.id ?? null
  let bestTime = -1
  for (const item of items) {
    const raw = item?.updated_at ?? item?.created_at ?? ''
    const time = Number.isFinite(Date.parse(raw)) ? Date.parse(raw) : -1
    if (time > bestTime) {
      bestTime = time
      bestId = item?.id ?? bestId
    }
  }
  return bestId
}

const loadConversations = async (autoSelect = false) => {
  try {
    const response = await axios.get('/api/chat/conversations')
    conversations.value = response.data.data.items || []
    if (currentConversationId.value) {
      currentConversation.value = conversations.value.find((item) => item.id === currentConversationId.value) || currentConversation.value
    }
    if (currentConversationId.value) {
      const currentConversation = conversations.value.find((item) => item.id === currentConversationId.value)
      if (currentConversation) {
        if (isPlaceholderConversationTitle(currentConversation.title)) {
          scheduleConversationTitleRefresh(currentConversationId.value)
        } else {
          stopConversationTitleRefresh(currentConversationId.value)
        }
      }
    }
    if (autoSelect && !currentConversationId.value && conversations.value.length > 0) {
      const lastIdRaw = localStorage.getItem(LAST_CONVERSATION_KEY)
      const lastId = lastIdRaw ? Number(lastIdRaw) : null
      const hasLast = lastId && conversations.value.some((c) => c.id === lastId)
      const pickId = hasLast ? lastId : getMostRecentConversationId(conversations.value)
      if (pickId) {
        await selectConversation(pickId)
      }
    }
  } catch (error) {
    ElMessage.error('加载对话列表失败')
  }
}

// 加载可用模型列表
const loadModels = async (options = {}) => {
  const manual = Boolean(options?.manual)
  const useRakazo = Boolean(options?.rakazo || isRakazoConversation.value)
  modelsLoading.value = true
  try {
    const res = await axios.get(useRakazo ? '/api/rakazo/models' : codexEnabled.value ? '/api/codex/models' : '/api/chat/models', {
      // Rakazo 直接镜像 OpenCode 当前连接的可选目录。未探测路由可以选择，
      // 后端会在首次创建/切换时自动做真实文本、流式与工具门禁。
      params: useRakazo
        ? { refresh: manual ? true : undefined }
        : undefined,
    })
    if (manual && res.data?.success === false) {
      ElMessage.error(res.data?.message || '刷新模型列表失败')
    }
    const rawData = res.data?.data
    const raw = useRakazo
      ? (Array.isArray(rawData) ? rawData : (rawData?.models || []))
      : codexEnabled.value
      ? (Array.isArray(rawData) ? rawData : rawData?.models || [])
      : (rawData?.models || [])
    const newList = raw.map(m => {
      if (typeof m === 'string') return makeModelOption(m, { runnable: true, source: 'server' })
      const id = m.id || m.modelID || m.name || ''
      const provider = m.provider || id.split('/')[0] || ''
      const model = m.model || id.split('/')[1] || id
      return {
        id,
        name: m.displayName || m.display_name || m.name || id,
        provider,
        model,
        source: m.source || '',
        runnable: useRakazo ? m.selectable !== false : m.runnable !== false,
        selectable: useRakazo ? m.selectable !== false : m.runnable !== false,
        protocol: m.protocol || '',
        transport: m.transport || '',
        compatibilityStatus: m.compatibilityStatus || '',
        probeState: m.probeState || '',
        probeRequired: Boolean(m.probeRequired),
        incompatibilityReason: m.incompatibilityReason || '',
        routeFingerprint: m.routeFingerprint || '',
        lastProbeAt: m.lastProbeAt || null,
        supportedReasoningEfforts: m.supportedReasoningEfforts || m.supported_reasoning_efforts || [],
      }
    }).filter(m => m.id)

    // 如果用户有已保存的模型，且新列表中没有对应条目，则保留一个占位条目以避免 el-select 显示空值
    const saved = useRakazo
      ? normalizeModelId(rakazoProjectState.value?.mapping?.model_route_id || selectedModel.value)
      : normalizeModelId(selectedModel.value)
    if (saved && saved !== selectedModel.value) {
      selectedModel.value = saved
    }
    if (saved && !newList.find(m => m.id === saved)) {
      if (!useRakazo && codexEnabled.value && saved.includes('/')) {
        // 当前运行时没有返回该旧 OpenCode 模型时清空选择，阻止它误落到
        // ChatGPT 账号通道；支持桥接的 Chat/Anthropic 模型会正常出现在 newList。
        selectedModel.value = ''
        localStorage.removeItem(LAST_MODEL_KEY)
        ElMessage.warning(`模型 ${saved} 不支持 Codex Responses，已切换为 Codex 默认模型；关闭 Codex 后仍可继续使用原模型。`)
      } else if (!useRakazo) {
        newList.push(makeModelOption(saved, { runnable: false, source: 'saved' }))
      }
    }
    availableModels.value = newList
    if (manual && res.data?.message) {
      ElMessage.warning(res.data.message)
    }
  } catch {
    // 加载失败时，如果有已保存模型，保留其占位条目
    const saved = useRakazo ? '' : normalizeModelId(selectedModel.value)
    if (saved && saved !== selectedModel.value) {
      selectedModel.value = saved
    }
    if (saved) {
      availableModels.value = [makeModelOption(saved, { runnable: false, source: 'saved' })]
    } else {
      availableModels.value = []
    }
    if (manual) {
      ElMessage.error('刷新模型列表失败')
    }
  } finally {
    modelsLoading.value = false
  }
}

const loadThirdPartyStatus = async () => {
  try {
    const response = await axios.get('/api/mcp/codebot/status')
    thirdPartyStatus.value = response.data?.data ?? null
  } catch {
    thirdPartyStatus.value = null
  }
}

const refreshThirdPartyConnections = async () => {
  thirdPartyStatusRefreshing.value = true
  try {
    await Promise.all([loadThirdPartyStatus(), loadModels()])
    ElMessage.success('连接状态已刷新')
  } catch {
    ElMessage.error('刷新连接状态失败')
  } finally {
    thirdPartyStatusRefreshing.value = false
  }
}

const loadRakazoProjectState = async () => {
  if (!rakazoProjectId.value) {
    rakazoProjectState.value = null
    return
  }
  try {
    const response = await axios.get(`/api/rakazo/projects/${rakazoProjectId.value}`)
    rakazoProjectState.value = response.data?.data || null
  } catch (error) {
    rakazoProjectState.value = null
    if (isRakazoConversation.value) ElMessage.error(error?.response?.data?.detail || '加载 Rakazo 项目状态失败')
  }
}

const lookupNewRakazoProject = async () => {
  newConversationRakazoExists.value = false
  if (newConversationForm.value.executor !== 'rakazo' || !newConversationForm.value.project_dir) return
  try {
    const response = await axios.get('/api/rakazo/projects/lookup', { params: { project_dir: newConversationForm.value.project_dir } })
    const data = response.data?.data || {}
    newConversationRakazoExists.value = Boolean(data.exists)
    if (data.project?.model_route_id) newConversationForm.value.route_id = data.project.model_route_id
  } catch (error) {
    ElMessage.error(error?.response?.data?.detail || '检查 Rakazo 项目失败')
  }
}

const selectNewExecutor = async (executor) => {
  const preferredModel = normalizeModelId(selectedModel.value)
  newConversationForm.value.executor = executor
  newConversationRakazoExists.value = false
  newConversationForm.value.route_id = ''
  if (executor === 'rakazo') {
    await loadModels({ rakazo: true })
    newConversationForm.value.route_id = pickInitialRakazoModel(availableModels.value, preferredModel)?.id || ''
    await lookupNewRakazoProject()
  }
}

const openNewConversationDialog = () => {
  newConversationForm.value = { executor: 'opencode', project_dir: '', route_id: '' }
  newConversationRakazoExists.value = false
  showNewConversationDialog.value = true
}

const selectNewConversationProject = async () => {
  let value = ''
  if (window.electronAPI?.selectFolder) {
    value = await window.electronAPI.selectFolder({ title: '选择新对话的项目文件夹' })
  } else {
    try {
      const result = await ElMessageBox.prompt('请输入项目文件夹完整路径', '选择项目', {
        inputValue: newConversationForm.value.project_dir,
        confirmButtonText: '确定',
        cancelButtonText: '取消',
      })
      value = result.value?.trim() || ''
    } catch {
      return
    }
  }
  if (!value) return
  newConversationForm.value.project_dir = value
  await lookupNewRakazoProject()
}

// 创建普通固定执行器对话，或打开/恢复项目唯一的 Rakazo 主会话。
const createNewConversation = async () => {
  if (!canCreateConversation.value) return
  creatingConversation.value = true
  try {
    let response
    if (newConversationForm.value.executor === 'rakazo') {
      response = await axios.post('/api/rakazo/projects/open', {
        project_dir: newConversationForm.value.project_dir,
        route_id: newConversationForm.value.route_id || null,
      })
    } else {
      response = await axios.post('/api/chat/conversations', {
        title: '新对话',
        project_dir: newConversationForm.value.project_dir || null,
        conversation_type: 'normal',
        executor: newConversationForm.value.executor,
      })
    }
    const conversation = newConversationForm.value.executor === 'rakazo'
      ? response.data?.data?.conversation
      : response.data?.data
    if (!conversation?.id) throw new Error('后端未返回对话 ID')
    showNewConversationDialog.value = false
    await loadConversations()
    await selectConversation(conversation.id)
    ElMessage.success(response.data?.message || '对话已创建')
  } catch (error) {
    ElMessage.error(error?.response?.data?.detail || error?.message || '创建对话失败')
  } finally {
    creatingConversation.value = false
  }
}

const loadHandoffPreview = async () => {
  if (!handoffForm.value.source_conversation_id) return
  handoffPreviewLoading.value = true
  try {
    const response = await axios.post('/api/chat/handoff/preview', {
      source_conversation_id: handoffForm.value.source_conversation_id,
      target_executor: handoffForm.value.target_executor,
    })
    const data = response.data?.data || {}
    handoffForm.value.summary = data.summary || ''
    if (!handoffForm.value.project_dir) handoffForm.value.project_dir = data.projectDir || ''
  } catch (error) {
    ElMessage.error(error?.response?.data?.detail || '生成交接摘要失败')
  } finally {
    handoffPreviewLoading.value = false
  }
}

const loadHandoffModels = async () => {
  handoffModelsLoading.value = true
  try {
    const response = await axios.get('/api/rakazo/models', {
      params: { offset: 0, limit: 200 },
    })
    const payload = response.data?.data || {}
    const catalog = Array.isArray(payload) ? payload : (payload.models || [])
    handoffModels.value = catalog.filter((model) => model?.selectable !== false)
    if (!handoffForm.value.route_id) {
      handoffForm.value.route_id = pickInitialRakazoModel(handoffModels.value, selectedModel.value)?.id || ''
    }
  } catch (error) {
    handoffModels.value = []
    ElMessage.error(error?.response?.data?.detail || '加载 OpenCode 当前可用模型失败')
  } finally {
    handoffModelsLoading.value = false
  }
}

const lookupHandoffRakazoProject = async () => {
  handoffRakazoExists.value = false
  if (handoffForm.value.target_executor !== 'rakazo' || !handoffForm.value.project_dir) return
  try {
    const response = await axios.get('/api/rakazo/projects/lookup', {
      params: { project_dir: handoffForm.value.project_dir },
    })
    const data = response.data?.data || {}
    handoffRakazoExists.value = Boolean(data.exists)
    if (data.project?.model_route_id) handoffForm.value.route_id = data.project.model_route_id
  } catch (error) {
    ElMessage.error(error?.response?.data?.detail || '检查 Rakazo 主会话失败')
  }
}

const selectHandoffExecutor = async (executor) => {
  if (!executor || executor === handoffForm.value.source_executor) return
  handoffForm.value.target_executor = executor
  handoffForm.value.route_id = ''
  handoffRakazoExists.value = false
  await loadHandoffPreview()
  if (executor === 'rakazo') {
    await loadHandoffModels()
    await lookupHandoffRakazoProject()
  }
}

const openHandoffDialog = async (conversation) => {
  const sourceExecutor = String(conversation?.executor || 'opencode').toLowerCase()
  const targetExecutor = executorOptions.find((item) => item.id !== sourceExecutor)?.id || 'opencode'
  handoffForm.value = {
    source_conversation_id: conversation?.id || null,
    source_title: conversation?.title || '新对话',
    source_executor: sourceExecutor,
    target_executor: targetExecutor,
    project_dir: conversation?.project_dir || '',
    route_id: '',
    summary: '',
  }
  handoffModels.value = []
  handoffRakazoExists.value = false
  handoffDialogVisible.value = true
  await loadHandoffPreview()
  if (targetExecutor === 'rakazo') {
    await loadHandoffModels()
    await lookupHandoffRakazoProject()
  }
}

const selectHandoffProject = async () => {
  let value = ''
  if (window.electronAPI?.selectFolder) {
    value = await window.electronAPI.selectFolder({ title: '选择交接目标项目文件夹' })
  } else {
    try {
      const result = await ElMessageBox.prompt('请输入项目文件夹完整路径', '选择交接项目', {
        inputValue: handoffForm.value.project_dir,
        confirmButtonText: '确定',
        cancelButtonText: '取消',
      })
      value = result.value?.trim() || ''
    } catch {
      return
    }
  }
  if (!value) return
  handoffForm.value.project_dir = value
  handoffForm.value.route_id = pickInitialRakazoModel(handoffModels.value, selectedModel.value)?.id || ''
  await lookupHandoffRakazoProject()
}

const commitHandoff = async () => {
  if (!canCommitHandoff.value) return
  handoffCommitting.value = true
  let targetConversationId = null
  try {
    let conversation
    let rakazoRoute = ''
    if (handoffForm.value.target_executor === 'rakazo') {
      const response = await axios.post('/api/rakazo/projects/open', {
        project_dir: handoffForm.value.project_dir,
        route_id: handoffForm.value.route_id || null,
      })
      conversation = response.data?.data?.conversation
      rakazoRoute = response.data?.data?.project?.model_route_id || handoffForm.value.route_id || ''
    } else {
      const response = await axios.post('/api/chat/conversations', {
        title: `交接 · ${handoffForm.value.source_title || '新对话'}`,
        project_dir: handoffForm.value.project_dir || null,
        conversation_type: 'normal',
        executor: handoffForm.value.target_executor,
      })
      conversation = response.data?.data
    }
    if (!conversation?.id) throw new Error('目标执行器没有返回会话 ID')
    targetConversationId = conversation.id

    const summary = handoffForm.value.summary.trim()
    await axios.post(`/api/chat/conversations/${targetConversationId}/messages`, { content: summary })
    const target = handoffForm.value.target_executor === 'opencode' ? 'codebot' : handoffForm.value.target_executor
    const execution = await axios.post('/api/chat/send', {
      conversation_id: targetConversationId,
      message: summary,
      model: handoffForm.value.target_executor === 'rakazo' ? rakazoRoute : null,
      mode: handoffForm.value.target_executor === 'rakazo' ? null : 'agent',
      project_dir: handoffForm.value.project_dir || null,
      target,
      user_already_saved: true,
    })
    handoffDialogVisible.value = false
    await loadConversations()
    await selectConversation(targetConversationId)
    ElMessage.success(execution.data?.data?.queued ? '交接摘要已写入目标会话并排队' : '执行器交接完成；原对话保持不变')
  } catch (error) {
    if (targetConversationId) {
      await loadConversations()
      await selectConversation(targetConversationId)
    }
    ElMessage.error(error?.response?.data?.detail || error?.message || '执行器交接失败')
  } finally {
    handoffCommitting.value = false
  }
}

const openRakazoDrawer = () => {
  rakazoDrawerVisible.value = true
}

const loadRakazoDrawer = async () => {
  if (!rakazoProjectId.value) return
  rakazoDrawerLoading.value = true
  try {
    const projectId = rakazoProjectId.value
    const [projectResult, memoryResult, permissionsResult, logsResult] = await Promise.allSettled([
      axios.get(`/api/rakazo/projects/${projectId}`),
      axios.get(`/api/rakazo/projects/${projectId}/memory`),
      axios.get(`/api/rakazo/projects/${projectId}/permissions`),
      axios.get(`/api/rakazo/projects/${projectId}/logs`),
    ])
    if (projectResult.status === 'fulfilled') rakazoProjectState.value = projectResult.value.data?.data || null
    if (memoryResult.status === 'fulfilled') rakazoMemory.value = memoryResult.value.data?.data || null
    if (permissionsResult.status === 'fulfilled') rakazoPermissions.value = permissionsResult.value.data?.data || null
    if (logsResult.status === 'fulfilled') rakazoLogs.value = logsResult.value.data?.data || []
  } finally {
    rakazoDrawerLoading.value = false
  }
}

const saveRakazoPermissions = async (field, value) => {
  if (!rakazoProjectId.value || !rakazoPermissions.value) return
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
      rakazoPermissions.value[field] = false
      return
    }
  }
  rakazoPermissionSaving.value = true
  try {
    const response = await axios.put(`/api/rakazo/projects/${rakazoProjectId.value}/permissions`, {
      projectRead: Boolean(rakazoPermissions.value.projectRead),
      projectWrite: Boolean(rakazoPermissions.value.projectWrite),
      commandExecution: Boolean(rakazoPermissions.value.commandExecution),
      externalNetwork: Boolean(rakazoPermissions.value.externalNetwork),
      networkPolicy: rakazoPermissions.value.externalNetwork ? 'allow' : 'deny',
    })
    rakazoPermissions.value = response.data?.data || rakazoPermissions.value
    ElMessage.success('Rakazo 项目权限已更新')
  } catch (error) {
    ElMessage.error(error?.response?.data?.detail || '更新 Rakazo 权限失败')
    await loadRakazoDrawer()
  } finally {
    rakazoPermissionSaving.value = false
  }
}

const rakazoComputerAction = async (action) => {
  if (!rakazoProjectId.value) return
  try {
    await axios.post(`/api/rakazo/projects/${rakazoProjectId.value}/computer`, { action })
    ElMessage.success('Computer 操作已提交')
    await loadRakazoDrawer()
  } catch (error) {
    ElMessage.error(error?.response?.data?.detail || 'Computer 操作失败')
  }
}

// 选择对话
const selectConversation = async (conversationId) => {
  shouldAutoScroll.value = true
  if (currentConversationId.value && currentConversationId.value !== conversationId) {
    saveCurrentConversationTargetState()
  }
  currentConversationId.value = conversationId
  localStorage.setItem(LAST_CONVERSATION_KEY, String(conversationId))
  try {
    // 对话详情与消息互不依赖，并行请求可显著缩短页面回切等待。
    const [convResponse, response] = await Promise.all([
      axios.get(`/api/chat/conversations/${conversationId}`),
      axios.get(`/api/chat/conversations/${conversationId}/messages`)
    ])
    const convData = convResponse.data?.data
    currentConversation.value = convData || null
    currentProjectDir.value = convData?.project_dir || ''
    applyConversationTargetState(conversationId)
    if (isMultiAgentHub(convData)) {
      await loadMultiAgentMembers()
    }

    const dbMessages = response.data.data.items || []
    // 恢复该对话已缓存的 event 气泡（推理过程），仅在流式传输仍在进行中时
    // 如果 runtimeReloadDone 已为 true，说明流已结束并已做过 DB reload，不需要再注入缓存
    const cachedEvents = perConversationEventMessages.value[conversationId] || []
    const streamDone = Boolean(runtimeReloadDoneByConversation.value[conversationId])
    if (cachedEvents.length > 0 && !streamDone) {
      messages.value = [...dbMessages, ...cachedEvents].sort((a, b) => {
        const ta = Date.parse(a.created_at) || 0
        const tb = Date.parse(b.created_at) || 0
        return ta - tb
      })
    } else {
      messages.value = dbMessages
    }
    runtimeAssistantIdByConversation.value = { ...runtimeAssistantIdByConversation.value, [conversationId]: null }
    runtimeEventSeqSeen.value = { ...runtimeEventSeqSeen.value, [conversationId]: [] }
    runtimeReloadDoneByConversation.value = { ...runtimeReloadDoneByConversation.value, [conversationId]: false }
    await fetchQueueStatus(conversationId)
    if (isPlaceholderConversationTitle(convData?.title) && dbMessages.length > 0) {
      scheduleConversationTitleRefresh(conversationId)
    }
    await nextTick()
    scrollToBottom()
  } catch (error) {
    ElMessage.error('加载消息失败')
  }
}

const toolEventLabel = (event) => {
  const eventType = event?.event_type || 'event'
  const map = {
    'step-start': '步骤开始',
    'step-finish': '步骤完成',
    'tool': '工具',
    'tool-call': '工具调用',
    'tool-result': '工具结果',
    'reasoning': '推理',
    'plan': '计划',
    'subtask': '子任务',
    'patch': '补丁',
    'snapshot': '快照',
    'agent': 'Agent',
    'retry': '重试',
    'compaction': '上下文压缩',
    'permission.asked': '等待确认',
    'permission.updated': '等待确认',
    'permission.replied': '确认结果',
    'permission.local_reply': '已回复',
    'question.asked': '等待选择',
    'question.replied': '选择结果',
    'question.rejected': '已取消',
    'question.local_reply': '已回复',
    'todo.updated': '待办',
    'session.status': '会话状态',
    'session.idle': '会话状态',
    'session.trace': '运行轨迹',
    'session.retry': '自动重试',
    'session.error': '会话错误',
    'model.route': '模型路由',
    'reasoning.delta': '推理',
    'tool.started': '工具开始',
    'tool.updated': '工具进度',
    'tool.completed': '工具完成',
    'computer.updated': 'Computer',
    'memory.updated': '记忆更新',
    'permission.requested': '等待确认',
    'usage': '用量',
    'message.updated': '消息状态',
    'message.part.updated': '消息片段',
    'file.edited': '文件编辑',
    'command.executed': '命令',
    'tui.toast.show': 'OpenCode',
    'tui.prompt.append': 'OpenCode',
    'tui.command.execute': 'OpenCode'
  }
  return map[eventType] || eventType
}

const toolEventSummary = (event) => {
  if (typeof event?.summary === 'string' && event.summary.trim()) return event.summary.trim()
  const data = event?.data || {}
  if (typeof data?.text === 'string' && data.text.trim()) return data.text.trim()
  if (typeof data?.name === 'string' && data.name.trim()) return data.name.trim()
  if (typeof data?.tool === 'string' && data.tool.trim()) return data.tool.trim()
  if (typeof data?.reason === 'string' && data.reason.trim()) return data.reason.trim()
  if (typeof data?.state === 'string' && data.state.trim()) return data.state.trim()
  if (typeof data?.summary === 'string' && data.summary.trim()) return data.summary.trim()
  if (typeof data?.status === 'string' && data.status.trim()) return data.status.trim()
  return '处理中'
}

const toolEventDetail = (event) => {
  if (typeof event?.detail === 'string' && event.detail.trim()) return event.detail.trim()
  const data = event?.data || {}
  if (data?.state && typeof data.state === 'object') {
    const output = data.state.output || data.state.error || data.state.raw
    if (typeof output === 'string' && output.trim()) return output.trim()
  }
  return ''
}

const classifyCodexEventClass = (event) => {
  const source = (event?.source || event?.data?.source || '').toLowerCase()
  if (source === 'rakazo') {
    if (event?.event_type === 'model.route' || event?.event_type === 'computer.updated' || event?.event_type === 'memory.updated') return 'codex-status'
    if (event?.event_type?.includes('reasoning')) return 'codex-trace'
    if (event?.event_type?.startsWith('tool.')) return 'codex-tool'
    return ''
  }
  if (source !== 'codex') return ''
  if (event?.event_type === 'session.idle') return 'codex-idle'
  if (event?.event_type === 'session.status') return 'codex-status'
  if (event?.event_type === 'model.route') return 'codex-status'
  if (event?.event_type?.includes('reasoning')) return 'codex-trace'
  if (event?.event_type === 'item/started' || event?.event_type === 'item/completed') return 'codex-tool'
  return ''
}

const toolEventActions = (event) => {
  if (!event || event.replied || shouldShowQuestionPanel(event)) return []
  return Array.isArray(event.actions) ? event.actions : []
}

const toolEventActionKey = (action) => `${action?.reply || ''}:${action?.label || ''}`

const questionPanelStateKey = (event) => {
  const requestId = event?.request_id || event?.data?.id || event?.data?.requestID
  return requestId ? String(requestId) : ''
}

const normalizeQuestionPanelOptions = (options) => {
  if (!Array.isArray(options)) return []
  return options
    .map((option) => {
      if (typeof option === 'string') return { label: option.trim(), value: option.trim(), description: '' }
      if (!option || typeof option !== 'object') return null
      return {
        label: String(option.label || '').trim(),
        value: String(option.value || option.label || '').trim(),
        description: String(option.description || '').trim(),
      }
    })
    .filter((option) => option?.label)
}

const parseQuestionOptionsFromDetail = (detail = '') => {
  const lines = String(detail || '').split('\n')
  const options = []
  for (const line of lines) {
    const match = line.match(/^\s*\d+[.)、]\s+(.+?)\s*$/)
    if (!match) continue
    const text = match[1].trim()
    if (!text) continue
    const separator = text.search(/[：:。]/)
    const label = separator > 0 ? text.slice(0, separator).trim() : text
    const description = separator > 0 ? text.slice(separator + 1).trim() : ''
    options.push({ label, description })
  }
  return options
}

const normalizeQuestionPanelQuestion = (question, event) => {
  const fallbackOptions = parseQuestionOptionsFromDetail(toolEventDetail(event))
  const rawOptions = normalizeQuestionPanelOptions(question?.options)
  return {
    header: String(question?.header || 'Question').trim(),
    question: String(question?.question || event?.question || event?.summary || 'OpenCode 正在等待你的选择').trim(),
    multiple: Boolean(question?.multiple),
    custom: question?.custom !== false,
    input_type: String(question?.input_type || question?.inputType || 'textarea').trim().toLowerCase(),
    placeholder: String(question?.placeholder || '').trim(),
    options: rawOptions.length > 0 ? rawOptions : fallbackOptions,
  }
}

const getQuestionEventQuestions = (event) => {
  if (Array.isArray(event?.questions) && event.questions.length > 0) {
    return event.questions.map((question) => normalizeQuestionPanelQuestion(question, event))
  }
  if (Array.isArray(event?.data?.questions) && event.data.questions.length > 0) {
    return event.data.questions.map((question) => normalizeQuestionPanelQuestion(question, event))
  }
  const question = String(event?.question || event?.summary || 'OpenCode 正在等待你的选择').trim()
  const options = normalizeQuestionPanelOptions(event?.data?.options)
  return [{
    question,
    multiple: Boolean(event?.multiple),
    custom: event?.allow_custom !== false,
    options: options.length > 0 ? options : parseQuestionOptionsFromDetail(toolEventDetail(event)),
  }]
}

const ensureQuestionPanelState = (event) => {
  const key = questionPanelStateKey(event)
  const questions = getQuestionEventQuestions(event)
  if (!key || questions.length === 0) return null
  const existing = questionPanelState.value[key]
  if (existing && existing.answers.length === questions.length) return existing
  const next = {
    tab: 0,
    answers: questions.map(() => []),
    custom: questions.map(() => ''),
    customOn: questions.map(() => false),
    sending: false,
  }
  questionPanelState.value = { ...questionPanelState.value, [key]: next }
  return next
}

const updateQuestionPanelState = (event, updater) => {
  const key = questionPanelStateKey(event)
  const current = ensureQuestionPanelState(event)
  if (!key || !current) return null
  const next = typeof updater === 'function' ? updater(current) : updater
  questionPanelState.value = { ...questionPanelState.value, [key]: next }
  return next
}

const clearQuestionPanelState = (event) => {
  const key = questionPanelStateKey(event)
  if (!key || !questionPanelState.value[key]) return
  const next = { ...questionPanelState.value }
  delete next[key]
  questionPanelState.value = next
}

const shouldShowQuestionPanel = (event) => {
  if (!event || event.replied || event.event_type !== 'question.asked') return false
  return getQuestionEventQuestions(event).length > 0
}

const getQuestionPanelTab = (event) => ensureQuestionPanelState(event)?.tab ?? 0

const setQuestionPanelTab = (event, tab) => {
  const questions = getQuestionEventQuestions(event)
  const nextTab = Math.max(0, Math.min(questions.length - 1, Number(tab) || 0))
  updateQuestionPanelState(event, (current) => ({ ...current, tab: nextTab }))
}

const currentQuestionPanelQuestion = (event) => {
  const questions = getQuestionEventQuestions(event)
  return questions[getQuestionPanelTab(event)] || questions[0] || null
}

const currentQuestionPanelAnswers = (event) => {
  const state = ensureQuestionPanelState(event)
  return state?.answers[getQuestionPanelTab(event)] || []
}

const currentQuestionCustomValue = (event) => {
  const state = ensureQuestionPanelState(event)
  return state?.custom[getQuestionPanelTab(event)] || ''
}

const isQuestionCustomEnabled = (event) => {
  const state = ensureQuestionPanelState(event)
  return Boolean(state?.customOn[getQuestionPanelTab(event)])
}

const isQuestionOptionPicked = (event, label) => currentQuestionPanelAnswers(event).includes(label)

const isQuestionPanelSending = (event) => Boolean(ensureQuestionPanelState(event)?.sending)

const questionPanelProgress = (event) => {
  const questions = getQuestionEventQuestions(event)
  return `问题 ${Math.min(getQuestionPanelTab(event) + 1, questions.length)} / ${questions.length}`
}

const isQuestionPanelAnswered = (event, index) => {
  const state = ensureQuestionPanelState(event)
  if (!state) return false
  const answers = state.answers[index] || []
  if (answers.length > 0) return true
  return Boolean(state.customOn[index] && String(state.custom[index] || '').trim())
}

const updateQuestionAnswerBucket = (event, index, nextAnswers) => {
  updateQuestionPanelState(event, (current) => ({
    ...current,
    answers: current.answers.map((item, itemIndex) => itemIndex === index ? nextAnswers : item)
  }))
}

const updateQuestionCustomValue = (event, value) => {
  const index = getQuestionPanelTab(event)
  const question = currentQuestionPanelQuestion(event)
  const text = String(value || '')
  updateQuestionPanelState(event, (current) => {
    const prevCustom = String(current.custom[index] || '').trim()
    const nextCustom = text.trim()
    const answers = current.answers.map((item) => [...item])
    const bucket = answers[index] || []
    if (question?.multiple) {
      const removed = prevCustom ? bucket.filter((item) => item !== prevCustom) : bucket.slice()
      answers[index] = current.customOn[index] && nextCustom
        ? (removed.includes(nextCustom) ? removed : [...removed, nextCustom])
        : removed
    } else if (current.customOn[index]) {
      answers[index] = nextCustom ? [nextCustom] : []
    }
    return {
      ...current,
      answers,
      custom: current.custom.map((item, itemIndex) => itemIndex === index ? text : item)
    }
  })
}

const toggleQuestionCustom = (event) => {
  const index = getQuestionPanelTab(event)
  const question = currentQuestionPanelQuestion(event)
  updateQuestionPanelState(event, (current) => {
    const enabled = !current.customOn[index]
    const custom = String(current.custom[index] || '').trim()
    const answers = current.answers.map((item) => [...item])
    if (question?.multiple) {
      const bucket = answers[index] || []
      answers[index] = enabled
        ? (custom && !bucket.includes(custom) ? [...bucket, custom] : bucket)
        : (custom ? bucket.filter((item) => item !== custom) : bucket)
    } else {
      answers[index] = enabled ? (custom ? [custom] : []) : []
    }
    return {
      ...current,
      answers,
      customOn: current.customOn.map((item, itemIndex) => itemIndex === index ? enabled : item)
    }
  })
}

const toggleQuestionOption = (event, label) => {
  const index = getQuestionPanelTab(event)
  const question = currentQuestionPanelQuestion(event)
  updateQuestionPanelState(event, (current) => {
    const answers = current.answers.map((item) => [...item])
    const bucket = answers[index] || []
    if (question?.multiple) {
      answers[index] = bucket.includes(label)
        ? bucket.filter((item) => item !== label)
        : [...bucket, label]
      return { ...current, answers }
    }
    return {
      ...current,
      answers: current.answers.map((item, itemIndex) => itemIndex === index ? [label] : item),
      customOn: current.customOn.map((item, itemIndex) => itemIndex === index ? false : item)
    }
  })
}

const submitQuestionPanel = async (event) => {
  const state = ensureQuestionPanelState(event)
  if (!state) return false
  updateQuestionPanelState(event, (current) => ({ ...current, sending: true }))
  try {
    const replied = await replyQuestion(event, { reply: 'question_answer', answers: state.answers.map((group) => Array.isArray(group) ? [...group] : []) })
    if (replied) clearQuestionPanelState(event)
    return replied
  } finally {
    if (!event?.replied) {
      updateQuestionPanelState(event, (current) => ({ ...current, sending: false }))
    }
  }
}

const cancelQuestionPanel = async (event) => {
  const state = ensureQuestionPanelState(event)
  if (!state) return false
  updateQuestionPanelState(event, (current) => ({ ...current, sending: true }))
  try {
    const replied = await replyQuestion(event, { reply: 'question_reject' })
    if (replied) clearQuestionPanelState(event)
    return replied
  } finally {
    if (!event?.replied) {
      updateQuestionPanelState(event, (current) => ({ ...current, sending: false }))
    }
  }
}

const permissionReplyLabel = (reply) => {
  const map = {
    once: '允许一次',
    always: '总是允许',
    reject: '拒绝',
    question_reject: '取消/先不回答'
  }
  return map[reply] || reply
}

const replyQuestion = async (event, actionOrReply) => {
  const action = typeof actionOrReply === 'string' ? { reply: actionOrReply } : (actionOrReply || {})
  const requestId = event?.request_id || event?.data?.id || event?.data?.requestID
  const source = eventSourceName(event)
  const sourceLabel = source === 'rakazo' ? 'Rakazo' : source === 'codex' ? 'Codex' : 'OpenCode'
  if (!requestId) {
    ElMessage.error(`缺少${sourceLabel}问题请求 ID`)
    return false
  }
  let payload = {
    request_id: requestId,
    conversation_id: currentConversationId.value,
    project_dir: currentProjectDir.value || null,
    source: event?.source || event?.data?.source || '',
    response_dir: event?.data?.response_dir || '',
  }
  if (action.reply === 'question_reject') {
    payload.reject = true
  } else if (action.custom) {
    try {
      const result = await ElMessageBox.prompt(event?.question || event?.summary || '请输入回答', `回复${sourceLabel}`, {
        confirmButtonText: '发送',
        cancelButtonText: '取消',
        inputType: (event?.questions?.[0]?.input_type || event?.data?.questions?.[0]?.input_type || event?.data?.input_type || 'textarea') === 'password' ? 'password' : 'textarea',
        inputPlaceholder: event?.questions?.[0]?.placeholder || event?.data?.questions?.[0]?.placeholder || event?.data?.placeholder || '输入你的回答'
      })
      const answer = String(result?.value || '').trim()
      if (!answer) {
        ElMessage.warning('回答不能为空')
        return
      }
      payload.answer = answer
    } catch {
      return false
    }
  } else if (Array.isArray(action.answers)) {
    payload.answers = action.answers
  } else if (action.label) {
    payload.answers = [[action.label]]
  } else {
    ElMessage.error('缺少问题回答')
    return false
  }

  event.replying = toolEventActionKey(action)
  const secretReply = (event?.questions?.[0]?.input_type || event?.data?.questions?.[0]?.input_type || event?.data?.input_type || '').toLowerCase() === 'password'
  try {
    await axios.post('/api/chat/question/reply', payload)
    event.replied = true
    event.summary = payload.reject
      ? '你已取消/先不回答'
      : secretReply
        ? '已安全提交敏感回答'
        : `你已选择：${payload.answer || (payload.answers || []).map((group) => (group || []).join(', ')).join('；')}`
    event.actions = []
    clearQuestionPanelState(event)
    if (secretReply) {
      payload.answer = ''
      payload.answers = []
    }
    ElMessage.success(`已回复 ${sourceLabel}`)
    return true
  } catch (error) {
    ElMessage.error(error?.response?.data?.detail || `回复 ${sourceLabel} 问题失败`)
    return false
  } finally {
    event.replying = ''
  }
}

const replyPermission = async (event, actionOrReply) => {
  const action = typeof actionOrReply === 'string' ? { reply: actionOrReply } : (actionOrReply || {})
  const reply = action.reply
  const requestId = event?.request_id || event?.data?.id || event?.data?.requestID || event?.data?.permissionID
  const source = eventSourceName(event)
  const sourceLabel = source === 'rakazo' ? 'Rakazo' : source === 'codex' ? 'Codex' : 'OpenCode'
  if (!requestId) {
    ElMessage.error(`缺少 ${sourceLabel} 权限请求 ID`)
    return
  }
  event.replying = toolEventActionKey(action)
  try {
    await axios.post('/api/chat/permission/reply', {
      request_id: requestId,
      reply,
      message: action.message || null,
      session_id: event?.data?.sessionID || null,
      conversation_id: currentConversationId.value,
      project_dir: currentProjectDir.value || null,
      source,
    })
    event.replied = reply
    event.summary = `你已选择：${permissionReplyLabel(reply)}`
    event.actions = []
    ElMessage.success(`已回复 ${sourceLabel}`)
  } catch (error) {
    ElMessage.error(error?.response?.data?.detail || `回复 ${sourceLabel} 失败`)
  } finally {
    event.replying = ''
  }
}

const replyToolAction = async (event, action) => {
  const reply = action?.reply || ''
  if (reply.startsWith('question_') || event?.event_type?.startsWith('question.')) {
    await replyQuestion(event, action)
    return
  }
  await replyPermission(event, action)
}

const findPendingQuestionEvent = () => {
  if (!currentConversationId.value || !isConversationLoading(currentConversationId.value)) return null
  for (let i = messages.value.length - 1; i >= 0; i -= 1) {
    const msg = messages.value[i]
    const event = msg?.pendingActionEvent || msg?.event
    if (event?.requires_user_action && event?.event_type === 'question.asked' && !event.replied) {
      return event
    }
  }
  return null
}

const clearPendingQuestionEvent = (event) => {
  if (!event) return
  event.replied = true
  event.requires_user_action = false
  event.actions = []
   clearQuestionPanelState(event)
  event.summary = event.summary || '问题请求已失效'
  for (const msg of messages.value) {
    if (msg?.pendingActionEvent === event) msg.pendingActionEvent = null
  }
}

let streamScrollScheduled = false
const scheduleStreamScroll = () => {
  if (streamScrollScheduled) return
  streamScrollScheduled = true
  requestAnimationFrame(() => {
    streamScrollScheduled = false
    scrollToBottom()
  })
}

const streamChatResponse = async (payload, onEvent) => {
  const response = await fetch('/api/chat/send_stream', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  })
  if (!response.ok || !response.body) {
    let message = `HTTP ${response.status}`
    try {
      const body = await response.json()
      message = body?.detail || body?.message || message
    } catch {}
    throw new Error(message)
  }
  const reader = response.body.getReader()
  const decoder = new TextDecoder('utf-8')
  let buffer = ''
  let eventCountSinceYield = 0
  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const lines = buffer.split('\n')
    buffer = lines.pop() || ''
    for (const line of lines) {
      const text = line.trim()
      if (!text) continue
      await onEvent(JSON.parse(text))
      eventCountSinceYield += 1
      if (eventCountSinceYield >= 25) {
        eventCountSinceYield = 0
        await new Promise((resolve) => requestAnimationFrame(resolve))
      }
    }
  }
  if (buffer.trim()) {
    await onEvent(JSON.parse(buffer.trim()))
  }
}

// 发送消息
const sendMessage = async () => {
  if (!inputMessage.value.trim() && attachedFiles.value.length === 0) return
  if (!currentConversationId.value) return

  const conversationId = currentConversationId.value
  const content = inputMessage.value
  const filesToSend = [...attachedFiles.value]
  const targetToSend = chatTarget.value || 'codebot'
  const isCodexTargetToSend = isCodexChatTarget(targetToSend)
  const isRakazoTargetToSend = targetToSend === 'rakazo'
  const knowledgePathsToSend = obsidianEnabled.value
    ? selectedKnowledgeBases.value.map((item) => item.id || item.path).filter(Boolean)
    : []
  const isLoading = isConversationLoading(conversationId)
  const pendingQuestionEvent = filesToSend.length === 0 ? findPendingQuestionEvent() : null

  if (pendingQuestionEvent && content.trim()) {
    if (shouldShowQuestionPanel(pendingQuestionEvent)) {
      ElMessage.warning('请先在问题面板中完成选择后再提交')
      return
    }
    const replied = await replyQuestion(pendingQuestionEvent, { reply: 'question_answer', answers: [[content.trim()]], label: content.trim() })
    if (replied) {
      inputMessage.value = ''
      memoryHints.value = []
      showCommandPanel.value = false
      showAtPanel.value = false
      showKnowledgePanel.value = false
      return
    }
    clearPendingQuestionEvent(pendingQuestionEvent)
    const pendingSource = eventSourceName(pendingQuestionEvent)
    const pendingSourceLabel = pendingSource === 'rakazo' ? 'Rakazo' : pendingSource === 'codex' ? 'Codex' : 'OpenCode'
    ElMessage.warning(`上一个${pendingSourceLabel}问题已失效，已按普通消息发送`)
  }

  inputMessage.value = ''
  attachedFiles.value = []
  memoryHints.value = []
  showCommandPanel.value = false
  showAtPanel.value = false
  showKnowledgePanel.value = false

  // 构建展示用的消息内容（附件 + 文字）
  const displayContent = filesToSend.length > 0
    ? `${filesToSend.map(f => `[附件: ${f.name}]`).join(' ')}\n${content}`.trim()
    : content

  if (currentConversation.value?.conversation_type === 'multi_agent_hub') {
    messages.value.push({
      id: Date.now(),
      role: 'user',
      content: displayContent,
      created_at: new Date().toISOString()
    })
    const assistantMessage = {
      id: Date.now() + 1,
      role: 'assistant',
      content: '正在生成多Agent任务计划...',
      streaming: true,
      created_at: new Date().toISOString()
    }
    messages.value.push(assistantMessage)
    runtimeAssistantIdByConversation.value = { ...runtimeAssistantIdByConversation.value, [conversationId]: assistantMessage.id }
    runtimeSeqByConversation.value = { ...runtimeSeqByConversation.value, [conversationId]: 0 }
    runtimeReloadDoneByConversation.value = { ...runtimeReloadDoneByConversation.value, [conversationId]: false }
    incrementLoading(conversationId)
    await nextTick()
    scrollToBottom(true)
    try {
      await axios.post(`/api/chat/conversations/${conversationId}/messages`, { content: displayContent })
      fetchQueueStatus(conversationId)
      const response = await axios.post(`/api/chat/multi-agent/${conversationId}/dispatch`, {
        message: content,
        model: selectedModel.value || null,
        reasoning_effort: isCodexTargetToSend ? selectedReasoningEffort.value || null : null,
        mode: agentMode.value || 'agent',
        project_dir: currentProjectDir.value || null,
        target: targetToSend,
      })
      assistantMessage.content = response.data?.data?.content || '多Agent任务已完成。'
      assistantMessage.streaming = false
      runtimeReloadDoneByConversation.value = { ...runtimeReloadDoneByConversation.value, [conversationId]: 'pending' }
      await loadMultiAgentMembers()
      await loadConversations()
    } catch (error) {
      assistantMessage.content = '多Agent任务分配失败，请检查成员对话和所选执行器连接。'
      assistantMessage.streaming = false
      ElMessage.error('多Agent任务分配失败')
    } finally {
      decrementLoading(conversationId)
      await nextTick()
      scrollToBottom(true)
    }
    return
  }

  // If already loading, still save locally and send to queue
  if (isLoading) {
    messages.value.push({
      id: Date.now(),
      role: 'user',
      content: displayContent,
      created_at: new Date().toISOString()
    })
    await nextTick()
    scrollToBottom(true)
    try {
      // Save user message to DB
      await axios.post(`/api/chat/conversations/${conversationId}/messages`, { content: displayContent })
      // Send to OpenCode (will be queued on backend)
      const response = await axios.post('/api/chat/send', {
        conversation_id: conversationId,
        message: content,
        model: selectedModel.value || null,
        reasoning_effort: isCodexTargetToSend ? selectedReasoningEffort.value || null : null,
        mode: agentMode.value || null,
        attached_files: filesToSend.length > 0 ? filesToSend : null,
        project_dir: currentProjectDir.value || null,
        target: targetToSend,
        knowledge_paths: knowledgePathsToSend,
        user_already_saved: true,
      })
      if (response.data?.data?.queued) {
        queuedCount.value += 1
        ElMessage.info('消息已加入队列，将在当前任务完成后处理')
      }
    } catch {
      ElMessage.error('发送失败')
    }
    return
  }

  incrementLoading(conversationId)

  let assistantMessage = null
  let flushPendingDelta = () => {}

  try {
    await axios.post(`/api/chat/conversations/${conversationId}/messages`, {
      content: displayContent
    })

    if (currentConversationId.value === conversationId) {
      messages.value.push({
        id: Date.now(),
        role: 'user',
        content: displayContent,
        created_at: new Date().toISOString()
      })
    }

    if (currentConversationId.value === conversationId) {
      await nextTick()
      scrollToBottom(true)
    }

    assistantMessage = {
      id: Date.now() + 1,
      role: 'assistant',
      content: isRakazoTargetToSend ? 'Rakazo 已连接主 Thread，正在处理...' : isCodexTargetToSend ? 'Codex Agent Harness 已启动，正在处理...' : '',
      rawContent: isRakazoTargetToSend ? 'Rakazo 已连接主 Thread，正在处理...' : isCodexTargetToSend ? 'Codex Agent Harness 已启动，正在处理...' : '',
      tool_events: [],
      cli_display: (isCodexTargetToSend || isRakazoTargetToSend) ? false : opencodeCliDisplay.value,
      source: isRakazoTargetToSend ? 'rakazo' : isCodexTargetToSend ? 'codex' : '',
      pendingActionEvent: null,
      streaming: true,
      created_at: new Date().toISOString()
    }
    if (currentConversationId.value === conversationId) {
      messages.value.push(assistantMessage)
    }

    let pendingDelta = ''
    let flushScheduled = false
    flushPendingDelta = () => {
      if (!pendingDelta) return
      // 将新增 delta 追加到累积内容后，再整体清洗一次
      // 整体清洗确保跨 delta 的标签（如 <think> 开始在一个 delta，结束在另一个）能被正确处理
      const raw = `${assistantMessage.rawContent || ''}${pendingDelta}`
      assistantMessage.rawContent = raw
      assistantMessage.content = assistantMessage.cli_display ? raw : sanitizeStreamContent(raw, content)
      pendingDelta = ''
    }

    const scheduleFlush = () => {
      if (flushScheduled) return
      flushScheduled = true
      requestAnimationFrame(() => {
        flushScheduled = false
        flushPendingDelta()
        if (currentConversationId.value === conversationId) scheduleStreamScroll()
      })
    }

    activeStreamByConversation.value = { ...activeStreamByConversation.value, [conversationId]: true }
    try {
      await streamChatResponse({
        conversation_id: conversationId,
        message: content,
        model: selectedModel.value || null,
        reasoning_effort: isCodexTargetToSend ? selectedReasoningEffort.value || null : null,
        mode: agentMode.value || null,
        attached_files: filesToSend.length > 0 ? filesToSend : null,
        project_dir: currentProjectDir.value || null,
        target: targetToSend,
        knowledge_paths: knowledgePathsToSend,
        user_already_saved: true,
      }, async (event) => {
        if (event?.type === 'queued') {
          queuedCount.value += 1
          if (currentConversationId.value === conversationId) {
            messages.value = messages.value.filter((m) => m.id !== assistantMessage.id)
          }
          ElMessage.info('消息已加入队列，将在当前任务完成后处理')
          return
        }
        if (event?.type === 'content_delta') {
          if (event?.cli_display) assistantMessage.cli_display = true
          const delta = event.delta || ''
          if (delta) {
            pendingDelta += delta
            scheduleFlush()
          }
          return
        }
        if (event?.type === 'tool_event' || event?.type === 'meta_event') {
          resolvePendingActionEvents(conversationId, event)
          const eventSource = String(event?.source || event?.data?.source || '').toLowerCase()
          const forceStructuredEvent = ['codex', 'rakazo'].includes(eventSource) && !event?.requires_user_action
          if (shouldUseChatEventPanel(event)) {
            upsertStructuredEventMessage(conversationId, event, assistantMessage)
            scheduleStreamScroll()
            return
          }
          if (!forceStructuredEvent && (event?.cli_inline || assistantMessage.cli_display || opencodeCliDisplay.value)) {
            attachCliActionEvent(assistantMessage, event)
            scheduleStreamScroll()
            return
          }
          if (!shouldShowStructuredEvent(event)) {
            notifyActionRequiredEvent(event)
            return
          }
          if (currentConversationId.value === conversationId) {
            const idx = messages.value.findIndex((m) => m.id === assistantMessage.id)
            const eventMsg = createStructuredEventMessage(conversationId, event)
            if (idx >= 0) {
              messages.value.splice(idx, 0, eventMsg)
            } else {
              messages.value.push(eventMsg)
            }
            // 同步缓存到 perConversationEventMessages，确保切换对话后可以恢复
            cacheStructuredEventMessage(conversationId, eventMsg)
            notifyActionRequiredEvent(event)
            scheduleStreamScroll()
          }
          return
        }
        if (event?.type === 'done') {
          flushPendingDelta()
          // 优先使用后端返回的已清洗内容，否则使用前端实时清洗后的内容
          if (event?.cli_display) assistantMessage.cli_display = true
          if (event?.source) assistantMessage.source = event.source
          const finalContent = event.content || assistantMessage.content
          assistantMessage.content = assistantMessage.cli_display
            ? finalContent
            : sanitizeStreamContent(finalContent, content)
          assistantMessage.streaming = false
          if (currentConversationId.value === conversationId) scheduleStreamScroll()
          return
        }
        if (event?.type === 'error') {
          throw new Error(event.message || event.error || '流式回复失败')
        }
      })
    } finally {
      activeStreamByConversation.value = { ...activeStreamByConversation.value, [conversationId]: false }
      // 标记该对话"等待 reload"，阻止 applyRuntimeEvents 在 reload 前重复注入 events
      runtimeReloadDoneByConversation.value = { ...runtimeReloadDoneByConversation.value, [conversationId]: 'pending' }
    }

    queuedCount.value = Math.max(0, queuedCount.value - 1)
    await loadConversations()
    if (isRakazoTargetToSend) {
      await loadRakazoProjectState()
      if (rakazoDrawerVisible.value) await loadRakazoDrawer()
    }
    // Force-restart title polling after stream ends (loadConversations may have started
    // a poll with default params; cancel it and restart with a longer 30s window to cover
    // slow AI title generation which can take up to 30s on the backend).
    stopConversationTitleRefresh(conversationId)
    scheduleConversationTitleRefresh(conversationId, 20, 1500)

  } catch (error) {
    const msg = error?.message || '发送消息失败'
    if (assistantMessage) {
      flushPendingDelta()
      assistantMessage.content = msg === '发送消息失败' ? msg : `发送消息失败：${msg}`
      assistantMessage.streaming = false
    }
    ElMessage.error(msg === '发送消息失败' ? msg : `发送消息失败：${msg}`)
  } finally {
    decrementLoading(conversationId)
    if (currentConversationId.value === conversationId) {
      await nextTick()
      scrollToBottom(true)
    }
  }
}

// 上传图片（保留兼容，实际已由 triggerFileUpload 替代）
const uploadImage = () => {
  triggerFileUpload()
}

// 生成技能：后端会先走 find-skills 检索，再决定改造现有 skill 还是调用 skill-creator 创建。
const generateSkill = async () => {
  if (!skillGenDescription.value.trim()) {
    ElMessage.warning('请输入技能描述')
    return
  }
  generatingSkill.value = true
  try {
    const response = await axios.post('/api/skills/generate', {
      description: skillGenDescription.value.trim()
    })
    const strategy = response?.data?.data?.strategy
    const strategyLabel = strategy === 'find-skills-adapt'
      ? '已通过 find-skills 检索并改造'
      : '已通过 skill 工作流生成'
    ElMessage.success(response.data.message || `${strategyLabel}，可在技能页面查看`)
    showSkillDialog.value = false
    skillGenDescription.value = ''
  } catch (error) {
    const msg = error?.response?.data?.detail || '生成失败，请重试'
    ElMessage.error(msg)
  } finally {
    generatingSkill.value = false
  }
}

// 撤销消息（删除该条及之后所有消息）
const undoFromMessage = async (msg) => {
  if (!currentConversationId.value) return
  try {
    await ElMessageBox.confirm(
      '确定撤销此消息及之后的所有消息吗？此操作不可恢复。',
      '撤销消息',
      { confirmButtonText: '撤销', cancelButtonText: '取消', type: 'warning' }
    )
    const undoResponse = await axios.post(`/api/chat/conversations/${currentConversationId.value}/undo`, {
      message_id: msg.id,
      conversation_id: currentConversationId.value
    })
    // Reload messages to reflect the deletion
    const response = await axios.get(`/api/chat/conversations/${currentConversationId.value}/messages`)
    messages.value = response.data.data.items || []
    ElMessage.success(undoResponse.data?.data?.project_restored ? '已撤销消息并恢复项目文件' : '已撤销消息')
  } catch (error) {
    if (error !== 'cancel') {
      ElMessage.error('撤销失败：消息可能是本地临时消息，请刷新后重试')
    }
  }
}

const deleteConversation = async (conversationId) => {
  try {
    const conversation = conversations.value.find((item) => Number(item.id) === Number(conversationId))
    const isRakazo = conversation?.executor === 'rakazo'
    const message = isRakazo
      ? [
          '确定删除这个 Rakazo 项目主会话吗？',
          '',
          '将清理：Codebot 对话与消息、Rakazo Bot/Thread/长期记忆、项目 MCP、项目专属 Computer 容器、私有网络和 Computer home。',
          '',
          '不会删除：你的项目目录和文件、Docker Desktop、共享 Rakazo 运行时/Postgres、镜像及其他项目。',
          '',
          '此操作不可恢复。',
        ].join('\n')
      : '确定删除这个对话吗？'
    await ElMessageBox.confirm(message, isRakazo ? '删除并清理 Rakazo 项目' : '删除对话', {
      confirmButtonText: isRakazo ? '删除并清理' : '删除',
      cancelButtonText: '取消',
      type: 'warning',
      customClass: isRakazo ? 'rakazo-delete-confirm' : '',
    })
    const response = await axios.delete(`/api/chat/conversations/${conversationId}`)
    stopConversationTitleRefresh(conversationId)
    if (currentConversationId.value === conversationId) {
      currentConversationId.value = null
      messages.value = []
    }
    await loadConversations()
    ElMessage.success(response.data?.message || (isRakazo ? 'Rakazo 项目已完整清理' : '对话已删除'))
  } catch (error) {
    if (error !== 'cancel') {
      ElMessage.error(error?.response?.data?.detail || '删除对话失败')
    }
  }
}

const clearConversation = async (conversationId) => {
  try {
    await ElMessageBox.confirm('确定清空这个对话的全部消息吗？对话入口会保留。', '清空对话', {
      confirmButtonText: '清空',
      cancelButtonText: '取消',
      type: 'warning'
    })
    await axios.post(`/api/chat/conversations/${conversationId}/clear`, { confirm: true })
    if (currentConversationId.value === conversationId) {
      messages.value = []
    }
    await loadConversations()
    ElMessage.success('对话内容已清空')
  } catch (error) {
    if (error !== 'cancel') {
      ElMessage.error('清空对话失败')
    }
  }
}

const renameConversation = async (conversationId, currentTitle) => {
  try {
    const result = await ElMessageBox.prompt('输入新的对话标题', '重命名', {
      confirmButtonText: '保存',
      cancelButtonText: '取消',
      inputValue: currentTitle || ''
    })
    const newTitle = result.value.trim()
    if (!newTitle) {
      ElMessage.warning('标题不能为空')
      return
    }
    await axios.patch(`/api/chat/conversations/${conversationId}/title`, { title: newTitle })
    stopConversationTitleRefresh(conversationId)
    await loadConversations()
    ElMessage.success('标题已更新')
  } catch (error) {
    if (error !== 'cancel') {
      ElMessage.error('重命名失败')
    }
  }
}

const togglePinConversation = async (conversationId, pinned) => {
  try {
    await axios.post(`/api/chat/conversations/${conversationId}/pin`, { pinned })
    await loadConversations()
    ElMessage.success(pinned ? '已置顶' : '已取消置顶')
  } catch (error) {
    ElMessage.error('更新置顶状态失败')
  }
}

const archiveConversation = async (conversationId) => {
  try {
    await axios.post(`/api/chat/conversations/${conversationId}/archive`, { archived: true })
    stopConversationTitleRefresh(conversationId)
    if (currentConversationId.value === conversationId) {
      currentConversationId.value = null
      messages.value = []
    }
    await loadConversations()
    ElMessage.success('对话已归档')
  } catch (error) {
    ElMessage.error('归档失败')
  }
}

const setGroupConversation = async (conversationId, isGroup) => {
  try {
    let groupRole = null
    if (isGroup) {
      const conv = conversations.value.find((item) => item.id === conversationId)
      const result = await ElMessageBox.prompt('请输入这个 Agent 在群聊中的角色，例如：前端、后端、数据库、测试', '加入多Agent群聊', {
        confirmButtonText: '加入',
        cancelButtonText: '取消',
        inputValue: conv?.group_role || conv?.title || ''
      })
      groupRole = result.value?.trim() || conv?.title || 'Agent'
    }
    await axios.post(`/api/chat/conversations/${conversationId}/group`, { is_group: isGroup, group_role: groupRole })
    await loadConversations()
    await loadMultiAgentMembers()
    ElMessage.success(isGroup ? '已加入多Agent群聊' : '已退出多Agent群聊')
  } catch (error) {
    if (error !== 'cancel') ElMessage.error(isGroup ? '加入多Agent群聊失败' : '退出多Agent群聊失败')
  }
}

const shareConversation = async (conversationId) => {
  try {
    const response = await axios.post(`/api/chat/conversations/${conversationId}/share`)
    const sharePath = response.data.data.share_path
    let baseUrl = window.location.origin
    try {
      const info = await axios.get('/api/network-info')
      baseUrl = info.data?.lan_url || baseUrl
    } catch {}
    const shareUrl = `${baseUrl}${sharePath}`
    await copyToClipboard(shareUrl)
    ElMessage.success('局域网分享链接已复制')
  } catch (error) {
    ElMessage.error('生成分享链接失败')
  }
}

const handleConversationCommand = async (conv, command) => {
  if (command === 'share') { await shareConversation(conv.id); return }
  if (command === 'handoff') { await openHandoffDialog(conv); return }
  if (command === 'group') { await setGroupConversation(conv.id, true); return }
  if (command === 'ungroup') { await setGroupConversation(conv.id, false); return }
  if (command === 'rename') { await renameConversation(conv.id, conv.title); return }
  if (command === 'pin') { await togglePinConversation(conv.id, true); return }
  if (command === 'unpin') { await togglePinConversation(conv.id, false); return }
  if (command === 'archive') { await archiveConversation(conv.id); return }
  if (command === 'clear') { await clearConversation(conv.id); return }
  if (command === 'delete') { await deleteConversation(conv.id) }
}

const onMessageListScroll = () => {
  const el = messageListRef.value
  if (!el) return
  const distance = el.scrollHeight - el.scrollTop - el.clientHeight
  shouldAutoScroll.value = distance <= 80
}

const scrollToBottom = (force = false) => {
  const el = messageListRef.value
  if (!el) return
  if (!force && !shouldAutoScroll.value) return
  el.scrollTop = el.scrollHeight
  shouldAutoScroll.value = true
}

const formatDate = (dateStr) => {
  nowTick.value
  if (!dateStr) return ''
  const raw = String(dateStr).trim()
  let date = null
  if (/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:\.\d+)?$/.test(raw)) {
    date = new Date(raw.replace(' ', 'T') + 'Z')
  } else if (/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?$/.test(raw)) {
    date = new Date(raw + 'Z')
  } else {
    date = new Date(raw)
  }
  if (!(date instanceof Date) || Number.isNaN(date.getTime())) return ''
  const now = new Date()
  const diff = now - date
  if (diff < 0) return '刚刚'
  if (diff < 60000) return '刚刚'
  if (diff < 3600000) return `${Math.floor(diff / 60000)}分钟前`
  if (diff < 86400000) return `${Math.floor(diff / 3600000)}小时前`
  return date.toLocaleDateString('zh-CN')
}

onMounted(() => {
  loadConversations(true)
  // 非首屏关键数据并行加载，避免从其他页面返回聊天时串行等待。
  Promise.allSettled([loadModels(), loadCommands(), loadThirdPartyStatus(), loadLinkOpenMode()])
  queueStatusTimer = setInterval(() => {
    if (currentConversationId.value) {
      fetchQueueStatus(currentConversationId.value, { reloadOnFinish: true })
    }
  }, 2000)
  timeRefreshTimer = setInterval(() => {
    nowTick.value = Date.now()
  }, 30000)
  thirdPartyStatusTimer = setInterval(() => {
    loadThirdPartyStatus()
  }, 10000)

  // 拦截消息列表中的链接点击，使用系统浏览器打开外部链接
  if (messageListRef.value) {
    messageListRef.value.addEventListener('click', handleLinkClick)
  }
})

// 处理消息区域中的链接点击
const linkOpenMode = ref('system')

// 加载链接打开方式配置，并同步通知 Electron 主进程
const loadLinkOpenMode = async () => {
  try {
    const res = await fetch('/api/config/general')
    if (res.ok) {
      const json = await res.json()
      if (json?.success && json?.data?.link_open_mode) {
        linkOpenMode.value = json.data.link_open_mode
      }
      if (json?.success && typeof json?.data?.opencode_cli_display === 'boolean') {
        opencodeCliDisplay.value = json.data.opencode_cli_display !== false
      }
      if (json?.success && typeof json?.data?.compact_mode === 'boolean') {
        compactMode.value = Boolean(json.data.compact_mode)
      }
      if (json?.success && typeof json?.data?.chat_default_model === 'string') {
        const backendModel = json.data.chat_default_model
        if (backendModel && !selectedModel.value) {
          selectedModel.value = backendModel
        }
      }
    }
  } catch {}
  // 同步给 Electron 主进程，使 will-navigate / setWindowOpenHandler 也生效
  if (window.electronAPI?.setLinkOpenMode) {
    window.electronAPI.setLinkOpenMode(linkOpenMode.value)
  }
}

const handleLinkClick = (e) => {
  const link = e.target.closest('a[href]')
  if (!link) return
  const href = link.getAttribute('href')
  if (!href) return
  // 跳过锚点链接和 javascript:
  if (href.startsWith('#') || href.startsWith('javascript:')) return

  e.preventDefault()
  e.stopPropagation()

  if (linkOpenMode.value === 'builtin') {
    // 内置浏览器：在 Electron 应用内新窗口中打开
    if (window.electronAPI?.openBuiltin) {
      window.electronAPI.openBuiltin(href)
    } else {
      // 非 Electron 环境（浏览器访问时）退化为新标签页
      window.open(href, '_blank', 'noopener,noreferrer')
    }
  } else {
    // 系统浏览器：调用系统默认浏览器打开
    if (window.electronAPI?.openExternal) {
      window.electronAPI.openExternal(href)
    } else {
      window.open(href, '_blank', 'noopener,noreferrer')
    }
  }
}

onUnmounted(() => {
  if (queueStatusTimer) {
    clearInterval(queueStatusTimer)
    queueStatusTimer = null
  }
  if (timeRefreshTimer) {
    clearInterval(timeRefreshTimer)
    timeRefreshTimer = null
  }
  if (thirdPartyStatusTimer) {
    clearInterval(thirdPartyStatusTimer)
    thirdPartyStatusTimer = null
  }
  // 清除链接点击拦截
  if (messageListRef.value) {
    messageListRef.value.removeEventListener('click', handleLinkClick)
  }
  for (const timer of conversationTitleRefreshTimers.values()) {
    clearTimeout(timer)
  }
  conversationTitleRefreshTimers.clear()
})
</script>

<style scoped>
.chat-container {
  height: 100%;
  overflow: hidden;
}

.el-container {
  height: 100%;
  overflow: hidden;
}

.el-main {
  height: 100%;
  padding: 0;
  overflow: hidden;
}

.el-aside {
  background: #fff;
  border-right: 1px solid #e4e7ed;
  display: flex;
  flex-direction: column;
}

.conversation-header {
  padding: 16px;
  border-bottom: 1px solid #e4e7ed;
  display: flex;
  gap: 8px;
  align-items: center;
}

.new-conv-btn {
  flex: 1;
}

.batch-btn {
  flex-shrink: 0;
}

.batch-toolbar {
  padding: 8px 12px;
  background: #fdf6ec;
  border-bottom: 1px solid #faecd8;
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 13px;
}

.batch-actions {
  display: flex;
  gap: 6px;
}

.batch-checkbox {
  flex-shrink: 0;
  margin-right: 4px;
}

.conversation-item.batch-selected {
  background: #ecf5ff;
  border: 1px solid #b3d8ff;
}

.conversation-search {
  padding: 8px 12px;
  border-bottom: 1px solid #e4e7ed;
}

.multi-agent-entry {
  margin: 8px 12px 0;
  border: 1px solid #dcdfe6;
  border-radius: 8px;
  overflow: hidden;
  background: #f8fafc;
}

.multi-agent-entry-toggle,
.multi-agent-hub-link {
  width: 100%;
  border: 0;
  background: transparent;
  color: #303133;
  cursor: pointer;
  font: inherit;
  text-align: left;
}

.multi-agent-entry-toggle {
  min-height: 36px;
  padding: 8px 10px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-weight: 600;
}

.multi-agent-entry-meta {
  color: #909399;
  font-size: 12px;
  font-weight: 400;
}

.multi-agent-entry-body {
  padding: 0 6px 6px;
}

.multi-agent-hub-link {
  padding: 8px;
  border-radius: 6px;
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.multi-agent-hub-link:hover,
.multi-agent-hub-link.active {
  background: #ecf5ff;
  color: #409eff;
}

.multi-agent-hub-title {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.conversation-list {
  flex: 1;
  overflow-y: auto;
  padding: 8px;
}

.conversation-item {
  padding: 12px;
  border-radius: 8px;
  cursor: pointer;
  margin-bottom: 8px;
  transition: all 0.3s;
  display: flex;
  align-items: center;
  gap: 8px;
}

.conversation-item:hover {
  background: #f5f7fa;
}

.conversation-item.active {
  background: #ecf5ff;
  color: #409EFF;
}

.conversation-item.multi-agent-hub-item {
  position: sticky;
  top: 0;
  z-index: 2;
  background: linear-gradient(135deg, #1f2937, #4338ca);
  color: #fff;
  box-shadow: 0 8px 18px rgba(67, 56, 202, 0.22);
}

.conversation-item.multi-agent-hub-item .conversation-time {
  color: rgba(255, 255, 255, 0.72);
}

.conversation-item.multi-agent-hub-item.active {
  background: linear-gradient(135deg, #111827, #2563eb);
}

.conversation-info {
  flex: 1;
  min-width: 0;
}

.conversation-actions {
  flex-shrink: 0;
  opacity: 0;
  transition: opacity 0.2s;
}

.conversation-item:hover .conversation-actions {
  opacity: 1;
}

.conversation-title {
  font-weight: 500;
  margin-bottom: 4px;
  display: flex;
  align-items: center;
  gap: 6px;
}

.conversation-title-text {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.conversation-time {
  font-size: 12px;
  color: #909399;
}

.chat-main {
  display: flex;
  flex-direction: column;
  height: 100%;
}

.third-party-banner {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 16px;
  background: #f4f8ff;
  border-bottom: 1px solid #e4ecff;
  color: #3f5873;
  font-size: 13px;
}

.multi-agent-members {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  padding: 10px 16px;
  background: #f8fafc;
  border-bottom: 1px solid #e5e7eb;
}

.refresh-members-btn {
  margin-left: auto;
}

.empty-members {
  font-size: 12px;
  color: #909399;
}

.message-list {
  flex: 1;
  overflow-y: auto;
  padding: 20px;
}

.compact-mode .message-list {
  padding: 10px;
}

.message {
  display: flex;
  gap: 12px;
  margin-bottom: 20px;
}

.compact-mode .message {
  gap: 8px;
  margin-bottom: 8px;
}

.message.user {
  flex-direction: row-reverse;
}

.message-content {
  max-width: 70%;
}

.compact-mode .message-avatar {
  display: none;
}

.compact-mode .message-content {
  max-width: 86%;
}

.message.user .message-content {
  background: #409EFF;
  color: #fff;
  padding: 12px 16px;
  border-radius: 12px 12px 0 12px;
}

.message.assistant .message-content {
  background: #f5f7fa;
  padding: 12px 16px;
  border-radius: 12px 12px 12px 0;
}

.message-source-badge {
  display: inline-flex;
  align-items: center;
  width: fit-content;
  margin-bottom: 8px;
  padding: 2px 7px;
  border-radius: 4px;
  background: #e8f4ff;
  color: #1d4f7a;
  font-size: 12px;
  line-height: 18px;
  font-weight: 600;
}

.compact-mode .message.user .message-content,
.compact-mode .message.assistant .message-content {
  padding: 8px 10px;
  border-radius: 8px;
}

.message.assistant.cli-message .message-content {
  width: min(980px, calc(100% - 52px));
  max-width: min(980px, calc(100% - 52px));
  background: #0f1115;
  color: #8f949d;
  border-radius: 8px;
  padding: 16px 18px;
}

.compact-mode .message.assistant.cli-message .message-content {
  width: min(980px, calc(100% - 20px));
  max-width: min(980px, calc(100% - 20px));
  padding: 10px 12px;
}

.message-text {
  word-break: break-word;
  white-space: pre-wrap;
}

.cli-output {
  color: #8f949d;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace;
  font-size: 14px;
  font-weight: 700;
  line-height: 1.45;
  letter-spacing: 0;
  white-space: pre-wrap;
  word-break: break-word;
}

.cli-action-bar {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px solid rgba(255, 255, 255, 0.1);
}

.question-panel-host {
  margin-top: 12px;
}

.structured-question-panel-host {
  width: min(760px, calc(100vw - 180px));
  min-width: min(520px, calc(100vw - 180px));
}

.question-panel {
  background: #ffffff;
  border: 1px solid #dcdfe6;
  border-radius: 8px;
  color: #303133;
  padding: 14px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.question-panel-header,
.question-panel-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.question-panel-title {
  font-size: 12px;
  color: #409eff;
  font-weight: 600;
}

.question-panel-progress {
  display: flex;
  gap: 6px;
}

.question-progress-dot {
  width: 34px;
  height: 6px;
  border: 0;
  border-radius: 999px;
  background: #dcdfe6;
  cursor: pointer;
}

.question-progress-dot.active {
  background: #409eff;
}

.question-progress-dot.answered {
  box-shadow: inset 0 0 0 1px #409eff;
}

.question-panel-body {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.question-panel-question {
  font-size: 15px;
  font-weight: 600;
  color: #303133;
}

.question-panel-hint {
  font-size: 12px;
  color: #909399;
}

.question-panel-options {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.question-option {
  width: 100%;
  border: 1px solid #dcdfe6;
  background: #ffffff;
  color: #303133;
  border-radius: 8px;
  padding: 12px;
  display: flex;
  align-items: flex-start;
  gap: 10px;
  text-align: left;
  cursor: pointer;
}

.question-option:hover,
.question-option.picked {
  border-color: #409eff;
  background: #ecf5ff;
}

.question-option-mark {
  width: 18px;
  height: 18px;
  border-radius: 50%;
  border: 2px solid #909399;
  margin-top: 1px;
  flex: 0 0 auto;
  position: relative;
}

.question-option-mark.multi {
  border-radius: 6px;
}

.question-option-mark.picked {
  border-color: #409eff;
  background: #409eff;
}

.question-option-mark.picked::after {
  content: '';
  position: absolute;
  inset: 4px;
  border-radius: inherit;
  background: #fff;
}

.question-option-main {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}

.question-option-label {
  font-size: 14px;
  font-weight: 600;
  color: #303133;
}

.question-option-description {
  font-size: 12px;
  color: #606266;
  line-height: 1.5;
}

.question-panel-footer-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.question-panel :deep(.el-textarea__inner) {
  background: #ffffff;
  border-color: #dcdfe6;
  color: #303133;
}

.question-panel :deep(.el-button:not(.el-button--primary)) {
  background: #ffffff;
  border-color: #dcdfe6;
  color: #606266;
}

.tool-events {
  display: none;
}

.event-message {
  padding: 2px 16px 2px 52px;
}

.compact-mode .event-message {
  padding: 1px 10px;
}

.tool-event-item {
  display: inline-flex;
  flex-direction: column;
  gap: 6px;
  align-items: stretch;
  font-size: 12px;
  color: #606266;
  background: #f5f7fa;
  border: 1px solid #ebeef5;
  border-radius: 6px;
  padding: 4px 10px;
  max-width: 100%;
}

.compact-mode .tool-event-item {
  padding: 3px 8px;
}

.tool-event-item.codex-tool {
  background: #eef6ff;
  border-color: #c6e2ff;
}

.tool-event-item.codex-trace {
  background: #f6f8fa;
  border-color: #e5e7eb;
}

.tool-event-item.codex-status,
.tool-event-item.codex-idle {
  background: #fff8e8;
  border-color: #f3d19e;
}

.tool-event-header {
  display: flex;
  gap: 8px;
  align-items: center;
  min-width: 0;
  cursor: pointer;
}

.tool-event-type {
  color: #409eff;
  font-weight: 600;
  white-space: nowrap;
}

.tool-event-summary {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tool-event-toggle {
  color: #909399;
  font-size: 12px;
  white-space: nowrap;
}

.tool-event-detail {
  color: #303133;
  max-width: min(820px, 100%);
  max-height: 360px;
  overflow: auto;
  padding-top: 4px;
  border-top: 1px solid #ebeef5;
}

.tool-event-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  padding-top: 2px;
}

/* Markdown Styles */
.markdown-body {
  font-size: 14px;
  line-height: 1.6;
  white-space: normal;
}

.markdown-body :deep(pre) {
  background-color: #282c34;
  color: #abb2bf;
  border-radius: 6px;
  padding: 12px;
  overflow-x: auto;
  margin: 8px 0;
}

.markdown-body :deep(code) {
  font-family: Consolas, Monaco, 'Andale Mono', 'Ubuntu Mono', monospace;
  background-color: rgba(175, 184, 193, 0.2);
  padding: 0.2em 0.4em;
  border-radius: 4px;
  font-size: 85%;
}

.markdown-body :deep(pre code) {
  background-color: transparent;
  padding: 0;
  border-radius: 0;
  color: inherit;
  font-size: 100%;
}

.markdown-body :deep(p) {
  margin: 0.5em 0;
}

.markdown-body :deep(p:first-child) {
  margin-top: 0;
}

.markdown-body :deep(p:last-child) {
  margin-bottom: 0;
}

.markdown-body :deep(ul), .markdown-body :deep(ol) {
  padding-left: 20px;
  margin: 0.5em 0;
}

.markdown-body :deep(blockquote) {
  margin: 0.5em 0;
  padding-left: 1em;
  border-left: 4px solid #dfe2e5;
  color: #6a737d;
}

.markdown-body :deep(table) {
  border-collapse: collapse;
  width: 100%;
  margin: 0.5em 0;
}

.markdown-body :deep(th), .markdown-body :deep(td) {
  border: 1px solid #dfe2e5;
  padding: 6px 13px;
}

.markdown-body :deep(th) {
  background-color: #f6f8fa;
  font-weight: 600;
}

.markdown-body :deep(img) {
  max-width: 100%;
  border-radius: 4px;
}

.message-footer {
  display: flex;
  justify-content: flex-end;
  align-items: center;
  margin-top: 4px;
  height: 20px;
  gap: 8px;
}

.plan-options {
  margin-top: 12px;
  padding: 10px 14px;
  background: #f0f7ff;
  border-radius: 8px;
  border: 1px solid #d4e5f7;
}

.plan-options-label {
  font-size: 12px;
  color: #606266;
  margin-bottom: 8px;
  font-weight: 500;
}

.plan-options-btns {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.plan-options-btns .el-button {
  font-size: 13px;
}

.message-actions {
  display: flex;
  align-items: center;
  gap: 2px;
  margin-left: auto;
}

.copy-btn {
  opacity: 0;
  transition: opacity 0.2s;
  padding: 0 4px;
  color: inherit;
}

.message-content:hover .copy-btn {
  opacity: 1;
}

.copy-btn:hover {
  color: #409EFF;
}

.message.user .copy-btn:hover {
  color: rgba(255, 255, 255, 0.8);
}

.message-time {
  font-size: 12px;
  color: #909399;
  margin-right: auto;
}

.message.user .message-time {
  color: rgba(255, 255, 255, 0.8);
}

.message-input {
  border-top: 1px solid #e4e7ed;
  padding: 10px 16px 16px;
  background: #fff;
  position: relative;
  transition: border-color 0.2s;
}

.message-input.drag-over {
  border-color: #409EFF;
  background: #f0f7ff;
}

/* 拖拽遮罩 */
.drag-overlay {
  position: absolute;
  inset: 0;
  background: rgba(64, 158, 255, 0.08);
  border: 2px dashed #409EFF;
  border-radius: 8px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  z-index: 10;
  pointer-events: none;
  font-size: 14px;
  color: #409EFF;
  font-weight: 500;
}
.drag-icon {
  font-size: 32px;
}

/* 附件预览区 */
.attached-files {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 8px;
}
.attached-file-chip {
  display: flex;
  align-items: center;
  gap: 4px;
  background: #ecf5ff;
  border: 1px solid #b3d8ff;
  border-radius: 14px;
  padding: 2px 10px 2px 8px;
  font-size: 12px;
  color: #409EFF;
  max-width: 220px;
  cursor: default;
}
.file-chip-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 150px;
}
.file-chip-icon {
  flex-shrink: 0;
  font-size: 14px;
}
.file-chip-remove {
  flex-shrink: 0;
  cursor: pointer;
  font-size: 13px;
  margin-left: 2px;
  opacity: 0.7;
}
.file-chip-remove:hover {
  opacity: 1;
  color: #f56c6c;
}

/* 命令面板 & @ 面板 */
.command-panel {
  position: absolute;
  bottom: calc(100% + 4px);
  left: 16px;
  right: 16px;
  background: #fff;
  border: 1px solid #e4e7ed;
  border-radius: 8px;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.12);
  z-index: 100;
  overflow: hidden;
  max-height: 300px;
  display: flex;
  flex-direction: column;
}
.at-panel {
  right: auto;
  min-width: 340px;
}
.command-panel-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 6px 12px;
  background: #f5f7fa;
  border-bottom: 1px solid #e4e7ed;
  font-size: 12px;
  color: #606266;
  font-weight: 500;
}
.command-search-hint {
  font-weight: normal;
  color: #909399;
  font-family: monospace;
}
.command-panel-list {
  overflow-y: auto;
  max-height: 240px;
}
.command-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 7px 12px;
  cursor: pointer;
  transition: background 0.15s;
}
.command-item:hover,
.command-item.active {
  background: #ecf5ff;
}
.command-label {
  font-size: 13px;
  font-weight: 500;
  color: #303133;
  min-width: 120px;
  white-space: nowrap;
}
.command-desc {
  font-size: 12px;
  color: #909399;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.command-file-icon {
  color: #909399;
  flex-shrink: 0;
}
.command-empty {
  padding: 12px;
  text-align: center;
  color: #c0c4cc;
  font-size: 13px;
}

/* 工具栏 */
.input-toolbar {
  display: flex;
  align-items: center;
  margin-bottom: 8px;
  gap: 0;
}

.toolbar-left {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.toolbar-label {
  font-size: 12px;
  color: #909399;
  white-space: nowrap;
  flex-shrink: 0;
}

.agent-mode-select {
  width: 90px;
  flex-shrink: 0;
}

:deep(.agent-mode-popper .el-select-dropdown__item) {
  display: flex;
  align-items: center;
  gap: 8px;
}

:deep(.agent-option-name) {
  font-size: 13px;
  font-weight: 500;
  color: #303133;
  min-width: 40px;
}

:deep(.agent-option-desc) {
  font-size: 11px;
  color: #909399;
}

.model-select {
  width: 220px;
}

:deep(.model-option-name) {
  font-size: 13px;
  color: #303133;
}

:deep(.model-option-provider) {
  font-size: 11px;
  color: #909399;
  margin-left: 8px;
}

:deep(.model-select-popper .el-select-dropdown__item) {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

:deep(.model-select-popper) {
  width: 320px !important;
  max-height: 400px;
}

.refresh-btn {
  padding: 4px 6px;
  color: #909399;
}

.refresh-btn:hover {
  color: #409EFF;
}

/* 操作按钮行 */
.input-actions {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-top: 10px;
}

.input-actions-left {
  display: flex;
  gap: 8px;
  align-items: center;
}

.input-actions-right {
  display: flex;
  gap: 8px;
  align-items: center;
}

.abort-btn {
  flex-shrink: 0;
}

.queue-indicator {
  margin-left: 4px;
}

.undo-btn {
  opacity: 0;
  transition: opacity 0.2s;
  padding: 0 4px;
  color: inherit;
}

.message-content:hover .undo-btn {
  opacity: 1;
}

.undo-btn:hover {
  color: #f56c6c;
}

.message.user .undo-btn:hover {
  color: rgba(255, 255, 255, 0.8);
}

/* ── 记忆提示区 ── */
.memory-hints {
  border-top: 1px solid #ebeef5;
  background: #fafbff;
  padding: 6px 16px;
  display: flex;
  align-items: flex-start;
  gap: 8px;
  flex-wrap: wrap;
}

.memory-hints-label {
  font-size: 12px;
  color: #909399;
  white-space: nowrap;
  padding-top: 2px;
  flex-shrink: 0;
}

.memory-hints-list {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.memory-hint-tag {
  max-width: 220px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  cursor: default;
}

.hint-cat {
  font-weight: 600;
  margin-right: 4px;
  opacity: 0.75;
}

.hint-cat::after {
  content: '·';
  margin-left: 3px;
}

.rakazo-session-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 10px 16px;
  border-bottom: 1px solid var(--el-border-color-lighter);
  background: linear-gradient(90deg, rgba(99, 102, 241, 0.1), rgba(139, 92, 246, 0.04));
}

.rakazo-session-identity {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.rakazo-session-controls {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
  min-width: 0;
  flex: 1;
}

.rakazo-model-label {
  color: var(--el-text-color-secondary);
  font-size: 12px;
  flex: 0 0 auto;
}

.rakazo-model-select {
  width: min(430px, 48vw);
  min-width: 220px;
}
.rakazo-route-alert { margin: 0 16px 10px; }

.rakazo-source-badge { color: #6d5bd0; }

.executor-picker {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
  margin-bottom: 20px;
}
.handoff-executor-picker { grid-template-columns: repeat(2, minmax(0, 1fr)); margin-top: 18px; }

/* 交接摘要可能较长。弹窗必须始终留在当前视口内，让正文区域滚动，
 * 避免在小屏桌面窗口中把“确认并交接”按钮挤到屏幕之外。 */
:deep(.handoff-dialog) {
  display: flex;
  max-height: calc(100vh - 40px);
  margin: 20px auto !important;
  flex-direction: column;
}
:deep(.handoff-dialog .el-dialog__header),
:deep(.handoff-dialog .el-dialog__footer) { flex: 0 0 auto; }
:deep(.handoff-dialog .el-dialog__body) {
  min-height: 0;
  overflow-y: auto;
}

.executor-card {
  display: flex;
  min-height: 112px;
  flex-direction: column;
  gap: 8px;
  padding: 16px;
  text-align: left;
  color: var(--el-text-color-primary);
  background: var(--el-bg-color);
  border: 1px solid var(--el-border-color);
  border-radius: 10px;
  cursor: pointer;
  transition: border-color 0.18s, box-shadow 0.18s, transform 0.18s;
}

.executor-card:hover { border-color: var(--el-color-primary-light-3); transform: translateY(-1px); }
.executor-card.active { border-color: var(--el-color-primary); box-shadow: 0 0 0 2px var(--el-color-primary-light-8); }
.executor-card-title { font-size: 16px; font-weight: 650; }
.executor-card-desc { color: var(--el-text-color-secondary); font-size: 12px; line-height: 1.55; }
.new-conversation-form { margin-top: 4px; }
.field-tip { width: 100%; margin-top: 5px; color: var(--el-text-color-secondary); font-size: 12px; line-height: 1.5; }

.rakazo-drawer-content { min-height: 420px; }
.drawer-json, .drawer-log-detail {
  padding: 10px;
  overflow: auto;
  color: var(--el-text-color-regular);
  background: var(--el-fill-color-light);
  border-radius: 8px;
  white-space: pre-wrap;
  word-break: break-all;
}
.drawer-json { max-height: 220px; }
.drawer-log-detail { max-height: 140px; margin: 6px 0 0; font-size: 11px; }
.drawer-actions { display: flex; gap: 8px; margin-top: 10px; }
.permission-note { margin-left: 8px; color: var(--el-text-color-secondary); font-size: 12px; }

@media (max-width: 760px) {
  .executor-picker { grid-template-columns: 1fr; }
  .rakazo-session-bar { align-items: stretch; flex-direction: column; }
  .rakazo-session-identity { flex-wrap: wrap; }
  .rakazo-session-controls { justify-content: flex-start; width: 100%; }
  .rakazo-model-select { width: 100%; min-width: 0; }
  :deep(.handoff-dialog) { width: calc(100vw - 24px) !important; }
}
</style>
