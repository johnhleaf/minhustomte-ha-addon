FROM python:3.11-alpine

# Install dependencies
RUN apk add --no-cache bash curl jq

# Install Python packages
RUN pip3 install --no-cache-dir requests websocket-client

# Copy files
COPY run.py /
COPY themes /themes
RUN chmod a+x /run.py

# Start script
CMD ["/run.py"]
