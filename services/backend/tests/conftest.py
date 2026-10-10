"""测试基线配置。

单元测试一律使用「独立临时目录 + SQLite」的元数据库，以保证用例隔离、可离线运行，
不依赖外部 MySQL（默认配置已指向 MySQL）。

环境变量优先级高于 .env 与代码默认值，故这里显式置空 DSN 即强制回退 SQLite；
必须早于 app.core.config 的首次导入执行，因此放在 conftest 顶层。
"""

from __future__ import annotations

import os

os.environ["SYNETRA_METADATA_DB_DSN"] = ""
