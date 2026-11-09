# Архитектура выпускного проекта

## Модули

### 1. Generator Module (на базе DZ2)
- Вход: файлы требований в формате BR-N
- Process: загрузка → нормализация → сбор промпта → Ollama chat → валидация → render
- Вывод: 5 типов артефактов (markdown) + JSON с данными кейсов
- Зависимости: `ai-testcase-generator` (path на `../dz2`), Ollama client

### 2. Quality Assessment Module (расширение DZ8)
- Readability: Flesch Reading Ease, avg sentence length
- Clarity of goals: Pattern analysis for measurable objectives
- Scenario coverage: Mapping BR-N → generated TC, coverage %
- Consistency: Сравнение Multiple generations, терминологическая стабильность
- Вывод: JSON с метриками per scenario + summary

### 3. Workflow Pipeline
- Sequence: generate → assess → visualize → report
- CLI: `ai-testgen-graduation` с флагами --generate, --assess, --visualize, --report
- Streamlit: вкладка оценки качества в working place Dz2

### 4. Output Format
- JSON: `{"scenarios": [{"id": "TC-01", "description": "...", "quality": {...}]}`
- Markdown report: specified format с general info, quality distribution, scenario table
- Charts: matplotlib/seaborn radar chart, bar charts per metric