---
name: expression
description: ETL 字段转换规则与受限表达式的使用规范——何时用哪类规则、表达式怎么写（含切片与字符串处理）、哪些写法会被拒绝或运行期失败
applies_to: field-rule-expression
---

# 字段转换规则与受限表达式使用规范

本技能用于生成或修订 **TaskConfig 的 `fields[]`**。目标：一次写对——既不被表达式白名单校验拒绝，也不写出运行期才报错的表达式，更不产生两套等价写法。

> 本文件中的「受支持函数清单」与「禁止项清单」由测试与 `app/expr/whitelist.py` 的权威白名单逐字比对；改动清单必须同步代码，否则测试失败。

## 1. 先选规则类型，再想表达式

`fields[]` 每个元素是一条字段规则，`kind` 决定处理方式。**选择判据：按下表顺序判断，能用前面的就不用后面的**：

| 顺序 | kind | 用途 | 必填字段 |
|---|---|---|---|
| 1 | `direct` | 源字段直接映射到目标字段 | `source`、`target` |
| 2 | `constant` | 目标字段是固定常量 | `constant_value` |
| 3 | `null_fill` | 源值为空（`null` 或空串）时用默认值兜底 | `null_default`，可选 `cast_to` |
| 4 | `cast` | 只做类型转换 | `cast_to`（`int`/`float`/`str`/`bool`/`bool_int`） |
| 5 | `expression` | 以上四类都无法表达时才用受限表达式 | `expression` |

把「字符串转整数」写成 `expression: "int(amount)"` 是**错的**，应写 `kind: "cast", cast_to: "int"`；把「空值填 0」写成 `expression: "coalesce(amount, 0)"` 也是错的，应写 `kind: "null_fill", null_default: "0"`。

## 2. 表达式怎么写

- 表达式是一段**受限 Python 表达式**（单行、无语句、无副作用），只能产出**一个标量值**，写入该规则的 `target` 列。
- **字段引用 = 直接写源列名**（列名即变量名）：`email`、`amount`、`order_id`。
- 只有**合法的 Python 标识符列名**才能被引用（列名含空格、连字符、中文、点号时不可引用）。
- **字符串字面量（尤其汉字）必须用英文单引号括起来**：`'正常'`、`'异常'`、`'未知'`。裸写汉字（如 `正常`）会被当成变量名，而汉字不是合法标识符，运行期报 `NameError`；也不要写成中文引号 `'正常'`（会被静态校验拒绝）。
- **函数名必须小写**：`concat` 可以，`CONCAT` 一律被拒（大小写敏感）。
- **没有 SQL 方言函数**：不存在 `CONCAT` / `LEFT` / `RIGHT` / `SUBSTRING` / `SUBSTR` / `COALESCE` / `IFNULL` / `TRIM` / `LENGTH` / `CASE WHEN`。取子串只能用**切片**（见下节），SQL 习惯写法会在保存时被拒。
- **禁止**用 `row['col']` 这种下标写法：静态校验不会拦它，但运行期会因 `row` 未定义而报 `NameError`。
- 只能引用**源表结构里确实存在的列**；引用不存在的列同样静态通过、运行期 NameError。

<!-- WRITING_TIPS:BEGIN -->
- 字段引用：直接写列名（如 customer_name、amount），不要写 row['customer_name']；
- 取子串/取首尾字符：用切片 customer_name[0]、customer_name[1:3]、phone[-4:]（没有 left / right / substring）；
- 拼接字符串：统一用 concat(a, '-', b)，不要用 f''、% 或 +；
- 函数名必须小写，且只能用受支持函数清单里的函数（大写 CONCAT 会被拒）；
- 条件取值：用三元表达式 a if 条件 else b（没有 case when / ifnull / coalesce）；
- 字符串/汉字字面量：必须用英文单引号括起来，如 '正常'、'异常'；不要裸写汉字或使用中文引号；
- 只是空值兜底或类型转换时，改用 kind: null_fill / cast，不要写表达式。
<!-- WRITING_TIPS:END -->

## 3. 可用的操作符与切片

除白名单函数外，以下纯操作符形式**合法且推荐**：

