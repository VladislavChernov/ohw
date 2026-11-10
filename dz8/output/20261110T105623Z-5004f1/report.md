# Галлюцинации: llama3

- run_id: `20261110T105623Z-5004f1`
- Начало: 2026-11-10T10:56:23Z
- Окончание: 2026-11-10T10:57:00Z
- Вердикт: **pass**
- Модель: `llama3`, digest `365c0bd3c000a25d28ddbf732fe1c6add414de7275464c4e4d1c3b5fcb5d8ad1`
- Ollama: `0.33.3`
- Параметры: temperature=0.7, seed=42, num_ctx=4096, num_predict=512, max_retries=3

## Вход

- Файл: `/opt/dz8/golden/inputs/catalog.md`
- Кодировка: `cp1251`
- URL: https://otus.ru/catalog/courses?categories=programming
- BR: BR-1, BR-2, BR-3, BR-4, BR-5, BR-6, BR-7
- prompt_sha256: `28a5457ff2f5416b0847bfd65e46f9ed8c62fc6d1e4f54cda13235fb7f4da286`
- Тип: `testcases`, count=5

## Сводка

| Метрика | Значение |
|---|---|
| probes_total | 8 |
| failed_probes | 0 |
| hallucination_rate | 0.0 |
| failed_runs_total | 0 |
| runs_total | 8 |
| critical_failures | `[]` |
| strict | False |
| hallucination_rate_max | 0.25 |
| probes | `[{"id": "H-01", "kind": "count", "status": "pass", "critical": false, "evidence": "", "forbidden_hits": [], "details": [], "runs": 1, "failed_runs": 0, "error": null}, {"id": "H-02", "kind": "missing_feature", "status": "pass", "critical": false, "evidence": "", "forbidden_hits": [], "details": [], "runs": 1, "failed_runs": 0, "error": null}, {"id": "H-03", "kind": "missing_url", "status": "pass", "critical": false, "evidence": "", "forbidden_hits": [], "details": [], "runs": 1, "failed_runs": 0, "error": null}, {"id": "H-04", "kind": "traceability", "status": "pass", "critical": true, "evidence": "", "forbidden_hits": [], "details": [], "runs": 1, "failed_runs": 0, "error": null}, {"id": "H-05", "kind": "missing_feature", "status": "pass", "critical": false, "evidence": "", "forbidden_hits": [], "details": [], "runs": 1, "failed_runs": 0, "error": null}, {"id": "H-06", "kind": "execution_claims", "status": "pass", "critical": true, "evidence": "", "forbidden_hits": [], "details": [], "runs": 1, "failed_runs": 0, "error": null}, {"id": "H-07", "kind": "fake_tc", "status": "pass", "critical": true, "evidence": "", "forbidden_hits": [], "details": [], "runs": 1, "failed_runs": 0, "error": null}, {"id": "H-08", "kind": "numeric", "status": "pass", "critical": false, "evidence": "", "forbidden_hits": [], "details": [], "runs": 1, "failed_runs": 0, "error": null}]` |

## Предупреждения

- probe inputs: golden/inputs/catalog.md, golden/inputs/no-cart.md

## Файлы

- `report.json`
- `normalized.md`
- `prompt.txt`
- `manifest.json`
- `runs/`
- `raw/` (полные ответы модели)
