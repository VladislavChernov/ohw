# Тест-кейсы: testcases_md.md

**Тестируемый сайт:** https://otus.ru/catalog/courses?categories=programming

**Всего кейсов:** 10 (позитивных: 6, негативных: 4)

## Позитивные сценарии

### TC-01. Catalog displays only programming courses

**Тип:** позитивный

**Проверяет требование:** BR-1

**Предусловия:**

- User opens the site in a web browser

**Шаги:**

| № | Действие |
|---|----------|
| 1 | Open https://otus.ru/catalog/courses?categories=programming |

**Ожидаемый результат:** Only courses under the 'Программирование' category are displayed

### TC-02. Course card displays required information

**Тип:** позитивный

**Проверяет требование:** BR-2

**Предусловия:**

- User opens the site in a web browser

**Шаги:**

| № | Действие |
|---|----------|
| 1 | Open https://otus.ru/catalog/courses?categories=programming |
| 2 | Select a course card from the list |

**Ожидаемый результат:** Course title, duration or start date, cost, and action button are displayed

### TC-03. Clicking on a course card opens the course page

**Тип:** позитивный

**Проверяет требование:** BR-3

**Предусловия:**

- User opens the site in a web browser

**Шаги:**

| № | Действие |
|---|----------|
| 1 | Open https://otus.ru/catalog/courses?categories=programming |
| 2 | Click on a course card |

**Ожидаемый результат:** The course page is opened without errors

### TC-04. Filters change the course list and resetting returns the original list

**Тип:** позитивный

**Проверяет требование:** BR-4

**Предусловия:**

- User opens the site in a web browser

**Шаги:**

| № | Действие |
|---|----------|
| 1 | Open https://otus.ru/catalog/courses?categories=programming |
| 2 | Apply a filter |
| 3 | Clear the filter |

**Ожидаемый результат:** The course list returns to the original state

### TC-05. Selected filter is visible in the URL

**Тип:** позитивный

**Проверяет требование:** BR-5

**Предусловия:**

- User opens the site in a web browser

**Шаги:**

| № | Действие |
|---|----------|
| 1 | Open https://otus.ru/catalog/courses?categories=programming |
| 2 | Apply a filter |

**Ожидаемый результат:** The applied filter is visible in the URL

### TC-06. Pagination works correctly and pages do not repeat courses

**Тип:** позитивный

**Проверяет требование:** BR-6

**Предусловия:**

- User opens the site in a web browser

**Шаги:**

| № | Действие |
|---|----------|
| 1 | Open https://otus.ru/catalog/courses?categories=programming |
| 2 | Navigate to the next page |

**Ожидаемый результат:** Courses are displayed on the next page and no duplicates are shown

## Негативные сценарии

### TC-07. Invalid category in URL does not break the page

**Тип:** негативный

**Проверяет требование:** BR-7

**Предусловия:**

- User opens the site in a web browser

**Шаги:**

| № | Действие |
|---|----------|
| 1 | Open https://otus.ru/catalog/courses?categories=invalid |

**Ожидаемый результат:** The page is displayed without errors, but only programming courses are shown

### TC-08. Non-existent page number in URL does not break the page

**Тип:** негативный

**Проверяет требование:** BR-7

**Предусловия:**

- User opens the site in a web browser

**Шаги:**

| № | Действие |
|---|----------|
| 1 | Open https://otus.ru/catalog/courses?categories=programming&page=999 |

**Ожидаемый результат:** The page is displayed without errors, but only the available courses are shown

### TC-09. Empty result after applying filters

**Тип:** негативный

**Проверяет требование:** BR-7

**Предусловия:**

- User opens the site in a web browser

**Шаги:**

| № | Действие |
|---|----------|
| 1 | Open https://otus.ru/catalog/courses?categories=programming |
| 2 | Apply filters that result in no courses |

**Ожидаемый результат:** The course list is empty and a message is displayed indicating no results

### TC-10. Clearing filters resets the URL

**Тип:** негативный

**Проверяет требование:** BR-5

**Предусловия:**

- User opens the site in a web browser with filters applied

**Шаги:**

| № | Действие |
|---|----------|
| 1 | Open https://otus.ru/catalog/courses?categories=programming&filter=example |
| 2 | Clear the filter |

**Ожидаемый результат:** The URL returns to the original state without any filters
