FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml README.md ./
COPY hoistway_audit ./hoistway_audit
RUN pip install --no-cache-dir .

ENV HOISTWAY_HOST=0.0.0.0
ENV HOISTWAY_PORT=8787
ENV HOISTWAY_DATA_DIR=/app/data

VOLUME ["/app/data"]
EXPOSE 8787
CMD ["hoistway-audit"]
