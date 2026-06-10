# PROMPT UP — Схема базы данных

## ER-диаграмма (Mermaid)

```mermaid
erDiagram
    users ||--o| user_profiles : "1:1 имеет профиль"
    users ||--o{ module_progress : "1:N прогресс по модулям"

    users {
        varchar_36 id PK "uuid4, авто"
        varchar_255 username UK "NOT NULL"
        varchar_255 email UK "NOT NULL"
        varchar_255 hashed_password "NOT NULL, bcrypt"
        boolean is_active "default: true"
        datetime created_at "default: now(UTC)"
    }

    user_profiles {
        varchar_36 id PK "uuid4, авто"
        varchar_36 user_id FK_UK "→ users.id, UNIQUE"
        varchar_255 level "newbie | intermediate | advanced"
        varchar_255 sphere "сфера деятельности"
        varchar_255 goals "цели обучения"
        boolean profiler_done "default: false"
        boolean tutor_introduced "default: false"
        integer current_module_id "1–6, nullable"
        integer total_score "default: 0"
        datetime created_at "default: now(UTC)"
    }

    module_progress {
        varchar_36 id PK "uuid4, авто"
        varchar_36 user_id FK "→ users.id"
        integer module_id "1–6"
        integer score "default: 0, макс. 50 = завершён"
        integer count "default: 0, кол-во попыток"
    }
```

## Связи

| От | До | Тип | Через | Описание |
|---|---|---|---|---|
| `users` | `user_profiles` | 1:1 | `user_profiles.user_id` → `users.id` | Один пользователь имеет один профиль |
| `users` | `module_progress` | 1:N | `module_progress.user_id` → `users.id` | Один пользователь имеет много записей прогресса |

## Уникальные ограничения

| Таблица | Поля | Имя | Описание |
|---|---|---|---|
| `module_progress` | `user_id` + `module_id` | `uq_user_module` | Один пользователь — одна запись на модуль |

## Вычисляемые поля (не хранятся в БД)

| Таблица | Поле | Формула | Описание |
|---|---|---|---|
| `module_progress` | `is_completed` | `score >= 50` | Модуль пройден |
| `module_progress` | `avg` | `score / count` | Средний балл за попытку |

## Справочник модулей

| module_id | Название | Уровень |
|---|---|---|
| 1 | Структура промпта | newbie |
| 2 | Улучшение промптов | newbie |
| 3 | Few-shot prompting | intermediate |
| 4 | Chain-of-thought | intermediate |
| 5 | Мастер контекста | intermediate |
| 6 | Комплексный промпт | advanced |
