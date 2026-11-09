FROM python:3.14.4-slim@sha256:2ca02f32b4d9d893863367ce07ec1972819f476dd38d8612f2a9cb6a41cbb727
ARG SOURCE_COMMIT
LABEL org.opencontainers.image.revision=$SOURCE_COMMIT
WORKDIR /app
RUN pip install --no-cache-dir --only-binary=:all: confluent-kafka==2.16.1
COPY engine engine
COPY pipeline pipeline
COPY tools tools
COPY contracts contracts
COPY ads ads
COPY content content
COPY fixtures fixtures
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER 10001:10001
ENTRYPOINT ["python","-m","pipeline.simulation"]
