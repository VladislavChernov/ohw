# Задачи

Задача считается закрытой, когда подтверждена гейтом, а не когда выглядит сделанной.

## Запись (до кода)

- [x] ADR-065: раздел «Пересмотр матрицы скриптов (решение владельца, 2026-10-19)» —
      прежняя запись процитирована, новая матрица названа, отмена флага обоснована
- [x] `docs/prototype_requirements.md` §3.3: таблица платформ заменена; инструкция для
      Windows на процессоре снята (есть скрипт); причина отсутствия GPU-скрипта на Unix
- [x] `Demo/README.md`: таблица файлов и абзац про отсутствие флага
- [x] `README-deployment.md` §3.1: таблица вариантов по трём платформам
- [x] `docs/infrastructure_stack.md` §4.1: та же таблица

## Скрипты

- [x] Завести `Demo/ingest-cpu.ps1` на основе `ingest-cpu.sh`: runtime + ingest, без
      GPU-оверлея, `LLM_NGPU_LAYERS=0`, набор команд как у `ingest.ps1`
- [x] `Demo/ingest.ps1`: убрать `[switch]$Cpu`, условную ветку и упоминания в usage
- [x] `Demo/ingest.sh`: убрать GPU-оверлей, поставить `LLM_NGPU_LAYERS=0`, переписать шапку
- [x] `Demo/demo.sh`: убрать GPU-оверлей, поставить `LLM_NGPU_LAYERS=0`, переписать шапку
- [x] Удалить `Demo/demo-cpu.sh` и `Demo/ingest-cpu.sh`
- [x] `Demo/demo-cpu.ps1`: подсказка называет `ingest-cpu.ps1`, не `ingest.ps1`
- [x] `Demo/demo.ps1`: подсказка называет `ingest.ps1` (видеокарта) — проверить, что парность
      сходится с матрицей
- [x] `README-deployment.md:269`: `demo.sh load` → `ingest.sh load --path`

## Гарды

- [x] `test_demo_scripts_parity.py`: константы под новую матрицу (`PS1_INGEST_CPU` существует,
      `SH_CPU`/`SH_INGEST_CPU` — нет)
- [x] Гард на флаг перебирает и `.ps1` (сегодня — только `.sh`)
- [x] Гард на матрицу: набор файлов на диске равен объявленному (Unix без `-cpu`, Windows с
      `-cpu.ps1`); падает с именами лишних и недостающих файлов
- [x] Гард парности: CPU-стенд в подсказке называет CPU-ингест, GPU-стенд — GPU-ингест
- [x] Поведенческий гард с подменой `docker`: `ingest-cpu.ps1` без GPU-оверлея,
      `ingest.ps1` с ним; `demo.sh`/`ingest.sh` без оверлея

## Проверка

- [x] `pytest prototype/tests/test_demo_scripts_parity.py`
- [x] `gate.sh full` — весь гейт перед коммитом
