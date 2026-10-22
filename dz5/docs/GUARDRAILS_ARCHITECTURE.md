# Архитектурное руководство: Guardrails Server + SDK против Монолитного контейнера

Данный документ описывает разницу между двумя подходами к интеграции эшелонов защиты больших языковых моделей (LLM) и предоставляет практическое руководство по интеграции ИИ-Агента на Go с выделенным контейнером безопасности на базе Python (**`Guardrails Server`**).

Текущая конфигурация стенда с описанием каждого параметра: [`GUARDRAILS_CONFIG.md`](GUARDRAILS_CONFIG.md).

---

## 1. Сравнительный анализ подходов

Когда мы выносим безопасность ИИ в отдельный контейнер `security-wall`, мы можем организовать код внутри него двумя путями:

### Подход А: «Лепить набор библиотек в контейнер» (Толстый REST-прокси)
В этом случае вы устанавливаете `fastapi`, `guardrails-ai` и плагины внутрь контейнера, а затем **вручную** пишете эндпоинты в `main.py` (например, `@app.post("/validate_input")`), внутри которых настраиваете вызовы библиотек через Python-код.

* **Плюсы:** Полный контроль над кодом. Вы можете писать любую кастомную логику на чистом Python внутри FastAPI (например, кастомные Regex, ручные логи в БД или сложные ветвления).
* **Минусы:** 
  * **Связанность кода:** При добавлении нового валидатора (например, проверки токсичности) вам нужно заново переписывать код эндпоинтов, менять Pydantic-схемы запросов FastAPI и пересобирать контейнер.
  * **Повторное изобретение велосипеда:** Вы тратите время на написание обёток, обработку HTTP-ошибок, сериализацию ответов и логирование, хотя сам фреймворк уже умеет это делать из коробки.

### Подход Б: Использование Guardrails Server + SDK (Индустриальный стандарт)
Guardrails AI умеет запускаться как **готовый стандартизированный микросервис** (Guardrails Server, пакет `guardrails-api`). Вы не пишете код на FastAPI вручную. Вы просто описываете правила защиты (Guards) в конфигурационном файле `config.py`, а сам контейнер запускается официальным сервером. Для общения с этим контейнером ИИ-Агент использует HTTP REST (SDK есть только для Python/TypeScript; Go-агент использует стандартный JSON).

* **Плюсы:**
  * **Разделение ответственности:** Изменения в политиках безопасности (например, замена порогового значения чувствительности PII) происходят в конфигурационном файле шлюза и вообще не требуют изменения кода ИИ-Агента.
  * **Стандартный контракт:** Сервер сам предоставляет `/health-check`, `/guards` и `/guards/{id}/validate`, поэтому агенту не нужно поддерживать собственные FastAPI-роуты.
  * **Универсальность периметра:** ИИ-Агент полностью разгружен от задач токенизации и верификации.

> В этом стенде у стандартного `DetectPII` есть важная особенность: при вызове
> без общих engine он создаёт новый `AnalyzerEngine`/`AnonymizerEngine` на каждый
> запрос и теряет ~300 MiB. Поэтому `security-wall/config.py` подменяет
> фабрики на общие экземпляры; без этого wall упирался в лимит памяти и
> output guard висел дольше таймаута.

---

## 2. Сводная таблица отличий для стенда Dz5

| Критерий | Самописный REST-контейнер (FastAPI) | Guardrails Server + SDK (Рекомендуемый) |
| :--- | :--- | :--- |
| **Добавление нового валидатора** | Править код `main.py`, менять логику эндпоинта. | Добавить Guard в `config.py`; сервер сам зарегистрирует его по `id`. |
| **Код на стороне ИИ-Агента** | Самописный JSON-контракт и роуты. | Стандартный `POST /guards/{id}/validate`. |
| **Контракт ответа** | Нужно проектировать самостоятельно. | `validationPassed`, `validatedOutput`, `rawLlmOutput` и `validationSummaries`. |
| **Эксплуатация** | Требует ручной настройки воркеров Uvicorn и логирования. | Официальный entry point, healthcheck и метрики; версии зависимостей нужно фиксировать. |

---

## 3. Практическое руководство по реализации схемы Server + SDK

Вот как правильно переписать файлы вашего стенда, чтобы уйти от «набора библиотек» к чистой архитектуре распределенных систем.

### Шаг 1. Конфигурация шлюза (`security-wall/config.py`)
Вместо написания роутов FastAPI, мы просто регистрируем наши «рельсы» защиты в специальном файле конфигурации, который сервер считает автоматически:

```python
import guardrails_ai.detect_pii.main as _detect_pii
from guardrails import Guard, OnFailAction
from guardrails_ai.detect_pii import DetectPII
from guardrails_ai.detect_prompt_injection import DetectPromptInjection

# Presidio по умолчанию создаёт engine на каждый вызов. Общие экземпляры
# убирают утечку ~300 MiB на validate и ускоряют повторные проверки.
_shared_analyzer = _detect_pii.AnalyzerEngine()
_shared_anonymizer = _detect_pii.AnonymizerEngine()
_detect_pii.AnalyzerEngine = lambda: _shared_analyzer
_detect_pii.AnonymizerEngine = lambda: _shared_anonymizer

# Входной контур: prompt injection / jailbreak. id обязателен: сервер
# использует его в URL /guards/{id}/validate. NOOP сохраняет HTTP 200
# с validationPassed=false, чтобы агент единообразно применил fail-closed.
input_guard = Guard(id="input-defense", name="input-defense").use(
    DetectPromptInjection(check_vector=False, on_fail=OnFailAction.NOOP)
)

# Write-контур: тот же встроенный детектор применяется к SQL, который агент
# собирается выполнить при ALLOW_WRITES=on. Проверяются только не-SELECT.
write_guard = Guard(id="write-defense", name="write-defense").use(
    DetectPromptInjection(check_vector=False, on_fail=OnFailAction.NOOP)
)

# Выходной контур: PII маскируется FIX. PERSON намеренно исключён:
# spaCy NER на тексте с именами в этом стенде уходит в >500% CPU.
output_guard = Guard(id="output-privacy", name="output-privacy").use(
    DetectPII(
        pii_entities=["EMAIL_ADDRESS", "PHONE_NUMBER", "CREDIT_CARD"],
        on_fail=OnFailAction.FIX,
    )
)
```

Имена `input-defense`, `write-defense` и `output-privacy` — контракт с
агентом; они же задаются в `INPUT_GUARD_NAME` / `WRITE_GUARD_NAME` /
`OUTPUT_GUARD_NAME`. Выходной guard
возвращает `validationPassed=true` и замаскированный `validatedOutput`,
поэтому агент сравнивает его с `rawLlmOutput`.

### Шаг 2. Обновление `security-wall/Dockerfile`
Мы меняем команду запуска. Вместо ручного запуска `main.py`, запускаем официальный сервер Guardrails и передаём ему конфигурацию через `GR_CONFIG_FILE_PATH`:

```dockerfile
FROM python:3.12-slim
WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && pip check

# Заранее скачиваем NLTK/Presidio-ассеты, чтобы они не грузились на запрос.
RUN python -m guardrails_ai.detect_pii.post_install

COPY config.py .

# Guardrails по умолчанию отправляет Hub-телеметрию; в офлайн-лаборатории
# exporter зависает на DNS, поэтому отключаем его.
RUN printf 'enable_metrics=false\n' > /root/.guardrailsrc
ENV OTEL_SDK_DISABLED=true
ENV OTEL_TRACES_EXPORTER=none

ENV GR_CONFIG_FILE_PATH=/app/config.py
ENV GUARDRAILS_LOG_LEVEL=INFO

EXPOSE 8000

# CLI `guardrails-api start` не принимает --host и слушает 127.0.0.1.
# Официальный factory-запуск даёт 0.0.0.0 и доступен из других контейнеров.
CMD ["uvicorn", "--factory", "guardrails_api.app:create_app", \
     "--host", "0.0.0.0", "--port", "8000"]
```

### Шаг 3. Интеграция с ИИ-Агентом на Go (Golang)
Поскольку официальный SDK от Guardrails AI написан под Python и TypeScript, ваш Go-агент общается с контейнером защиты по универсальному протоколу HTTP REST API с использованием стандартных Go-структур.

Актуальная реализация находится в `agent/internal/agentcore/wall.go`. Клиент
не использует SDK и отправляет стандартный JSON:

```go
package agentcore

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/url"
	"time"
)

var ErrWallUnavailable = errors.New("external wall unavailable")

type WallClient struct {
	base   string
	client *http.Client
}

type WallResult struct {
	Passed    bool
	Validated string
	Raw       string
}

type wallRequest struct {
	LLMOutput string `json:"llm_output"`
}

type wallResponse struct {
	ValidationPassed bool   `json:"validationPassed"`
	ValidatedOutput  string `json:"validatedOutput"`
	RawLlmOutput     string `json:"rawLlmOutput"`
}

func NewWallClient(base string, timeout time.Duration) *WallClient {
	return &WallClient{base: base, client: &http.Client{Timeout: timeout}}
}

func (w *WallClient) Validate(ctx context.Context, guard, text string) (WallResult, error) {
	body, err := json.Marshal(wallRequest{LLMOutput: text})
	if err != nil {
		return WallResult{}, fmt.Errorf("%w: encode request: %v", ErrWallUnavailable, err)
	}
	endpoint := fmt.Sprintf("%s/guards/%s/validate", w.base, url.PathEscape(guard))
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, endpoint, bytes.NewReader(body))
	if err != nil {
		return WallResult{}, fmt.Errorf("%w: build request: %v", ErrWallUnavailable, err)
	}
	req.Header.Set("Content-Type", "application/json")

	resp, err := w.client.Do(req)
	if err != nil {
		return WallResult{}, fmt.Errorf("%w: %v", ErrWallUnavailable, err)
	}
	defer resp.Body.Close()

	// 4xx — это policy block, а не сбой стены.
	if resp.StatusCode >= 400 && resp.StatusCode < 500 {
		return WallResult{Passed: false, Validated: text, Raw: text}, nil
	}
	if resp.StatusCode != http.StatusOK {
		return WallResult{}, fmt.Errorf("%w: status %s", ErrWallUnavailable, resp.Status)
	}

	var out wallResponse
	if err := json.NewDecoder(resp.Body).Decode(&out); err != nil {
		return WallResult{}, fmt.Errorf("%w: decode response: %v", ErrWallUnavailable, err)
	}
	raw, validated := out.RawLlmOutput, out.ValidatedOutput
	if raw == "" && validated == "" {
		raw, validated = text, text
	}
	return WallResult{Passed: out.ValidationPassed, Validated: validated, Raw: raw}, nil
}
```