| 形式 | 用法 | 示例 | 结果（phone=13800138001） |
|---|---|---|---|
| 切片取值 | `col[i]` 取单个字符 | `phone[0]` | `'1'` |
| 切片区间 | `col[a:b]`（0 起、含 a 不含 b） | `phone[0:3]` | `'138'` |
| 切片到末尾 | `col[a:]` | `phone[7:]` | `'8001'` |
| 切片取尾部 | `col[-n:]` | `phone[-4:]` | `'8001'` |
| 字符串拼接 | `a + b`（两侧须都是字符串） | `phone[0:3] + '****'` | `'138****'` |
| 比较 | `a == b`、`a > 0`、`a in [...]` | `status == 1` | `True` |
| 三元取值 | `x if 条件 else y` | `'paid' if status == 1 else 'unpaid'` | `'paid'` |

**取子串一律用切片**：`phone[0:3]` 等价于 SQL 的 `LEFT(phone,3)`，`phone[-4:]` 等价于 `RIGHT(phone,4)`，`name[1:3]` 等价于 `SUBSTRING(name,2,2)`（SQL 从 1 开始计数，Python 从 0 开始，下标要减 1）。

## 4. 受支持函数清单

<!-- FUNCTIONS:BEGIN -->
str, int, float, bool, len, abs, round, min, max, sum, lower, upper, strip, replace, concat, cast
<!-- FUNCTIONS:END -->

| 函数 | 语义 | 边界与示例 |
|---|---|---|
| `str` / `int` / `float` / `bool` | 内建类型转换 | `int(amount)`；转换失败会抛异常，值不确定时优先用 `kind: "cast"`（失败返回 `null`） |
| `len` | 长度 | `len(name)` |
| `abs` | 绝对值 | `abs(diff)` |
| `round` | 四舍五入到指定小数位 | `round(amount, 2)`；第二参省略为取整 |
| `min` / `max` | 取最小 / 最大 | `max(a, b)`；可直接传多个标量 |
| `sum` | 求和 | `sum([a, b])`；**参数必须可迭代**，`sum(a, b)` 是错的 |
| `lower` / `upper` | 转小写 / 大写 | 非字符串入参**原样返回**，不报错 |
| `strip` | 去掉首尾空白 | 非字符串入参原样返回（**这才是去空格，不是 `trim`**） |
| `replace` | 子串替换 | `replace(status, 'paid', 'PAID')`，三参必填；非字符串入参原样返回 |
| `concat` | 拼接为字符串 | `concat(id, '-', name)`，任意个参数，统一 `str()` 后拼接（**这才是拼接，不是 `+` 更不是 `CONCAT`**） |
| `cast` | 按指定类型转换 | `cast(amount, int)` 等价于 `int(amount)`；类型作为第二参传入 |

字符串拼接**统一用 `concat(...)`**：不要写 `f'{a}-{b}'`、`'%s-%s' % (a, b)` 或 `a + '-' + b`。前两者虽然能通过校验，但会让同一份任务配置出现多套等价写法。

## 5. SQL / 数据库习惯 → 合规等价写法

| SQL 习惯写法（会被拒或不可用） | 合规写法 |
|---|---|
| `CONCAT(a, b)` | `concat(a, b)`（小写） |
| `LEFT(s, n)` | `s[:n]` |
| `RIGHT(s, n)` | `s[-n:]` |
| `SUBSTRING(s, a, len)` / `SUBSTR(...)` | `s[a-1:a-1+len]` |
| `COALESCE(a, b)` / `IFNULL(a, b)` / `NVL(a, b)` | `kind: "null_fill", null_default: "b"` |
| `CASE WHEN c THEN x ELSE y END` | `x if c else y` |
| `CAST(x AS int)` | `kind: "cast", cast_to: "int"` |
| `TRIM(s)` | `strip(s)` |
| `LENGTH(s)` | `len(s)` |
| `ROUND(x, 2)` | `round(x, 2)` |

## 6. 禁止项清单

<!-- FORBIDDEN:BEGIN -->
- 禁止属性访问（如 value.strip()、value.lower()），请改用白名单函数 lower/upper/strip；
- 禁止赋值、海象运算符等任何存储操作；
- 禁止调用白名单之外的函数（含内建函数与自造函数），也不存在 import；
- 禁止访问对象属性与下标之外的任何运行时对象（没有 row/now 等上下文变量）；
- 只能引用当前行的列名（须为合法标识符）与上表函数，字母大小写敏感。
<!-- FORBIDDEN:END -->

