# ES 手术手册：重建 ragflow_doc_meta 映射（flood_event date→keyword）

> 待全量导入自然结束后执行（run_import 进程已退出为前提）。
> 原则：零上游源码改动；索引数据先备份后回灌，全程可回滚。
> 执行前置：确认 `python3 -c "import json;print(len([v for v in json.load(open('out/import_state.json')).values() if v['status']!='done']))"` 的行数即本次要复活的目标数。

## 0. 变量与核对

```bash
IDX=ragflow_doc_meta_72fc3f62a01f11f19d7235ad4ea699d4
# 核对映射现状（应看到 meta_fields.flood_event type=date —— 若已被改过则终止）
docker exec docker-es01-1 sh -c \
  "curl -s -u elastic:\$ELASTIC_PASSWORD localhost:9200/${IDX}/_mapping" | head -50
# 字段清单以 src/config.py METADATA_SCHEMA 为准（运行时导出，勿手抄）：
cd src && python3 -c "from config import METADATA_SCHEMA; print([f[0] if isinstance(f,(list,tuple)) else f for f in METADATA_SCHEMA])"
```

## 1. 备份（_search 全量落盘到容器，再拷回宿主 out/）

```bash
docker exec docker-es01-1 sh -c \
  "curl -s -u elastic:\$ELASTIC_PASSWORD -H 'Content-Type: application/json' \
   localhost:9200/${IDX}/_search -d '{\"size\":10000,\"query\":{\"match_all\":{}}}'" \
  > out/es_backup/doc_meta_search.json
# 断言 hits.total.value 与 _count 一致且 >0 后再进行第 2 步
```

## 2. 删除并按显式映射重建

mapping.json 模板要点：
- 所有 meta_fields.* 子字段一律 `"type": "keyword"`（含 flood_event、year——year 原被动态映射成 long 同属地雷）；
- 顶层其余字段沿用现库同名拷贝（status/process 等），执行前用第 0 步的 _mapping 输出补齐，**不要凭记忆构造**；
- 动态模板兜底：`"strings_as_keyword": {"match_mapping_type":"string","mapping":{"type":"keyword"}}`。

```bash
docker exec docker-es01-1 sh -c \
  "curl -s -u elastic:\$ELASTIC_PASSWORD -X DELETE localhost:9200/${IDX}"    # 回滚=不删或重建后 _bulk 回灌即可
curl -s -u elastic:$ES_PASS_HOSTSIDE -X PUT ... # 由执行脚本生成并 PUT 新 mapping
```

## 3. 回灌备份行（_search hits → _bulk ndjson，_id 原样保留）

转换脚本从 out/es_backup/doc_meta_search.json 读 hits.hits[]，逐条生成两行：
`{"index":{"_index":IDX,"_id":"<原_id>"}}` + 原样 `_source`，经容器内 curl POST `/_bulk?refresh=wait_for`。
断言返回 errors=false、items 数 == 备份行数。

## 4. 验证

```bash
# (a) 抽查一条历史行的 flood_event 值原封未动
# (b) 用一位月值试插→删除一枚临时探针文档：_bulk 写 {"meta_fields":{"flood_event":"2013-7"}} 应成功
docker exec docker-es01-1 sh -c \
  "curl -s -u elastic:\$ELASTIC_PASSWORD -H 'Content-Type: application/json' \
   localhost:9200/${IDX}/_mapping" | grep -A3 flood_event     # 必须 type=keyword
```

## 5. 复活失败行

```bash
cd src && python3 run_import.py --apply          # 状态机自动重驱全部非 done 行
```
预期：failed/wait_timeout 行经 upload(复用同名文档)→patch_document→parse 全部转 done。

## 风险与回滚

| 步 | 风险 | 缓解 |
|---|---|---|
| 删索引 | 误删其他索引 | IDX 变量硬编码本库名；DELETE 前必须已见第 1 步断言通过 |
| 回灌 | 行丢失/字段变化 | errors 校验 + 行数对账；备份文件保留在 out/es_backup/ 不清 |
| 新映射缺字段 | RAGFlow 其他写入路径报错 | mapping 其余字段从实时 _mapping 拷贝而非凭记忆 |
