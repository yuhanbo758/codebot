'use strict';

/**
 * 编排 Rakazo 桌面运行环境的四个启动步骤。
 *
 * 该模块不直接依赖 Electron，所有系统与后端动作都通过 dependencies 注入，便于
 * 单元测试确认“一键启动”不会偷偷安装 Docker，也不会在缺少已安装运行时或模型时
 * 伪造成功。真正的凭据始终留在 Electron safeStorage 与后端进程中，本函数只接收
 * “是否已存储”这类布尔状态。
 */
async function startRakazoDesktopStack(options = {}, onProgress, dependencies = {}) {
  const required = [
    'getDockerStatus',
    'startDocker',
    'getRuntimeStatus',
    'startRuntime',
    'getAuthorizationStatus',
    'restoreAuthorization',
    'bootstrapAuthorization',
  ];
  for (const name of required) {
    if (typeof dependencies[name] !== 'function') {
      throw new Error(`Rakazo 一键启动缺少受控依赖：${name}`);
    }
  }

  const emit = (phase, percent, message) => onProgress?.({ phase, percent, message });
  const storageRoot = String(options.storageRoot || '').trim();
  const dockerOptions = storageRoot ? { storageRoot } : {};
  const createAuthorization = options.createAuthorization === true;

  emit('checking', 2, '正在检查 Rakazo 四项运行环境');
  let docker = await dependencies.getDockerStatus(dockerOptions);
  if (!docker?.installed) {
    throw new Error('尚未安装 Docker Desktop，请先完成一次受控安装；一键启动不会静默安装系统组件');
  }

  if (!docker.ready) {
    emit('docker', 12, '正在启动已有 Docker Desktop Engine');
    await dependencies.startDocker(dockerOptions, (progress = {}) => {
      emit('docker', Math.max(12, Math.min(38, Number(progress.percent || 0) / 4)), progress.message || '正在启动 Docker Desktop');
    });
    docker = await dependencies.getDockerStatus(dockerOptions);
  }
  if (!docker?.ready) throw new Error('Docker Desktop 已启动，但 Engine 验证未通过');

  emit('models', 42, '正在确认 OpenCode 当前可用模型连接');
  let runtime = await dependencies.getRuntimeStatus();
  if (Number(runtime?.selectableModelCount || 0) < 1) {
    throw new Error('OpenCode 当前没有可供 Rakazo 调用的模型，请先在 OpenCode 中连接至少一个模型');
  }

  if (!(runtime?.connected && runtime?.compatibility?.compatible)) {
    emit('runtime', 58, '正在启动 Rakazo 受管运行时');
    await dependencies.startRuntime();
    runtime = await dependencies.getRuntimeStatus();
  }
  if (!(runtime?.connected && runtime?.compatibility?.compatible)) {
    throw new Error('Rakazo 运行时启动后未通过合同与镜像身份校验');
  }

  if (!runtime.sessionAuthorized) {
    emit('authorization', 78, '正在恢复 Rakazo 本机加密授权');
    const authorization = await dependencies.getAuthorizationStatus();
    if (authorization?.stored) {
      await dependencies.restoreAuthorization();
    } else if (createAuthorization) {
      emit('authorization', 82, '正在创建并安全托管 Rakazo 本机账号');
      await dependencies.bootstrapAuthorization();
    } else {
      throw new Error('未找到已加密保存的 Rakazo 本机授权，请在设置页点击一次“一键启动全部”完成授权');
    }
    runtime = await dependencies.getRuntimeStatus();
  }

  emit('verifying', 94, '正在复核四项运行状态');
  docker = await dependencies.getDockerStatus(dockerOptions);
  runtime = await dependencies.getRuntimeStatus();
  const missing = [];
  if (!docker?.ready) missing.push('Docker Desktop');
  if (Number(runtime?.selectableModelCount || 0) < 1) missing.push('OpenCode 模型连接');
  if (!(runtime?.connected && runtime?.compatibility?.compatible)) missing.push('Rakazo 运行时');
  if (!runtime?.sessionAuthorized) missing.push('本机授权');
  if (missing.length) throw new Error(`Rakazo 一键启动后仍有未就绪项目：${missing.join('、')}`);

  emit('done', 100, 'Docker、模型连接、Rakazo 运行时和本机授权均已就绪');
  return {
    success: true,
    dockerReady: true,
    modelsReady: true,
    runtimeReady: true,
    authorizationReady: true,
    runtimeVersion: String(runtime.runtimeVersion || ''),
    selectableModelCount: Number(runtime.selectableModelCount || 0),
  };
}

module.exports = { startRakazoDesktopStack };
