# Agent Note: pi-ai 模型行的思考强度控件

Status: implemented

[English](2026-09-03-pi-ai-reasoning-effort-ui.md) | 中文

## 问题

[[2026-08-08-pi-ai-per-model-reasoning-declarations]] 以"零界面改动"交付了按模型的 `reasoningEfforts`:声明过的 profile 会点亮 composer 的强度面板,但声明本身只能写在 `settings.yaml` 里。使用自定义(手声明)模型的用户无法从设置页声明该模型会思考、或以何种强度思考——即用户报告的诉求。

## 决策

`ModelListEditor` 的每个模型行——所有 pi-ai 路由的模型都使用这个编辑器——在行内已有的展开区、容量字段旁边,增加一组思考强度复选框。它提供常用四档(minimal / low / medium / high),每档带双语标签,并附提示:不勾选任何档位即不发送思考。

草稿状态契约与 profile 字段一样区分三种含义:

- **缺省**——用户未触碰该组之前,行上不携带 `reasoningEfforts`,因此目录继承模型的声明行为不会因无关编辑而改变。
- **`{ off: null, <level>: <level>, … }`**——勾选第一档时写入字典,`off` 作为无值的"支持思考、不发送档位"条目,每个勾选档映射到与档位同名的线上拼写(恒等拼写,与提供方式一致)。再勾选即追加。
- **`false`**——取消最后一档时写入"该模型不思考"的拼写。

档位词表是本地常量,镜像 `llm-pi-ai` 目录中的 `THINKING_LEVELS`(schema 的事实来源);client 包刻意不依赖 adapter 包。`xhigh` 与 `max` 不提供——包 README 的 Known-Limitations 条目记录了:勾选任意复选框会按提供的四档重写字典,丢弃手工声明的 `xhigh`/`max` 条目。

共用的 `validateDeepSeekModels` 门(已同时服务两个家族编辑器)新增 `modelReasoningEffortsInvalid` 失败,镜像 adapter 的解析规则:`false` 或字典;键在档位词表内;每个声明档都是非空线上拼写,`null` 仅限 `off`;至少一个 `off` 之外的档位。这里的严格度对齐 adapter 而非界面的四档——adapter 接受的手编 YAML 值不能被设置页拒存。

## 已考虑的替代方案

**提供方级别的思考强度控件。** 拒绝,理由是该控件缺席时原有头注释所记录的:强度是按模型的能力,同一路由下的模型对此并不一致,提供方级别的值必然对其中一些是错的。create 卡片与编辑器的头注释现在指向按行控件,而不是陈述缺席。

**提供完整七档词表。** 拒绝:`xhigh`/`max` 是少见的网关方言,四档让行保持紧凑,且 profile 字段继续接受它们——README 记录了这一取舍。

**复用 DeepSeek 编辑器的容量 `patch` 处理切换。** `patch` 的值类型被放宽以接受档位字典与 `false`(其丢弃 `undefined`/丢弃 `''` 的过滤本就把两者视为保留值),但切换逻辑有独立的 `toggleLevel`——字典重建与"取消最后一档写 `false`"的规则是容量路径不应知晓的档位逻辑。

## 后果

- [[2026-08-08-pi-ai-per-model-reasoning-declarations]] 留下的缺口闭合:声明自定义模型的思考强度现在是设置页的一次交互,composer 强度面板经由该 note 确立的同一接缝跟随生效(由既有 `declared-reasoning` web 场景钉住)。
- 表单测试钉住三种草稿状态与 validator 接受/拒绝的形态;解析器层面的词表变更仍由 adapter 自身测试经漂移门拥有。
