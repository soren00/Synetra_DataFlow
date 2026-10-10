"""app.queries：只读查询守卫与执行。"""

from .catalog import (  # noqa: F401
    CatalogNotSupported,
    CatalogResult,
    build_snippet,
    list_objects,
    list_schemas,
)
from .executor import (  # noqa: F401
    QueryNotImplemented,
    QueryRejected,
    QueryResult,
    run_select,
)