"""应用配置，通过环境变量加载，默认值面向单机一键部署。"""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# .env 固定放在 services/backend 下，避免受进程工作目录影响（uvicorn 从任意目录启动都能读到）
_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


class Settings(BaseSettings):
    """后端运行配置。

    取值优先级：环境变量 > .env 文件 > 默认值。
    """

    model_config = SettingsConfigDict(
        env_prefix="SYNETRA_", env_file=str(_ENV_FILE), extra="ignore"
    )

    # 服务
    app_name: str = "synetra-backend"
    app_version: str = "0.1.0"
    host: str = "0.0.0.0"
    port: int = 8000

    # 本地数据目录（Vault 密钥/密文、脚本产物；未配置 DSN 时也用于 SQLite 回退）
    data_dir: str = "data"

    # 元数据/日志库（元数据、血缘、审计、运行记录）。
    # 形如 mysql://user:password@host:port/dbname?charset=utf8mb4；
    # 留空则回退到 data_dir/synetra.db（本地开发/测试）。
    metadata_db_dsn: str = "mysql://root:12345@127.0.0.1:3306/synetra"

    # LLM 接入层（任务 5.1）：供应商抽象，可插拔切换；切换不改变既有任务配置与脚本
    llm_provider: str = "deepseek"
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com/v1"
    deepseek_model: str = "deepseek-chat"

    # 脚本产物根目录（current/versions 原子替换，任务 5.5）；默认置于 data_dir 下
    scripts_root: str = ""

    # 本地轮询调度（8.x）：定时触发支撑；默认开启，可显式关闭
    local_scheduler_enabled: bool = True
    local_scheduler_interval_s: int = 20

    # 失败重试（8.3）：默认次数与间隔秒
    retry_times: int = 2
    retry_interval_s: int = 60

    # 邮件告警（8.3 / 9.5）：SMTP 配置，未配置时记日志降级
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = "synetra@example.com"
    alert_email_to: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()