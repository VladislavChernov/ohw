# Глоссарий параметров отчёта

Справочник по ключам, которые встречаются в `report.json` и в разделе
**«Сводка»** файла `report.md`. Ключи сгруппированы по типу проверки.
Формулы и пороги — в конце документа.

> Технические имена ключей намеренно оставлены как в отчёте: по ним удобно
> искать в JSON и писать запросы к CI.

---

## 1. Общие поля отчёта

| Поле | Что означает |
|---|---|
| `schema_version` | версия схемы отчёта; сейчас `1`. Отчёт с другой версией не читается |
| `run_id` | идентификатор запуска, `YYYYMMDDTHHMMSSZ-<hex>`; совпадает с именем каталога |
| `kind` | тип проверки: `api`, `hallucinations`, `consistency`, `canary`, `degradation` |
| `started_at` / `finished_at` | время начала и окончания в UTC |
| `environment.base_url` | адрес Ollama |
| `environment.model` | модель, на которой выполнялся запуск |
| `environment.digest` | digest модели из `/api/tags` |
| `environment.ollama_version` | версия Ollama |
| `environment.temperature` / `seed` / `num_ctx` / `num_predict` / `max_retries` | фактические параметры запуска |
| `input.path` | путь к входному документу |
| `input.encoding` | кодировка входа (`utf-8`, `cp1251`, …) |
| `input.url` | URL, извлечённый из документа |
| `input.br_ids` | список требований `BR-N` во входе |
| `input.source_sha256` / `text_sha256` | хэши исходника и нормализованного текста |
| `prompt.artifact_type` | тип артефакта: `testcases`, `qareport`, `coveragematrix` |
| `prompt.count` | запрошенное число тест-кейсов |
| `prompt.prompt_sha256` | хэш фактического промпта |
| `verdict.status` | итоговый вердикт |
| `verdict.reasons` | человекочитаемые причины `fail`/`warn`/`regressed` |
| `verdict.thresholds` | пороги, применённые в этом запуске |
| `warnings` | предупреждения нормализатора и входа |
| `runs[]` | массив записей о прогонах (для серий) |
| `summary` | агрегированные метрики; это и есть «Сводка» |
| `baseline` / `candidate` | сводки двух ячеек в деградации |
| `comparison` | сравнение baseline и candidate |

### Прогон (`runs[]`)

| Поле | Что означает |
|---|---|
| `index` | номер прогона, с 1 |
| `cell` | ячейка в деградации (`baseline` / `candidate`), иначе `null` |
| `latency_ms` | длительность запроса, мс |
| `attempts` | сколько попыток понадобилось валидатору |
| `raw_sha256` | SHA-256 сырого ответа |
| `raw_chars` | длина сырого ответа |
| `validator_pass` | прошёл ли артефакт валидатор Dz2 |
| `error` | текст ошибки, если прогон не удался |
| `metrics` | метрики артефакта (см. ниже) |

### Метрики артефакта (`runs[].metrics`)

| Поле | Что означает |
|---|---|
| `json_valid` | ответ — валидный JSON |
| `schema_valid` | JSON соответствует схеме артефакта |
| `validator_pass` | прошёл валидатор |
| `validator_issues` | список замечаний валидатора |
| `cases_total` | число тест-кейсов |
| `positive` / `negative` | число позитивных и негативных кейсов |
| `negative_ratio` | `negative / cases_total` |
| `count_matches` | `cases_total == count` |
| `tc_ids` | идентификаторы тест-кейсов |
| `tc_duplicates` | повторяющиеся `TC-*` |
| `br_ids_source` | `BR-N` во входе |
| `br_ids_output` | `BR-N`, упомянутые в артефакте |
| `br_covered` / `br_missing` / `br_invented` | покрытые, пропущенные и выдуманные требования |
| `br_coverage` | доля покрытых `BR` |
| `urls_output` / `urls_invented` | URL в артефакте и выдуманные URL |
| `claims_execution_results` | артефакт утверждает, что тесты уже выполнены |
| `claims_markers` | слова-маркеры таких утверждений |
| `section_headings` | заголовки разделов |
| `title_tokens` | токены заголовка |
| `required_sections_present` / `required_sections_missing` | обязательные разделы |
| `empty_sections` | пустые разделы |
| `truncated` | ответ обрезан по `num_predict` |
| `token_count` | число токенов ответа |
| `attempts` | число попыток |

---

## 2. Стенд

Вкладка «Стенд» не пишет `report.json`; она возвращает объект health.

| Поле | Что означает |
|---|---|
| `ok` | `true`, если Ollama ответила и модель найдена |
| `base_url` | проверенный адрес |
| `ollama_version` | версия из `/api/version` |
| `model` | запрошенная модель |
| `digest` | digest найденной модели |
| `models` | все имена из `/api/tags` |
| `command_hint` | подсказка, если модель не найдена |
| `error` | текст ошибки, если `ok=false` |

---

## 3. API

