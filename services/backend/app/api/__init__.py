"""app.api：HTTP 路由。"""

from .access import router as access_router  # noqa: F401
from .auth import router as auth_router  # noqa: F401
from .catalog import router as catalog_router  # noqa: F401
from .connections import router as connections_router  # noqa: F401
from .llm import router as llm_router  # noqa: F401
from .mdm import router as mdm_router  # noqa: F401
from .metadata import router as metadata_router  # noqa: F401
from .monitor import router as monitor_router  # noqa: F401
from .projects import router as projects_router  # noqa: F401
from .query import router as query_router  # noqa: F401
from .schedule import router as schedule_router  # noqa: F401
from .scripts import router as scripts_router  # noqa: F401
from .tasks import router as tasks_router  # noqa: F401