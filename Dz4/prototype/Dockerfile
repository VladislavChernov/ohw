# GraphRAG proto service image (Config/Glossary), M0.
FROM python:3.13-slim-bookworm

RUN pip install --no-cache-dir uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --no-dev --frozen

# Исходник нужен только чтобы собрать проект, но в образе он оставаться не должен:
# compose монтирует `../src:/app/src:ro` каждому сервису, поэтому запечённая копия всегда
# перекрыта и в рантайме не используется. Хуже того, без монтирования образ молча отдаёт
# код времени сборки - и правка на хосте выглядит применённой, а не действует.
#
# Единственный источник кода - монтирование. Образ без него падает на импорте, и это
# правильно: молча работать на устаревшем коде хуже, чем не работать.
RUN rm -rf /app/src

ENV DOMAIN_PROFILES_DIR=/app/domain_profiles \
    CONFIG_DB_PATH=/app/runtime/config.db

ENTRYPOINT ["uv", "run", "--no-sync"]
CMD ["graphrag-config"]