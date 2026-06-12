FROM aquasec/trivy:0.70.0

RUN adduser -D trivyuser \
    && mkdir -p /home/trivyuser/.cache/trivy \
    && chown -R trivyuser:trivyuser /home/trivyuser

ENV HOME=/home/trivyuser
USER trivyuser
