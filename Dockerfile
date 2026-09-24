# One image, two roles. Whether a container is the API or the operator is
# decided by the command the Deployment gives it, not by the image - so
# there is only one thing to build, tag and load into the cluster.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Nothing here needs to write to disk or bind a privileged port, so the
# processes run as this user. The Deployments set the same id in their
# securityContext.
RUN useradd --create-home --uid 10001 tellix

# A scratch directory for the build only. It is deleted once the package
# is installed; see the final WORKDIR for why that matters.
WORKDIR /build

# Dependencies first, in their own layer. Editing app/ afterwards does not
# invalidate this layer, so a rebuild during development takes seconds
# instead of re-downloading everything.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Then the project itself, installed into site-packages as an ordinary
# package. pip writes those files world-readable no matter what
# permissions the source had, and the source copy is removed right away,
# so the installed package is the only copy of the code in the image.
COPY pyproject.toml README.md ./
COPY app ./app
RUN pip install --no-cache-dir --no-deps . && rm -rf /build

USER 10001

# An empty directory on purpose. uvicorn puts its working directory at
# the front of sys.path, so if the source tree were here it would import
# app/ from the copied files rather than the installed package - and
# COPY keeps whatever permissions those files had on the machine that ran
# `docker build`. That is how an image can pass in CI and crash with a
# PermissionError on a laptop.
WORKDIR /home/tellix

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
