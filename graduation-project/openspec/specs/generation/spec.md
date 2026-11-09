# Specification: Test Artifact Generation

## EARS Requirements

### SHALL requirements
- **SHALL** генерировать 5 типов артефактов из requirement файлов: testcases, checklist, testplan, qareport, coveragematrix
- **SHALL** использовать внешние шаблоны промптов из `dz7/templates/<type>.txt` (не hardcoded в code)
- **SHALL** поддерживать normalization требований через `--normalize` флаг DZ2
- **SHALL** валидировать ответ Ollama по schema и retry при неудаче (max_retries из config)
- **SHALL**.render markdown отчеты через renderer DZ2 service
- **SHALL** генерировать boundary values в тест-кейсах при наличии границ в требованиях
- **SHALL** поддерживать `--url` аргумент для указания URL тестируемого сайта

### WHEN/THEN scenarios
- **WHEN** подставляются плейсхолдеры `{url}`, `{count}`, `{positive}`, `{negative}`, `{requirement_rule}`, `{content}`, `{extra}`, `{testcases}`
- **THEN** Generated artifact сохраняется в `output/<stem>.md`
- **WHEN** URL неизвестен, строка `Target site: {url}` удаляется completely
- **THEN** `{testcases}` в coveragematrix шаблоне превращается в список real TC или "No test cases have been generated..."