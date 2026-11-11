"""The default runner: the seam exists, no adapter is connected yet."""

from src.autotests.models import ApiTestReport, ApiTestRequest
from src.autotests.ports import ApiTestError


class DisabledApiTestRunner:
    """Fail loudly instead of pretending that API tests were executed."""

    name = "disabled"

    def run(self, request: ApiTestRequest) -> ApiTestReport:
        raise ApiTestError(
            "адаптер API-тестов не подключён: сеть и ДЗ3 advanced в этот шаг "
            "не входят. Контракт описан в openspec/specs/autotests/spec.md"
        )
