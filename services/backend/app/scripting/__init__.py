"""脚本生成校验与执行（任务 5.3/5.4/5.5）。"""

from .checks import static_validate, syntax_check  # noqa: F401
from .executor import dry_run_script  # noqa: F401