`summary` содержит единственный ключ `checks` — массив проверок
API-01…API-12.

| Поле проверки | Что означает |
|---|---|
| `id` | `API-01` … `API-12` |
| `name` | короткое название проверки |
| `status` | `pass`, `warn` или `fail` |
| `http_status` | HTTP-код ответа; `null`, если запрос не дошёл |
| `latency_ms` | длительность запроса |
| `detail` | пояснение или текст ошибки |
| `raw` | сырое тело ответа (если сохранялось) |
| `critical` | `true`, если провал делает вердикт `fail` |

---

## 4. Галлюцинации

| Ключ | Что означает |
|---|---|
| `probes_total` | число probe-ов в наборе |
| `failed_probes` | число probe-ов, где хотя бы один прогон провалился |
| `hallucination_rate` | `failed_probes / probes_total` |
| `failed_runs_total` | сумма проваленных прогонов по всем probe-ам |
| `runs_total` | `probes_total × runs` |
| `critical_failures` | ID критичных probe-ов, которые провалились |
| `strict` | включён ли строгий режим |
| `hallucination_rate_max` | допустимый `hallucination_rate`; в strict — `0.0` |
| `probes` | массив результатов по каждому probe-у |

### Элемент `probes[]`

| Поле | Что означает |
|---|---|
| `id` | `H-01` … `H-08` |
| `kind` | тип probe-а: `count`, `missing_feature`, `missing_url`, `traceability`, `execution_claims`, `fake_tc`, `numeric` |
| `status` | `pass` / `fail` |
| `critical` | критичен ли probe (или включён `strict`) |
| `evidence` | цитата ответа до 200 символов |
| `forbidden_hits` | найденные запрещённые слова/URL/ID |
| `details` | пояснения проверки |
| `runs` | сколько раз запускался probe |
| `failed_runs` | сколько прогонов провалилось |

---

## 5. Согласованность

Серия одинаковых прогонов; чем ближе метрики к `1.0`, тем стабильнее модель.

| Ключ | Что означает | Формула / трактовка |
|---|---|---|
| `runs` | число прогонов | — |
| `pass_at_1` | доля прогонов, прошедших валидатор **с первой попытки** | `validator_pass and attempts == 1` |
| `pass_all` | доля прогонов, прошедших валидатор | `validator_pass` |
| `validator_pass_rate` | то же, что `pass_all` | — |
| `valid_json_rate` | доля прогонов с валидным JSON | `json_valid` |
| `count_match_rate` | доля прогонов, где число кейсов совпало с `count` | `cases_total == count` |
| `balance_match_rate` | доля сбалансированных прогонов | см. формулу ниже |
| `br_coverage_mean` | среднее покрытие `BR` | `mean(br_coverage)` |
| `br_coverage_min` | худшее покрытие `BR` | `min(br_coverage)` |
| `invented_br_total` | сумма выдуманных `BR-N` по всем прогонам | `sum(len(br_invented))` |
| `invented_url_total` | сумма выдуманных URL | `sum(len(urls_invented))` |
| `tc_jaccard_mean` | среднее попарное сходство множеств `TC-*` | Jaccard по `tc_ids` |
| `tc_jaccard_min` | худшее попарное сходство `TC-*` | минимум по парам |
| `heading_jaccard_mean` | среднее сходство наборов заголовков | Jaccard по `section_headings` |
| `heading_jaccard_min` | худшее сходство заголовков | минимум по парам |
| `title_jaccard_mean` | среднее сходство токенов заголовка | Jaccard по `title_tokens` |
| `identical_runs` | число **пар** прогонов с одинаковым `raw_sha256` | `Σ C(n, 2)` по группам одинаковых хэшей |
| `unique_artifacts` | число уникальных `raw_sha256` | `len(set(hashes))` |
| `mean_attempts` | среднее число попыток | `mean(attempts)` |
| `mean_latency_ms` | средняя задержка | `mean(latency_ms)` |
| `self_consistency` | интегральная стабильность | среднее из `br_coverage_mean`, `heading_jaccard_mean` и (для testcases) `tc_jaccard_mean` |

Важно: `identical_runs` — это **пары**, а не число прогонов. Например, 10
прогонов двумя группами по 5 дают `2 × C(5,2) = 20`.

---

## 6. Канарейка

| Ключ | Что означает |
|---|---|
| `runs` | число прогонов канарейки |
| `canary` | результат одного прогона (dict) или список при `runs > 1` |
| `results` | всегда список результатов |

### Результат канарейки

| Поле | Что означает |
|---|---|
| `verdict` | `pass` / `warn` / `fail` |
| `parse_error` | почему не удалось разобрать ответ модели |
| `ids_exact` | набор и порядок `BR-N` совпали с эталоном |
| `count_exact` | число требований совпало |
| `url_exact` | URL совпал после нормализации |
| `title_overlap` | Jaccard токенов заголовка |
| `body_overlap_mean` | среднее сходство текстов требований |
| `body_overlap_min` | худшее сходство текста требования |
| `extra_ids` | ID, которых нет в эталоне |
| `missing_ids` | ID, которые модель потеряла |
| `llm_canonical` | первые 300 символов ответа модели |
| `det_canonical` | первые 300 символов эталона |
| `raw` | полный сырой ответ модели |

