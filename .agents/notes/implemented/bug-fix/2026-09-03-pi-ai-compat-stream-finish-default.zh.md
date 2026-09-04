# Agent Note: 未知 openai-completions 模型默认关闭 `supportsFinishReason`

Status: implemented

[English](2026-09-03-pi-ai-compat-stream-finish-default.md) | 中文

## 问题

用户的一个 OpenAI 兼容网关上,自定义模型每次响应都报 `Stream ended without finish_reason`;同一模型在其他 agent 中工作正常。重试策略重试了允许的五次后使本轮失败——这本该是针对瞬时故障的行为,而这个失败却是确定性的。

机制:pi-ai 的 `openai-completions` 流跨 chunk 跟踪 `hasFinishReason`,当流干净地结束——内容已送达、收到 `[DONE]`——却没有任何 chunk 携带真值 `finish_reason` 时抛出该错误。不做合成(或完全不发该字段)直接转发上游的网关会在每次响应上触发它。harness 将该措辞归类为 `TRANSPORT`(见 [[2026-07-22-pi-ai-transport-truncation-classification]],正是它把这一措辞纳入截断家族),于是确定性的网关形态被当作 mid-stream 掉线来重试。

`compat.supportsFinishReason: false` 本就是逃生通道——它告诉 pi-ai 推断终止原因而不是要求该字段——但它只能在 settings 里设置,而未知网关的 URL 没有任何线索提示需要这个开关。

## 决策

`resolveModelCompat` 现在为**目录未知**(`base === undefined`)或**协议被改指**(`base.api !== api`)的 `openai-completions` 模型默认 `supportsFinishReason: false`,前提是路由和模型条目都未配置该字段。已配置的值——路由或模型、任意极性——始终获胜,已安装目录的模型保持其声明/探测到的行为不变。

该插入点位于"无配置 compat → 空"的提前返回之前,因此没有任何其他 compat 的模型也会物化出 `{ compat: { supportsFinishReason: false } }`。这一宽容与官方 DeepSeek 适配器在该接缝处的自身行为一致——那里缺失的终止原因映射为 `stop`。

## 已考虑的替代方案

**不再把该措辞归类为 `TRANSPORT`。** 拒绝:真正的响应中途截断同样会让流在没有 `finish_reason` 的情况下结束,那一情形必须保持可重试。分类器的匹配对它能观察到的东西是正确的;错的是给它喂了确定性形态。

**对每个 `openai-completions` 模型(含目录已知)都默认 `false`。** 拒绝:具名厂商的条目携带 pi-ai 声明/探测到的 compat,静默覆盖声明行为会把适配器的宽容扩大到目录作者未授权的范围。两个排除条件(目录未知、协议改指)恰好是没人声明过流形态的情形。

**给 pi-ai 上游打补丁。** 在锁定的版本上不可行;解析期默认在 harness 内部达成同样效果,并为想要恢复严格性的部署保留逃生通道(`true`)。

## 后果

- 确定性的无 `finish_reason` 网关首次尝试即流式完成;所报告场景的五次重试失败消失。
- 真正的响应中途掉线仍会抛出措辞命中 `classifyPiAiError` 的 `TRANSPORT` 匹配的 SDK 错误,因此重试路径只留给真实截断(以及用 `supportsFinishReason: true` 显式选择严格性的路由)。
- 手声明的 `openai-completions` 模型的解析器测试现在携带默认字段;线上测试钉住两种极性(宽容默认下完成;配置的严格性仍以错误 finish 使本轮失败)。
