const test = require('node:test');
const assert = require('node:assert/strict');

const { startRakazoDesktopStack } = require('../rakazo-startup');

function createHarness(overrides = {}) {
  const calls = [];
  const state = {
    docker: { installed: true, ready: true },
    runtime: {
      connected: true,
      compatibility: { compatible: true },
      sessionAuthorized: true,
      selectableModelCount: 3,
      runtimeVersion: 'v0.1.0',
    },
    authorization: { stored: true, encryptionAvailable: true },
  };
  const dependencies = {
    getDockerStatus: async () => ({ ...state.docker }),
    startDocker: async () => {
      calls.push('startDocker');
      state.docker.ready = true;
    },
    getRuntimeStatus: async () => ({ ...state.runtime, compatibility: { ...state.runtime.compatibility } }),
    startRuntime: async () => {
      calls.push('startRuntime');
      state.runtime.connected = true;
      state.runtime.compatibility.compatible = true;
    },
    getAuthorizationStatus: async () => ({ ...state.authorization }),
    restoreAuthorization: async () => {
      calls.push('restoreAuthorization');
      state.runtime.sessionAuthorized = true;
    },
    bootstrapAuthorization: async () => {
      calls.push('bootstrapAuthorization');
      state.runtime.sessionAuthorized = true;
      state.authorization.stored = true;
    },
    ...overrides,
  };
  return { calls, state, dependencies };
}

test('四项已经就绪时一键启动保持幂等且不重复执行启动动作', async () => {
  const { calls, dependencies } = createHarness();
  const progress = [];
  const result = await startRakazoDesktopStack({}, (item) => progress.push(item), dependencies);
  assert.equal(result.success, true);
  assert.equal(result.selectableModelCount, 3);
  assert.deepEqual(calls, []);
  assert.equal(progress.at(-1).phase, 'done');
});

test('此前可运行但当前已停止时按 Docker、运行时、已存授权顺序恢复', async () => {
  const { calls, state, dependencies } = createHarness();
  state.docker.ready = false;
  state.runtime.connected = false;
  state.runtime.compatibility.compatible = false;
  state.runtime.sessionAuthorized = false;
  await startRakazoDesktopStack({}, null, dependencies);
  assert.deepEqual(calls, ['startDocker', 'startRuntime', 'restoreAuthorization']);
});

test('用户主动点击一键启动时可创建首次本机授权', async () => {
  const { calls, state, dependencies } = createHarness();
  state.runtime.sessionAuthorized = false;
  state.authorization.stored = false;
  await startRakazoDesktopStack({ createAuthorization: true }, null, dependencies);
  assert.deepEqual(calls, ['bootstrapAuthorization']);
});

test('随 Codebot 启动不会在没有既有授权时静默创建账号', async () => {
  const { calls, state, dependencies } = createHarness();
  state.runtime.sessionAuthorized = false;
  state.authorization.stored = false;
  await assert.rejects(
    startRakazoDesktopStack({ createAuthorization: false }, null, dependencies),
    /未找到已加密保存的 Rakazo 本机授权/,
  );
  assert.deepEqual(calls, []);
});

test('一键启动在 Docker 未安装时停止且不伪装成一键安装', async () => {
  const { calls, state, dependencies } = createHarness();
  state.docker.installed = false;
  state.docker.ready = false;
  await assert.rejects(startRakazoDesktopStack({}, null, dependencies), /不会静默安装系统组件/);
  assert.deepEqual(calls, []);
});

test('OpenCode 没有可用模型时不启动 Rakazo 运行时', async () => {
  const { calls, state, dependencies } = createHarness();
  state.runtime.connected = false;
  state.runtime.compatibility.compatible = false;
  state.runtime.selectableModelCount = 0;
  await assert.rejects(startRakazoDesktopStack({}, null, dependencies), /至少一个模型/);
  assert.deepEqual(calls, []);
});
