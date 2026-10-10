# 受限表达式正反例（可执行清单）

本文件是 `SKILL.md` 的配套示例，**同时是测试用例**：`tests/test_expression_skill.py` 会解析下面各标记块并断言其结论成立（正例可求值、反例被拒、运行期错误类静态通过但求值失败）。

行格式：`表达式 | 行数据(JSON) | 期望值或期望异常`。

## 正例（通过校验且可安全求值）

<!-- POSITIVE:BEGIN -->
lower(email) | {"email": "A@B.com"} | 'a@b.com'
upper(name) | {"name": "abc"} | 'ABC'
strip(name) | {"name": "  x  "} | 'x'
replace(status, 'paid', 'PAID') | {"status": "paid-1"} | 'PAID-1'
concat(id, '-', name) | {"id": 1, "name": "x"} | '1-x'
concat(upper(name), '-', id) | {"name": "ab", "id": 3} | 'AB-3'
round(amount, 2) | {"amount": 1.234} | 1.23
round(amount) | {"amount": 1.6} | 2
cast(amount, int) | {"amount": "12"} | 12
cast(code, str) | {"code": 12} | '12'
len(name) | {"name": "abcd"} | 4
abs(diff) | {"diff": -3} | 3
min(a, b) | {"a": 1, "b": 2} | 1
max(a, b) | {"a": 1, "b": 2} | 2
sum([a, b]) | {"a": 1, "b": 2} | 3
int(amount) | {"amount": "7"} | 7
float(qty) | {"qty": "1.5"} | 1.5
str(code) | {"code": 12} | '12'
bool(flag) | {"flag": 0} | False
id + 1 | {"id": 1} | 2
lower(amount) if amount else 'unknown' | {"amount": ""} | 'unknown'
1 if amount > 0 else 0 | {"amount": 5} | 1
amount / qty if qty else 0 | {"amount": 10, "qty": 0} | 0
phone[0] | {"phone": "13800138001"} | '1'
phone[0:3] | {"phone": "13800138001"} | '138'
phone[7:] | {"phone": "13800138001"} | '8001'
phone[-4:] | {"phone": "13800138001"} | '8001'
concat(phone[0:3], '****', phone[7:]) | {"phone": "13800138001"} | '138****8001'
concat(customer_name[0], '*', customer_name[2:]) | {"customer_name": "张小明"} | '张*明'
concat(customer_name[0], '*', customer_name[2:]) | {"customer_name": "张三"} | '张*'
concat(id_card[0:6], '********', id_card[-4:]) | {"id_card": "110101199001011234"} | '110101********1234'
phone[0:3] + '****' + phone[7:] | {"phone": "13800138001"} | '138****8001'
'paid' if status == 1 else 'unpaid' | {"status": 1} | 'paid'
'a' in name | {"name": "abc"} | True
lower(email) | {"email": null} | None
strip(name) | {"name": null} | None
replace(status, 'a', 'b') | {"status": null} | None
<!-- POSITIVE:END -->

注：`lower` / `upper` / `strip` / `replace` 对**非字符串入参原样返回**（不报错），因此对可能为 `null` 的列直接套用是安全的。

## 反例（保存时被校验拒绝 → 任务配置不被保存）

<!-- REJECTED:BEGIN -->
lower(email).strip() | 表达式禁止属性访问
email.lower() | 表达式禁止属性访问
datetime.now() | 表达式禁止属性访问
'{}'.format(a) | 表达式禁止属性访问
coalesce(amount, 0) | 函数不在白名单
nvl(status, 'x') | 函数不在白名单
isnull(a) | 函数不在白名单
__import__('os') | 函数不在白名单
eval('1') | 函数不在白名单
open('x') | 函数不在白名单
CONCAT(LEFT(customer_name, 1), '*', SUBSTRING(customer_name, 3)) | 函数不在白名单
CONCAT(LEFT(phone, 3), '****', RIGHT(phone, 4)) | 函数不在白名单
CONCAT(a, b) | 函数不在白名单
LEFT(name, 2) | 函数不在白名单
RIGHT(name, 2) | 函数不在白名单
SUBSTRING(name, 2, 2) | 函数不在白名单
TRIM(name) | 函数不在白名单
LENGTH(name) | 函数不在白名单
CASE WHEN status = 1 THEN 'paid' ELSE 'unpaid' END | 表达式语法错误
CAST(amount AS int) | 表达式语法错误
a = 1 | 表达式语法错误
<!-- REJECTED:END -->

## 运行期错误（静态校验通过，但一运行就失败）

这类最危险：保存时不会报错，任务执行时才暴露。

<!-- RUNTIME_ERROR:BEGIN -->
row['a'] | {"a": 1} | NameError
missing_col | {"a": 1} | NameError
sum(a, b) | {"a": 1, "b": 2} | TypeError
int(amount) | {"amount": "abc"} | ValueError
<!-- RUNTIME_ERROR:END -->

规避方法：
- 绝不写 `row['col']`，改为直接写列名；
- 只引用源表结构里**确实存在**的列名（生成前对照源表结构逐列核对）；
- 需要"转换失败也不要炸"时，用 `kind: "cast"`（失败返回 `null`）而不是 `expression: int(...)`；
- `sum` 的参数必须是可迭代对象：`sum([a, b])`。

## 通过校验但不合规范（能跑通，但与 concat 语义重复 → 统一改写）

行格式：`不合规范写法 | 推荐写法 | 行数据(JSON)`；三者须求值相等。

<!-- DISCOURAGED:BEGIN -->
f'{id}-{name}' | concat(id, '-', name) | {"id": "1", "name": "x"}
'%s-%s' % (id, name) | concat(id, '-', name) | {"id": "1", "name": "x"}
id + '-' + name | concat(id, '-', name) | {"id": "1", "name": "x"}
<!-- DISCOURAGED:END -->

另有两类「本不该用表达式」的等价改写（无需求值，故不列入上表）：

- `expression: int(amount)` → 应写 `kind: "cast", cast_to: "int"`；
- 想让空值兜底为 0（如 `coalesce(amount, 0)` 的意图）→ 应写 `kind: "null_fill", null_default: "0"`。

前三行能通过白名单校验且可求值，但与 `concat()` 语义重复，会让同一份任务配置出现多套等价写法；生成阶段就应统一为 `concat()`。
