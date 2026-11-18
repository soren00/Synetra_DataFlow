# 流水线步骤参考

本文档与代码保持同源：步骤类型与配置项由 `app/etl_sdk/pipeline/registry.py` 声明，
接口 `GET /api/pipeline/steps` 直接返回该声明，画布节点面板据此渲染。
新增/修改步骤时请同步本文件。

## 通用约定

- **数据形态**：全流程是「行字典」流（`dict`），列名 = 键。所有步骤在原行基础上增删列，未声明的列原样透传（`select` 除外）。
- **批次**：源端按 `batch_size`（默认 5000）分批读取；行级步骤逐批处理；`sort / unique / aggregate` 需要全量，超过 `spill_threshold_mb` 时把有序块溢出到 `data_dir/spill` 再归并（`union` 顺序读入各入边，不做全量物化）。
- **写入**：按 `commit_size`（默认 5000）分批提交；`upsert` 需主键，重跑幂等；`append` 重跑会重复。
- **类型提升**（`union` 与合并列）：`bool < int < float < str`，逐列取各入边样本中实际值的最大等级；样本是每个入边的前 100 行（列集合同样按这 100 行推断，仅出现在样本之后的列会被丢弃）；冲突列在画布「解析列对齐」中可见。
- **空值**：`None` 参与排序时升序排最前、降序排最后；`value_map` 把 `None` 视为 `""` 查表；`null_fill` 把 `None` 与 `""` 都视为空。
- **受限表达式**：仅用于 `filter / expression / select.rules[].expression`，可用函数见 `app/expr/whitelist.py`（`str/int/float/bool/len/abs/round/min/max/sum/lower/upper/strip/replace/concat/cast`），禁止属性访问、赋值、SQL 写法。

## 源（source，入边 0 / 出边 1）

| 步骤 | 配置 | 行为 |
| --- | --- | --- |
| `source_table` | `conn_key`、`object`、`watermark_field?`、`track_watermark?` | `SELECT * FROM object`；配置 `watermark_field` 且执行期有已保存水位时追加 `WHERE field > 水位`；`track_watermark` 指定推进水位的列（按该列最大值推进，留空则不推进） |
| `source_sql` | `conn_key`、`sql` | 直接执行只读 SQL（不做水位过滤，水位需自行写在 SQL 中） |
| `source_csv` | `path`、`delimiter?` | 本地路径或 `http(s)://`；UTF-8-SIG 解码；首行为表头 |
| `source_api` | `conn_key`、`path?`、`records_path?` | 经连接元数据（`url`/`method`/`auth`）发请求；`path` 与 `url` 拼接，`records_path` 逐级取嵌套字段（如 `data.items`）；取到对象时视为单条记录，数组中的非对象元素被跳过 |

## 合并（merge）

| 步骤 | 配置 | 行为 |
| --- | --- | --- |
| `union` | `columns?`、`aliases?` | 至少 2 条入边。列集合 = 各入边并集（或显式 `columns`，其顺序即输出顺序）；按列名对齐，缺失列补空，同名类型按提升规则归一；`aliases` 为 `源列: 结果列` 映射，可重命名输出列（重名时后者覆盖前者）。**不做跨源 join** |

```json
{"id": "u", "type": "union", "config": {}}
```

## 行级（row）

| 步骤 | 配置 | 行为 |
| --- | --- | --- |
| `select` | `rules[]` | 字段映射（五类规则，与旧 `fields` 同构）：`direct`/`constant`/`null_fill`/`cast`/`expression`。**只输出规则声明的列**，未映射列丢弃；`rules` 为空时整行透传 |
| `filter` | `expression` | 保留表达式为真的行；不改变行内容，被拒行计入 `rejected_rows` |
| `cast` | `field`、`target?`、`cast_to` | `int/float/str/bool/bool_int`；`int/float/str` 转换失败写 `None`；`bool_int` 不做转换，恒为 `1`（真）/`0`（假，含 `None`） |
| `expression` | `target`、`expression` | 计算新列（受限表达式），原列保留 |
| `value_map` | `field`、`target?`、`mapping`、`default?` | 静态码表映射：键为源值字符串；未命中用 `default`（缺省 `None`） |
| `null_fill` | `field`、`target?`、`default` | `None` 或 `""` 时填默认值 |
| `string_ops` | `field`、`target?`、`op`、`args?` | `trim/lower/upper/replace/substr`；`replace` 需 `args:[old,new]`，`substr` 需 `args:[start,end?]`；非字符串先 `str()`，`None` 透传 |
| `constant` | `target`、`value` | 写入固定常量列 |
| `sequence` | `target`、`start?`、`step?` | 递增序列号，按执行顺序编号；默认 1 起、步长 1，`start` 或 `step` 传 0 会回落为 1 |