Вердикт серии — худший из вердиктов прогонов.

---

## 7. Деградация

`summary` отчёта деградации — это сводка **candidate** (те же ключи, что в
разделе 5). Отдельно в `comparison` лежит сравнение ячеек.

| Ключ `comparison` | Что означает |
|---|---|
| `pass_at_1` | сравнение `pass_at_1`: `baseline`, `candidate`, `diff`, `ci_baseline`, `ci_candidate`, `overlap` |
| `invented_br_total` | сравнение числа выдуманных требований |
| `br_coverage_mean` | сравнение среднего покрытия |
| `valid_json_rate` | сравнение доли валидного JSON |
| `mean_attempts` | сравнение среднего числа попыток |
| `effect_size_h` | размер эффекта Cohen's h для `pass_at_1` |
| `relative_diff` | `diff / baseline_pass_at_1` |
| `bootstrap_pass_at_1` | bootstrap-интервалы для `pass_at_1` обеих ячеек |

### Поля `pass_at_1`

| Поле | Что означает |
|---|---|
| `baseline` / `candidate` | доля успехов с первой попытки |
| `diff` | `candidate − baseline` |
| `ci_baseline` / `ci_candidate` | 95 % интервал Уилсона |
| `overlap` | `true`, если интервалы пересекаются |

---

## 8. Пороговые значения

Значения по умолчанию из `config.toml` (`[thresholds]`).

| Ключ | По умолчанию | Где применяется |
|---|---|---|
| `hallucination_rate_max` | `0.25` | галлюцинации |
| `validator_pass_rate_min` | `0.90` | согласованность |
| `count_match_rate_min` | `1.0` | согласованность, testcases |
| `br_coverage_min` | `0.80` | согласованность |
| `tc_jaccard_mean_min` | `0.40` | согласованность, testcases |
| `heading_jaccard_mean_min` | `0.70` | объявлен, но в вердикте не участвует |
| `canary_title_overlap_min` | `0.60` | канарейка |
| `canary_body_overlap_mean_min` | `0.85` | канарейка |
| `canary_body_overlap_min` | `0.50` | канарейка |
| `degradation_pass_drop_max` | `0.20` | деградация |
| `degradation_br_drop_max` | `0.10` | деградация |
| `degradation_json_drop_max` | `0.20` | деградация |
| `degradation_attempts_increase_max` | `1.0` | деградация |

Дополнительно:

- канарейка ставит `warn`, если все пороги пройдены, но
  `body_overlap_mean < 0.95`;
- согласованность ставит `warn`, если `temperature == 0.0`, но
  `identical_runs == 0`;
- деградация требует **≥ 5 прогонов** в каждой ячейке, иначе
  `insufficient_data`.

---

## 9. Формулы

### Jaccard

```
J(A, B) = |A ∩ B| / |A ∪ B|
```

Если оба множества пусты, сходство считается равным `1.0`. Токены
приводятся к нижнему регистру; URL нормализуется (убираются пробелы,
хвостовая пунктуация, якорь `#…`, завершающий `/`).

### Покрытие требований

```
br_coverage = |br_covered| / |br_ids_source|
```

Если во входе нет `BR`, покрытие равно `1.0`.

### Сбалансированность (`balance_match_rate`)

Прогон считается сбалансированным, если:

- `cases_total > 0`;
- при `count >= 2` есть хотя бы один `positive` и один `negative`;
- при `count >= 5` негативных не меньше `ceil(count / 3)`.

### Интервал Уилсона

95 % интервал для доли успехов:

```
center = (p + z²/(2n)) / (1 + z²/n)
half   = z·sqrt(p(1−p)/n + z²/(4n²)) / (1 + z²/n)
CI     = center ± half,  z = 1.96
```

### Cohen's h

```
h = 2·asin(sqrt(p_candidate)) − 2·asin(sqrt(p_baseline))
```

Ориентир: `|h| ≈ 0.2` — малый эффект, `0.5` — средний, `0.8` — большой.

### Bootstrap

`1000` ресемплов, фиксированный seed `12345`; возвращает 2,5 % и 97,5 %
перцентили распределения `pass_at_1`.

---

## 10. Что читать в первую очередь

1. `verdict.status` и `verdict.reasons`.
2. `summary.runs` — достаточно ли данных.
3. Ключевую метрику проверки:
   - галлюцинации — `hallucination_rate`;
   - согласованность — `validator_pass_rate`, `br_coverage_min`;
   - канарейка — `ids_exact`, `body_overlap_mean`;
   - деградация — `comparison.pass_at_1.diff` и `overlap`.
4. `warnings` — там бывают важные оговорки о входе и промпте.
