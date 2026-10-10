# etl_sdk 契约（RAG 注入文档，任务 5.2）

本文件为生成脚本的 SDK 契约。LLM 生成须严格遵循以下接口，仅使用本契约暴露的能力。

## 抽取 (Extract)

```python
from etl_sdk import readers

rows = readers.query(conn_key="SAMPLE_SOURCE", sql="SELECT * FROM orders WHERE updated_at > :watermark")
# 输出：list[dict]；列名即 dict 键。
```

对 REST/API 源：

```python
from etl_sdk import readers
rows = readers.api(conn_key="SAMPLE_API", path="/orders", params={"page": 1})
```

## 转换 (Transform)

```python
from etl_sdk import transforms

# 按配置字段映射 + 规则处理后产生目标行
output = transforms.apply(src_rows=rows, config=task_config)
```

## 加载 (Load)

```python
from etl_sdk import writers

# 目标为关系库：
writer = writers.relational(conn_key="SAMPLE_TARGET", table="orders_dw",
                            mode="upsert", primary_keys=["id"])
writer.buffered(rows=output, watermark_field="max_updated_at")

# 目标为 API（推送幂等由端点保证，不在 SDK 内修改）：
# writers.api(conn_key="SAMPLE_TARGET", path="/orders").push(output)
```

## SDK 运行约束

- 只在 dry-run（`os.environ.get("SYNETRA_DRY_RUN")=="1"`）时，`writers` 不产生任何目标写入，
  仅记录"本应写入"的行数与字段，供预览/校验。
- 所有连接仅通过 `conn_key` 引用，绝不内联明文口令。
- 水位：仅当加载成功后才推进 `watermark_field`；失败保持原水位。

## 示例：增量 upsert 同步

```python
import os
from etl_sdk import readers, transforms, writers

watermark = os.environ.get("SYNETRA_WATERMARK", "1970-01-01")
rows = readers.query(conn_key="SAMPLE_SOURCE", sql="SELECT * FROM orders WHERE updated_at > %(wm)s",
                     params={"wm": watermark})
output = transforms.apply(rows, task_config)
writer = writers.relational(conn_key="SAMPLE_TARGET", table="orders_dw",
                            mode="upsert", primary_keys=["id"])
writer.buffered(output, watermark_field="max_updated_at")
```