# Публикация результатов

Реализация отправлена в существующую ветку `feat/baikal-llm-wiki`: commit `c2d39f6`. Основная ветка не изменялась. PDF, локальные базы и изображения не включены.

Создание draft Pull Request командой gh завершилось ответом:

```text
Post "https://api.github.com/graphql": Forbidden
```

В этой среде Git push работает, но запрос GitHub GraphQL API запрещён. Ответ не позволяет однозначно разделить сетевую политику прокси и права API. Ошибка не является доказательством неверного пользовательского токена; новые секреты не запрашивались. PR автоматически не создан.

Переносить файлы вручную уже не требуется. Открыть [сравнение main и feat/baikal-llm-wiki](https://github.com/EkaterinaShigarova/baikal-reports/compare/main...feat/baikal-llm-wiki?expand=1), нажать **Create pull request**, вставить [готовое описание](PULL_REQUEST.md). Рекомендуемый заголовок: `Baikal LLM Wiki: processed reports, evidence wiki and environmental dashboard`. PR можно оставить черновиком до проверки.

В собственной среде с работающим GitHub CLI и доступом к API:

```bash
gh pr create --draft --base main --head feat/baikal-llm-wiki \
  --title 'Baikal LLM Wiki: processed reports, evidence wiki and environmental dashboard' \
  --body-file docs/PULL_REQUEST.md
```

Слияние в main автоматически не выполнялось.
