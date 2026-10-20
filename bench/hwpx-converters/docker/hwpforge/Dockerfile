FROM python:3.12-slim
RUN pip install --no-cache-dir hwpforge==0.16.11 pymupdf
COPY convert.py /app/convert.py
ENTRYPOINT ["python","/app/convert.py"]
