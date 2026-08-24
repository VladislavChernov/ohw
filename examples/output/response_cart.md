# Тест-кейсы: cart.txt

**Тестируемый сайт:** https://www.saucedemo.com

**Всего кейсов:** 5 (позитивных: 3, негативных: 2)

## Позитивные сценарии

### TC-01. User logs in successfully with valid credentials

**Тип:** позитивный

**Проверяет требование:** BR-1

**Предусловия:**

- User has a valid account with the system

**Шаги:**

| № | Действие |
|---|----------|
| 1 | Enter valid email and password |
| 2 | Click on the login button |

**Ожидаемый результат:** User is redirected to the dashboard

### TC-02. User creates a new account with valid details

**Тип:** позитивный

**Проверяет требование:** BR-2

**Предусловия:**

- User is on the registration page

**Шаги:**

| № | Действие |
|---|----------|
| 1 | Fill in the registration form with valid data |
| 2 | Click on the submit button |

**Ожидаемый результат:** User is redirected to the login page

### TC-04. User updates account details

**Тип:** позитивный

**Проверяет требование:** BR-3

**Предусловия:**

- User is logged in and on the account settings page

**Шаги:**

| № | Действие |
|---|----------|
| 1 | Click on the edit icon |
| 2 | Update the email and password |
| 3 | Click on the save changes button |

**Ожидаемый результат:** Account details are updated successfully

## Негативные сценарии

### TC-03. User fails to log in with invalid credentials

**Тип:** негативный

**Проверяет требование:** BR-1

**Предусловия:**

- User has an account with the system

**Шаги:**

| № | Действие |
|---|----------|
| 1 | Enter invalid email and password |
| 2 | Click on the login button |

**Ожидаемый результат:** Login fails and error message is displayed

### TC-05. User attempts to log in with expired credentials

**Тип:** негативный

**Проверяет требование:** BR-1

**Предусловия:**

- User has an account with expired credentials

**Шаги:**

| № | Действие |
|---|----------|
| 1 | Enter the email and password |
| 2 | Click on the login button |

**Ожидаемый результат:** Login fails and an error message is displayed
