# Graduation Project

Automated Test Documentation Generation with LLM

This project integrates existing homeworks (DZ2, DZ7, DZ8) into a comprehensive graduation project.

## Structure
- `openspec/` - Mandatory OpenSpec specifications
- `src/` - Source code
- `scripts/` - Helper scripts
- `docs/` - Documentation
- `output/` - Generated results

## Quick Start

```bash
cd d:/Otus/ohw
python -m graduation_project \
  --generate \
  --assess \
  --visualize \
  --report \
  --input-dir dz2/docs \
  --type testcases \
  --count 10 \
  --normalize \
  --output-dir output
```

## Related Homeworks
- **DZ2**: Ядро генерации тест-кейсов и артефактов (5 типов)
- **DZ7**: Шаблоны промптов для 5 типов артефактов
- **DZ8**: Проверка качества генерации (галлюцинации, консистентность)
- **Infra**: Общий Ollama сервис и Docker окружение