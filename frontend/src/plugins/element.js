// 仅注册实际使用的组件；保留全局模板用法，让构建器移除未使用的组件和图标。
import {
  ElAlert, ElAside, ElAvatar, ElBadge, ElButton,
  ElCard, ElCheckbox, ElCheckboxGroup, ElCol, ElCollapse,
  ElCollapseItem, ElCollapseTransition, ElContainer, ElDescriptions, ElDescriptionsItem,
  ElDialog, ElDivider, ElDrawer, ElDropdown, ElDropdownItem,
  ElDropdownMenu, ElEmpty, ElForm, ElFormItem, ElHeader,
  ElIcon, ElInput, ElInputNumber, ElMain, ElMenu,
  ElMenuItem, ElOption, ElOptionGroup, ElPagination, ElPopover,
  ElProgress, ElRadio, ElRadioButton, ElRadioGroup, ElRow,
  ElSelect, ElSkeleton, ElSlider, ElSwitch, ElTabPane,
  ElTable, ElTableColumn, ElTabs, ElTag, ElText,
  ElTimePicker, ElTimeline, ElTimelineItem, ElTooltip, ElUpload,
  ElLoading, ElMessage, ElMessageBox, ElNotification
} from 'element-plus'
import {
  ChatDotRound, CircleCheck, CircleClose, Clock, Close, Connection,
  CopyDocument, Delete, Document, Download, Edit, Folder,
  FolderOpened, Grid, InfoFilled, Link, Loading, MagicStick,
  Monitor, MoreFilled, Plus, Promotion, QuestionFilled, Refresh,
  Search, Setting, TopRight, Upload, User, VideoPause,
  View, Warning
} from '@element-plus/icons-vue'

const components = {
  ElAlert, ElAside, ElAvatar, ElBadge, ElButton,
  ElCard, ElCheckbox, ElCheckboxGroup, ElCol, ElCollapse,
  ElCollapseItem, ElCollapseTransition, ElContainer, ElDescriptions, ElDescriptionsItem,
  ElDialog, ElDivider, ElDrawer, ElDropdown, ElDropdownItem,
  ElDropdownMenu, ElEmpty, ElForm, ElFormItem, ElHeader,
  ElIcon, ElInput, ElInputNumber, ElMain, ElMenu,
  ElMenuItem, ElOption, ElOptionGroup, ElPagination, ElPopover,
  ElProgress, ElRadio, ElRadioButton, ElRadioGroup, ElRow,
  ElSelect, ElSkeleton, ElSlider, ElSwitch, ElTabPane,
  ElTable, ElTableColumn, ElTabs, ElTag, ElText,
  ElTimePicker, ElTimeline, ElTimelineItem, ElTooltip, ElUpload,
  ElLoading, ElMessage, ElMessageBox, ElNotification
}
const icons = {
  ChatDotRound, CircleCheck, CircleClose, Clock, Close, Connection,
  CopyDocument, Delete, Document, Download, Edit, Folder,
  FolderOpened, Grid, InfoFilled, Link, Loading, MagicStick,
  Monitor, MoreFilled, Plus, Promotion, QuestionFilled, Refresh,
  Search, Setting, TopRight, Upload, User, VideoPause,
  View, Warning
}

export default {
  install(app) {
    for (const component of Object.values(components)) app.use(component)
    for (const [name, icon] of Object.entries(icons)) app.component(name, icon)
  }
}
