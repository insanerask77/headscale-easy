"""What every feature mixin can rely on from the console's request handler."""

from __future__ import annotations

from typing import TYPE_CHECKING

from http_base import HttpHelpers


class HandlerBase(HttpHelpers):
    """Base of the feature mixins. The methods below are defined by ``app.Handler`` (they need its settings and
    the session store); they are declared here, for type checkers only, so the mixins can call them."""

    if TYPE_CHECKING:
        def fail(self, status: int, title: str, text: str) -> None: ...
        def session(self) -> dict | None: ...
        def rate_limited(self, bucket: str) -> int: ...
        def too_many(self, wait: int, page: str | None = None) -> None: ...

        @staticmethod
        def set_cookie(name: str, value: str, max_age: int) -> tuple[str, str]: ...

        def keys_view(self, session: dict, flash: str, new_key: dict | None = None, new_apikey: str = "",
                      preselect: str = "") -> None: ...