```json
{"id": "pick", "type": "select", "config": {"rules": [
  {"kind": "direct", "source": "id", "target": "id"},
  {"kind": "null_fill", "source": "name", "target": "name", "null_default": "匿名"},
  {"kind": "cast", "source": "amount", "target": "amount", "cast_to": "float"},
  {"kind": "expression", "source": "name", "target": "name_up", "expression": "upper(name)"}
]}}
```

## 整批（batch，需要全量，可溢出）

| 步骤 | 配置 | 行为 |
| --- | --- | --- |
| `sort` | `keys:[{field, desc?}]` | 多列稳定排序，逐列比较；空值规则见上 |
| `unique` | `keys:[列]` | 按 key 排序后去重，保留排序后的首行（输出即按 key 有序；`keys` 留空则按整行）；重复行计入 `rejected_rows` |
| `aggregate` | `group_by:[列]`、`aggregations:[{func, field, target}]` | 分组聚合；`func` 支持 `sum/count/min/max/avg/first/last`，`field="*"` 时 `count` 计数、其它按 1 处理；`first` 取首个非空值、`last` 取末行值（可为空）；`sum/avg` 遇非数值按 0 计；分组键为空值也会成为一组 |

```json
{"id": "agg", "type": "aggregate", "config": {
  "group_by": ["dept"],
  "aggregations": [
    {"func": "sum", "field": "amount", "target": "total"},
    {"func": "count", "field": "*", "target": "cnt"},
    {"func": "avg", "field": "amount", "target": "avg_amount"}
  ]}}
```

## 目标（target，入边 1 / 出边 0）

| 步骤 | 配置 | 行为 |
| --- | --- | --- |
| `target_table` | `conn_key`、`object`、`mode`、`primary_keys?` | `append` 批量插入；`upsert` 按主键冲突更新（需主键，方言各自实现）；目标表不存在时按首批列自动建表 |
| `target_csv` | `path` | 输出 UTF-8 CSV（分隔符固定为 `,`，不支持自定义）；列取首批首行的键集合，后续行出现该集合外的键会报错 |

## 完整示例：两源合并 → 清洗 → 聚合 → upsert

```json
{
  "nodes": [
    {"id": "a", "type": "source_table", "config": {"conn_key": "crm", "object": "users"}},
    {"id": "b", "type": "source_table", "config": {"conn_key": "erp", "object": "users"}},
    {"id": "u", "type": "union", "config": {}},
    {"id": "pick", "type": "select", "config": {"rules": [
      {"kind": "direct", "source": "id", "target": "id"},
      {"kind": "direct", "source": "dept", "target": "dept"},
      {"kind": "cast", "source": "amount", "target": "amount", "cast_to": "float"}
    ]}},
    {"id": "f", "type": "filter", "config": {"expression": "amount > 0"}},
    {"id": "d", "type": "unique", "config": {"keys": ["id"]}},
    {"id": "g", "type": "aggregate", "config": {
      "group_by": ["dept"],
      "aggregations": [{"func": "sum", "field": "amount", "target": "total"}]
    }},
    {"id": "w", "type": "target_table",
     "config": {"conn_key": "dw", "object": "dept_total", "mode": "upsert", "primary_keys": ["dept"]}}
  ],
  "edges": [
    {"source": "a", "target": "u"}, {"source": "b", "target": "u"},
    {"source": "u", "target": "pick"}, {"source": "pick", "target": "f"},
    {"source": "f", "target": "d"}, {"source": "d", "target": "g"},
    {"source": "g", "target": "w"}
  ],
  "batch_size": 5000,
  "commit_size": 5000
}
```

## 旧配置等价编译

任务只配了 `fields`（无 `pipeline`）时，执行前按
`source_table(source_conn_key/source_object) → select(fields) → target_table(target_conn_key/target_object/mode/primary_keys)`
编译，抽取范围、字段映射、写入模式与增量语义与旧版本一致；保存画布后以 `pipeline` 为准。

## 试运行与观测

- `POST /api/tasks/{key}/nodes/{node_id}/preview`：执行到该节点，返回样本与步级指标，不写目标。
- `POST /api/tasks/{key}/nodes/{node_id}/schema`：合并节点的入边列集合、对齐结果与类型提升。
- `GET /api/runs/{run_id}/steps`：真实运行的步级 `in/out/rejected/耗时/状态`。
- 失败时自动把失败步骤样本写到 `data_dir/runs/<run_id>/`，按 `SYNETRA_RUN_SNAPSHOT_KEEP` 清理。