## 7. 三类错误与后果

1. **保存被拒（422）**：属性访问、非白名单函数（如大写 `CONCAT`、`LEFT`）→ 任务配置**不被保存**，必须改对再提交。
2. **静态通过但运行期报错**：`row['col']`、引用不存在的列名 → 保存成功，但任务一运行就 `NameError`。生成时必须只用源表结构里**确实存在**的列名。
3. **通过但不合规范**：`f''`、`%` 拼接等 → 保存成功但写法不一致，应在生成阶段就用 `concat()` 统一。

## 8. 常见需求 → 推荐写法

| 需求 | 推荐 | 不要写 |
|---|---|---|
| 拼接 id 与 name | `expression: concat(id, '-', name)` | `CONCAT(...)`、`f'{id}-{name}'`、`id + '-' + name` |
| 姓名第二个字打码（张小明→张*明） | `expression: concat(customer_name[0], '*', customer_name[2:])` | `CONCAT(LEFT(...),'*',SUBSTRING(...))` |
| 姓名只保留姓（张小明→张*） | `expression: concat(customer_name[0], '*')` | `LEFT(name,1)` + `'*'` |
| 手机号中间四位打码（138****8001） | `expression: concat(phone[0:3], '****', phone[7:])` | `CONCAT(LEFT(phone,3),'****',RIGHT(phone,4))` |
| 身份证保留前 6 后 4 | `expression: concat(id_card[0:6], '********', id_card[-4:])` | `CONCAT(LEFT(id_card,6), ...)` |
| 邮箱局部打码（a@b.com→***@b.com） | 仅 `replace(email, email[0], '*')` 这类有限处理；**按下标定位 `@` 无法表达**（没有 `index`/`find`/`split`），此类需求应明确告知用户无法用受限表达式实现 | `SUBSTRING(email, 1, 1)` |
| 取手机号前 3 位 | `expression: phone[0:3]` | `LEFT(phone,3)`、`SUBSTRING(phone,1,3)` |
| 取订单号后 4 位 | `expression: order_no[-4:]` | `RIGHT(order_no,4)` |
| 邮箱转小写 | `expression: lower(email)` | `email.lower()` |
| 去掉首尾空格 | `expression: strip(name)` | `TRIM(name)`、`name.strip()` |
| 状态值替换 | `expression: replace(status, 'paid', 'PAID')` | `status.replace(...)` |
| 金额保留 2 位 | `expression: round(amount, 2)` | — |
| 字符串转整数 | `kind: cast, cast_to: int` | `expression: int(amount)`、`CAST(x AS int)` |
| 空值兜底为 0 | `kind: null_fill, null_default: '0'` | `expression: coalesce(amount, 0)` |
| 空值兜底后转小写 | `expression: lower(amount) if amount else 'unknown'` | 无（`coalesce` 不存在） |
| 状态映射为文本 | `expression: 'paid' if status == 1 else 'unpaid'` | `CASE WHEN status = 1 THEN ... END` |
| 状态映射为中文文本 | `expression: '正常' if status == 0 else '异常'` | `CASE WHEN status = 0 THEN '正常' ELSE '异常' END` |

正例/反例的完整可执行清单见同目录 `examples.md`。

## 9. 生成前的自检

生成 `fields[]` 后逐条自问：
1. 这条规则是否本可以用 `direct` / `constant` / `null_fill` / `cast` 表达？（若是，改掉）
2. 表达式中的每个名字，是源表结构里确实存在的列名，还是上表 16 个函数之一？（若都不是 → 会被拒或运行期报错）
3. 是否出现了 `.` 属性访问、`import`，或任何**全大写/ SQL 风格函数**（`CONCAT`/`LEFT`/`RIGHT`/`SUBSTRING`/`COALESCE`/`TRIM`/`LENGTH`/`CASE WHEN`）？（若是，按第 5 节对照表改写）
4. 需要取子串时，是否用了**切片**（`col[0]`、`col[a:b]`、`col[-n:]`）而不是 `left`/`right`/`substring`？
5. 是否用了 `f''`/`%`/`+` 拼接？（若想统一，改成 `concat`）
