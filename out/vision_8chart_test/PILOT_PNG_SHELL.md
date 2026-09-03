# 试点报告：原图为壳 + 融合文本为切片（大坝注册登记证）

- 日期：2026-09-02；脚本 `pilot_png_shell.py`（幂等，含 `--rollback`）
- 模式：上传原始 png（**不 parse**，杜绝低质 OCR 分片）→ 复制 md 元数据 → 手动挂载 md 的 4 条切片 → 验证 → 下架 md

## 结果

| 项 | 结果 |
|---|---|
| png 上传/元数据 | `353bad48a69611f196c0d10ff025f702`，11 字段与 md 一致，run=UNSTART（从未解析） |
| 切片挂载 | `POST /datasets/{ds}/documents/{id}/chunks` ×4 全部成功（137–279 字/条） |
| 检索 | 删 md 后 top1–4 = 《大坝注册登记证.png》（锚点 61000030008-A2 在 rank1/2，sim 0.53/0.50） |
| 问答层 | vision8_qa_test 答对 61000030008-A2，引用列表含《大坝注册登记证.png》 |
| md 处置 | 已从 ds4 删除（融合文本保留在 `fusion/` 本地，随时可重导） |

## 关键发现（v0.27.1 REST）

1. **`PUT /datasets/{ds}/documents/{id}` 传 `{"status":"0"}` 返回 code=0 但静默不生效**——md 仍 status=1、切片照常参与检索。REST 不暴露"启用/禁用"开关，**下架 md 只能删除**（可回滚）。
2. 手动挂载的切片**无需 parse 即入检索索引**（挂完 3 秒即可召回）；png 文档保持 UNSTART 不会产生 OCR 分片。
3. 手动切片无页码/位置锚点，引用预览即整图（对单图文档正好）。

## 回滚路径

- 重导 md：`cd out/vision_8chart_test && python3 import_fusion_8.py --apply`（幂等按名补传，approved.flag 仍在）；
- 或仅删除 png 名下切片与 png 文档恢复原状。

## 铺开清单（待批准，共 11 张剩余）

- ds4：三个责任人.jpg、中心架构图.png、各部门用水需求.jpg
- ds2：水库基本信息.jpg、溢洪道图2.jpg、物资图1.jpg、物资图2.jpg（第一轮四图另行确认）：
  库容水位对照表.jpg、泄流曲线.jpg、大坝剖面图.jpg、溢洪道图1.jpg
- 每张同试点流程：上传 png/jpg → 元数据对齐 → 挂切片 → 验证锚点 → 删 md。
