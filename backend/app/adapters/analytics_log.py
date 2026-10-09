import json
import logging
from collections.abc import Mapping

from app.ports.analytics import Prop

log = logging.getLogger("app.analytics")


class LogAnalytics:
    """Structured-log sink: one JSON line per event, until a real provider is named (deferred)."""

    def emit(self, name: str, props: Mapping[str, Prop]) -> None:
        log.info(json.dumps({"analytics_event": name, "props": dict(props)}, sort_keys=True))
