# Proposal: Proposal Review Gate — фаза аналитики предложений

## Почему

`Proposals/` содержит идеи и направления развития, но не является нормативным контрактом
системы. Предложения нужно сравнивать с текущим runtime, `CONCEPT.md`, `docs/`, активными
OpenSpec-бандлами, eval-артефактами и ограничениями эксплуатации. Без отдельной фазы
идеи могут незаметно превратиться в обязательные требования или быть отвергнуты без
зафиксированной причины.

Нужна промежуточная аналитическая веха: промежуточные предложения рассматриваются, после
чего принимается решение о полном включении, частичном включении, отложении или отказе.

## Что делаем

- Вводим единый Proposal Review Gate для каталога `Proposals/`.
- Определяем вход, критерии анализа, формат review-record и допустимые решения.
- Привязываем gate к завершению текущего lightweight baseline и перед `M6-Growth /
  pre-connectors`; повторяем review после архитектурно значимых вех.
- Проводим первый review предложения `graph_as_data_map_manifest.md` без автоматического
  принятия его claims.
- Accepted-часть идеи оформляем новым OpenSpec change; rejected/deferred-часть сохраняем
  с причинами и следующими условиями.
- Запрещаем реализацию и изменение нормативных документов до решения review-record.

## Спека

- `design.md` — процесс, статусы, роли и артефакты фазы.
- `spec.md` — сценарии submitted/review/accepted/partial/deferred/rejected и периодичность.
- Review-record содержит evidence, ограничения, риски, решение и follow-up change.
- `graph_as_data_map_manifest.md` проходит review как Data Map proposal, а не как готовая
  спецификация: отдельно оцениваются metadata-only mode, online expansion experiment и
  future background mining.

## Проверка

1. Провести walkthrough proposal → evidence matrix → decision record без изменения кода.
2. Проверить, что решение явно различает `accepted`, `partial`, `deferred` и `rejected`.
3. Проверить, что ни одно предложение не считается частью текущего контракта без
   соответствующего review-record и follow-up OpenSpec.
4. На первом review зафиксировать, какие утверждения `graph_as_data_map_manifest.md`
   подтверждены, какие требуют измерения и какие отклонены.
