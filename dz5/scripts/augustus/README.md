# Augustus для Dz5 (Apache 2.0)

Augustus — Go-фреймворк для red-team LLM
(https://github.com/praetorian-inc/augustus): 240+ probes, 130+ detectors,
48 providers, один бинарник. В Dz5 он атакует **агента**, а не Ollama
напрямую.

Полный новичковый гайд: `docs/AUGUSTUS_QUICKSTART.md`.
Разбор текущих probes и создание своих: `docs/AUGUSTUS_ATTACKS.md`.

## Куда целится

`augustus.config.yaml` использует `rest.Rest`:

```yaml
generators:
  rest.Rest:
    uri: "http://localhost:8080/chat"
    method: "POST"
    req_template_json_object:
      message: "$INPUT"
    response_json: true
    response_json_field: "$.reply"
    request_timeout: 180
    skip_codes: [403]
```

`request_timeout` важен: агент ждёт Ollama, а `rest.Rest` по умолчанию рвёт
запрос через 20 секунд. `skip_codes: [403]` превращает блокировку fixed-агента
в пустой ответ (`SAFE`), а не в ошибку Augustus.

Это контракт агента Dz5: `POST /chat {"message": "..."}` →
`{"reply": "...", "tool_calls": [...]}`. Для preset 5 target —
`http://localhost:8081/chat` (`agent-fixed`).

## Ручной первый прогон

Нужен Go 1.27+ и установленный Augustus. Версию лучше пинить, а не ставить
`@latest`:

```bash
go install github.com/praetorian-inc/augustus/cmd/augustus@<version>
augustus list
```

В upstream v0.14.34 `augustus version` печатает зашитое `0.0.9`; в образе
`attack-runner` версия проставляется через `-ldflags` и показывает `0.14.34`.

Один probe по агенту:

```bash
augustus scan rest.Rest \
  --config-file scripts/augustus/augustus.config.yaml \
  --probe dan.DAN_Jailbreak \
  --verbose
```

Несколько probes:

```bash
augustus scan rest.Rest \
  --config-file scripts/augustus/augustus.config.yaml \
  --probe dan.DAN_Jailbreak \
  --probe latentinjection.LatentJailbreak \
  --probe goodside.SystemPromptConfusion
```

## Запуск через attack-runner

```powershell
docker compose -f infra/compose.yaml --profile attack run --rm --no-deps `
  --entrypoint python attack-runner `
  -m attack_runner run --target http://agent:8080 --preset 1 --runs 3 --timeout 120 `
  --augustus on
```

Runner сам подставляет target в `uri`, пишет `reports/<run-id>/augustus/`:
`config.yaml`, `results.jsonl`, `report.html`, `stdout.txt`, `stderr.txt`,
`db-before.json`, `db-after.json`. Список probes задаётся `AUGUSTUS_PROBES`
(CSV). HTML-отчёт и настройки прогона доступны в UI: `http://localhost:8090/augustus`.

## Что Augustus видит, а что нет

- Видит: ответ агента через `$.reply`, отказы, утечки в тексте, поведение
  модели.
- Не видит: `tool_calls` и внутреннюю SQL-политику, если они не попали в
  `reply`.
- `db-before/after` в `augustus/` — грубый DB-oracle на весь прогон, а не
  вердикт по каждому probe.
- Точные проверки tool policy и БД делает `attack-runner` через свои YAML
  сценарии: `tool_calls`, `forbidden_sql`, DB snapshot.
- Поэтому `VULN` в Augustus — finding по probe, а не автоматический вердикт
  «агент уязвим»; `SAFE` — не доказательство защиты.

## License

Augustus: Apache 2.0 (Praetorian Security, Inc.).
