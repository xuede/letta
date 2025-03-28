# Start with pgvector base for builder
FROM ankane/pgvector:v0.5.1 AS builder

# Install Python and required packages
RUN apt-get update && apt-get install -y \
    python3 \
    python3-venv \
    python3-pip \
    python3-full \
    build-essential \
    libpq-dev \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

ARG LETTA_ENVIRONMENT=PRODUCTION
ENV LETTA_ENVIRONMENT=${LETTA_ENVIRONMENT} \
    POETRY_NO_INTERACTION=1 \
    POETRY_VIRTUALENVS_IN_PROJECT=1 \
    POETRY_VIRTUALENVS_CREATE=1 \
    POETRY_CACHE_DIR=/tmp/poetry_cache

WORKDIR /app

# Create and activate virtual environment
RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Now install poetry in the virtual environment
RUN pip install --no-cache-dir poetry==1.8.2

# Copy dependency files first
COPY pyproject.toml poetry.lock ./
# Then copy the rest of the application code
COPY . .

RUN poetry lock --no-update && \
    poetry install --all-extras && \
    rm -rf $POETRY_CACHE_DIR

# Runtime stage
FROM ankane/pgvector:v0.5.1 AS runtime

# Install Python packages and OpenTelemetry Collector
RUN apt-get update && apt-get install -y \
    python3 \
    python3-venv \
    curl \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /app \
    # Install OpenTelemetry Collector
    && curl -L https://github.com/open-telemetry/opentelemetry-collector-releases/releases/download/v0.96.0/otelcol-contrib_0.96.0_linux_amd64.tar.gz -o /tmp/otel-collector.tar.gz \
    && tar xzf /tmp/otel-collector.tar.gz -C /usr/local/bin \
    && rm /tmp/otel-collector.tar.gz \
    && mkdir -p /etc/otel

# Add OpenTelemetry Collector configs
COPY otel/otel-collector-config-file.yaml /etc/otel/config-file.yaml
COPY otel/otel-collector-config-clickhouse.yaml /etc/otel/config-clickhouse.yaml

ARG LETTA_ENVIRONMENT=PRODUCTION
ENV LETTA_ENVIRONMENT=${LETTA_ENVIRONMENT} \
    VIRTUAL_ENV="/app/.venv" \
    PATH="/app/.venv/bin:$PATH" \
    POSTGRES_USER=letta \
    POSTGRES_PASSWORD=letta \
    POSTGRES_DB=letta \
    COMPOSIO_DISABLE_VERSION_CHECK=true \
    LETTA_OTEL_EXPORTER_OTLP_ENDPOINT="http://localhost:4317"

WORKDIR /app

# Copy virtual environment and app from builder
COPY --from=builder /app .

# Copy initialization SQL if it exists
COPY init.sql /docker-entrypoint-initdb.d/

EXPOSE 8283 5432 4317 4318

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["./letta/server/startup.sh"]
