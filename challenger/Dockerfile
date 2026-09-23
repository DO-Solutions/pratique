# The challenger's sandbox image: a real browser, ready before the model gets its task.
# Registered as a Harness Runtime custom template (Bring-Your-Own-Template) on the coding-opencode base:
#   docker build -t registry.digitalocean.com/$REGISTRY/pratique-challenger:v1 challenger/
#   docker push registry.digitalocean.com/$REGISTRY/pratique-challenger:v1
#   doctl harness-runtime template create --name pratique-browser --base-template coding-opencode \
#     --source-oci-ref registry.digitalocean.com/$REGISTRY/pratique-challenger:v1
# Sessions then set `template: pratique-browser` and find Chromium at $PLAYWRIGHT_BROWSERS_PATH.
FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers
RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 python3-pip ca-certificates curl \
 && rm -rf /var/lib/apt/lists/*
RUN pip3 install --break-system-packages --no-cache-dir playwright==1.47.0 \
 && mkdir -p /opt/pw-browsers \
 && python3 -m playwright install --with-deps chromium \
 && chmod -R a+rX /opt/pw-browsers \
 && rm -rf /var/lib/apt/lists/*
