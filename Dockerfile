FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY returns_agent.py api.py ./

# Run as a non-root user
RUN useradd -m appuser
USER appuser

EXPOSE 8000
# ONE worker on purpose: state is in memory (see README, known limits)
CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8000"]