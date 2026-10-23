# 百炼接入点排查（2026-10-23）

线上对话模型为 `deepseek-v4-flash-0731`。在同一台服务器使用同一把现有密钥，最简 OpenAI 兼容请求和 DashScope 原生请求经旧 `dashscope.aliyuncs.com` 接入点均返回 `403 / AllocationQuota.FreeTierOnly`。北京试用接入点返回 200；从现有控制台获取的北京业务空间专属 API Host 也返回 200，并成功生成 JSON。未创建、重置密钥，未修改免费额度保护开关。

这证明本次失败与接入点有关，不足以说明旧接入点对所有用户均存在问题，也不能据此断定具体后台计费故障原因。正式服务改用现有业务空间专属域名，格式为 `https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/compatible-mode/v1`，实际地址保留在服务器环境配置中。试用域名不用于生产。

并行对照于 2026-10-23 11:27（北京时间）复现：同一时刻、相同密钥和完全相同请求体，旧域名 403（请求 ID `cd62a915-bae0-9559-be5a-5a107e1315bf`），业务空间域名 200（请求 ID `47539730-297c-90a3-bf16-d039bf8622e1`）。部署后，真实应用的结构化 JSON 输出和 `search_papers` 原生工具调用均成功。

客户端对官方业务空间域名保留 `enable_thinking=false`，避免结构化回答预算被隐藏推理消耗。`AllocationQuota.FreeTierOnly` 单独提示检查接入地址、模型额度和账号状态，不再笼统显示为密钥无效。

独立核验：向量为硅基流动 `BAAI/bge-m3`，返回 1024 维；重排为 `BAAI/bge-reranker-v2-m3`，真实请求成功。两项配置无需修改。MinerU 为 `vlm`，本次未进行文档解析测试。

官方依据：

- https://help.aliyun.com/zh/model-studio/beijing-access-information
- https://help.aliyun.com/zh/model-studio/error-code/
- https://help.aliyun.com/zh/model-studio/new-free-quota
