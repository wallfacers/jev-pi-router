# jev-pi-router — PI 多厂商模型路由

强模型（mimo/glm 池）做计划/决策/review/兜底/主控，flash 模型（deepseek/glm-flash/relay/
opencode-go 池）子代理实现代码；混合裁决（配置表 + Jev typed-choice + fail-open）；双层跨厂
商 review；双轨兜底；主会话零切换保护前置缓存。

需求与设计文档见 `specs/001-pi-model-routing/`（spec / plan / research / data-model /
contracts / quickstart）。

## 安装

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
cp router.config.yaml.example router.config.yaml
bin/jev-pi-doctor --config router.config.yaml check    # 校验 api_ref 已注册于 pi
```

### pi 侧集成

1. **缓存保活**（research R5）：将 `pi/models.promptcache.json` 的 `promptCache` 声明合并进
   `~/.pi/agent/models.json` 对应模型条目；并在 `~/.pi/settings.json` 设置
   `"cacheWarming": "idle"`。
2. **派发技能**：将 `pi/skills/jev-pi-router/` 安装到 `~/.pi/agent/skills/`。
3. **Jev 决策通道**：环境变量 `TYPESAFE_ENDPOINT` / `TYPESAFE_MODEL` / `TYPESAFE_API_KEY`
   （或 `AI_GATEWAY_API_KEY`）已配置即可；协议兼容 jev-ultrafast 的网关适配。

## 使用

```bash
echo '<request JSON>' | bin/jev-pi-decide [--engine auto|rules|jev] [--config PATH]
bin/jev-pi-report [--days N] [--json]
bin/jev-pi-doctor check
bin/jev-pi-doctor probe relay/cmd-deepseek-v4.1-flash --write-back   # 中转缓存透传实测（R8）
```

请求/响应契约：`specs/001-pi-model-routing/contracts/decision-cli.md`。
日志 schema 与不变量：`contracts/decision-log-schema.md`（写入 `~/.jev-pi-router/decisions.jsonl`）。
环境变量 `JEV_PI_ROUTER_HOME` 可改日志/状态目录（测试隔离用）。

## 测试与验证

```bash
pytest                       # unit + contract（quickstart V5）
```

端到端验证场景 V1~V9 与 SC 对账表：`specs/001-pi-model-routing/quickstart.md`。

## 配置

一切模型池/角色/配对/兜底参数都在 `router.config.yaml`（schema 与校验规则：
`contracts/config-schema.md`）。增删厂商、调整池序**只改配置**（FR-013），改完即时生效。