Оркестрация в агенте:

- вход: `Validate(ctx, INPUT_GUARD_NAME, userMessage)`; `Passed=false` → `403`,
  ошибка стены → `503`, LLM не вызывать;
- запись: при `EXTERNAL_GUARD=on` и `ALLOW_WRITES=on` перед выполнением
  не-`SELECT` SQL — `Validate(ctx, WRITE_GUARD_NAME, stmt)`; `Passed=false` →
  `403`, SQL не выполнять; ошибка стены/таймаут → `503` (fail-closed);
- выход: `Validate(ctx, OUTPUT_GUARD_NAME, reply)`; `Passed=false` или
  `Validated != Raw` → блокировка либо маска по `BLOCK_ON_FAIL`; ошибка
  стены/таймаут → `503`, исходный ответ не отдавать;
- сервер не проксирует LLM-трафик: агент сам вызывает Ollama, а wall — только
  отдельная валидация.

Важно: для выходного guard'а `on_fail=fix` (PII) сервер возвращает
`validationPassed=true` и уже замаскированный `validatedOutput`. Поэтому
агент сравнивает `validatedOutput` с `rawLlmOutput`; различие означает
срабатывание PII-фильтра. HTTP 4xx — policy block, а 5xx/таймаут —
fail-closed `503`, а не «пропустить без проверки».

`write-defense` проверяет всю SQL-строку как текст. Он ловит явные инструкции
в `INSERT`/`UPDATE`, но не разбирает SQL и не проверяет значения полей;
обфускация и SQL-инъекция могут пройти. Это дополнительный слой, а не замена
read-only/allow-list.

### Шаг 4. Compose и переменные окружения

Сервис `wall` и параметры агента задаются в `infra/compose.yaml`:

```yaml
wall:
  build:
    context: ../security-wall
  environment:
    GR_CONFIG_FILE_PATH: /app/config.py
    SELF_ENDPOINT: http://wall:8000
    GUARDRAILS_LOG_LEVEL: INFO
  deploy:
    resources:
      limits:
        memory: 2048M
  healthcheck:
    test: ["CMD-SHELL", "python -c \"import urllib.request; urllib.request.urlopen('http://localhost:8000/health-check', timeout=3)\""]
```

Агент получает адрес и имена guards через env:

| Переменная | Значение в compose | Назначение |
|------------|--------------------|------------|
| `EXTERNAL_GUARD` | `off` / `on` | Включает внешний контур |
| `GUARDRAILS_BASE_URL` | `http://wall:8000` | Адрес Guardrails Server |
| `INPUT_GUARD_NAME` | `input-defense` | Guard для входа |
| `WRITE_GUARD_NAME` | `write-defense` | Guard для write-SQL перед выполнением |
| `OUTPUT_GUARD_NAME` | `output-privacy` | Guard для выхода |
| `GUARDRAILS_TIMEOUT` | `60s` | Таймаут HTTP-запроса к стене |
| `BLOCK_ON_FAIL` | `on` | Блокировать при нарушении выхода |

Wall опубликован на `localhost:8000` только для отладки; контейнеры ходят
друг к другу по имени `wall:8000` в сети `ib-network`. Лимит `2048M` выбран
под общие Presidio-движки: RSS стены ~0.9–1.3 GiB.

---

## 4. Архитектурное обоснование для методички (Связка Go + Python)

> *«Использование гетерогенного стека (ИИ-Агент на **Go** + Шлюз безопасности на **Python**) демонстрирует классический паттерн микросервисной изоляции. Компилируемый язык Go обеспечивает максимальную пропускную способность, низкое потребление памяти и эффективную параллельную обработку бизнес-логики агента и вызовов инструментов (Tool Calling). В то же время тяжелые задачи лингвистического анализа, токенизации и инференса локальных моделей классификации угроз (Prompt Injection, Microsoft Presidio PII) делегируются специализированному серверу Guardrails AI, развернутому в изолированном контейнере. Взаимодействие между компонентами осуществляется по слабосвязанному протоколу HTTP REST API, что исключает взаимовлияние сред выполнения (Runtimes) и позволяет масштабировать контур безопасности независимо от бизнес-логики».*
