# ---- Stage 1: install dependencies into a virtual environment ----------
FROM python:3.12-slim AS build
WORKDIR /build
RUN python -m venv /venv
ENV PATH="/venv/bin:$PATH"
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ---- Stage 2: small runtime image, no build tools, non-root user --------
FROM python:3.12-slim AS runtime
ARG APP_VERSION=dev
ENV PATH="/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_VERSION=${APP_VERSION}

RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin orbit
COPY --from=build /venv /venv
WORKDIR /srv
COPY orbitwatch ./orbitwatch

USER 10001
EXPOSE 8080
CMD ["uvicorn", "orbitwatch.main:app", "--host", "0.0.0.0", "--port", "8080"]
