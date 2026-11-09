# Specification: Quality Assessment

## EARS Requirements

### SHALL requirements
- **SHALL** рассчитывать метрику читаемости (Readability): формула на основе sentence length и syllable count
- **SHALL** рассчитывать метрику четкости целей (Clarity of goals):Pattern match для measurable objectives, "SHOULD"/"SHALL" structures
- **SHALL** рассчитывать метрику покрытия сценариев (Scenario coverage): % match requirement BR-N → generated TC
- **SHALL** рассчитывать метрику консистентности (Consistency): Jaccard similarity между multiple generations

### Quantified success criteria
- Readability score: 0.0 - 1.0 (1.0 = очень читаемо)
- Clarity score: 0.0 - 1.0 (1.0 = цели полностью измеримы)
- Coverage: 0-100% требований покрыто тестами
- Consistency: Jaccard index > 0.6 считается хорошей консистентностью

### WHEN/THEN scenarios
- **WHEN** генерируется 10 сценариев одного и того же промпта
- **THEN** консистентность рассчитывается как средний Jaccard index всех пар генераций
- **WHEN** читаемость ниже порога 0.5
- **THEN** система должна предложить упрощение языка вromptа
- **WHEN** покрытие требований ниже 30%
- **THEN** система должна предложить добавить больше BR-N требований в входной файл