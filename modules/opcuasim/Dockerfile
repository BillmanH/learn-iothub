FROM python:3.11-slim

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Copy requirements and install dependencies using uv
COPY requirements.txt .
RUN uv pip install --system --no-cache -r requirements.txt

# Copy application code
COPY app.py .
COPY building.py .
COPY building_structure.yaml .

# Expose OPC-UA default port
EXPOSE 4840

# Run the application
CMD ["python", "app.py"]
