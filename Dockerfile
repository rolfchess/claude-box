# syntax=docker/dockerfile:1
FROM maven:3.9-eclipse-temurin-21

ARG NODE_MAJOR=22
ARG GOSU_VERSION=1.17
ARG USERNAME=claude
ARG GO_VERSION=1.27.1
ARG GO_SHA256_AMD64=63d339f0da5ab53635a56f2490a7984dfe12dfcff22ad749f63edaf590168445
ARG GO_SHA256_ARM64=3450b45a3f9ee8568792736a5c5e70a1f2e9b36c35a8f74958c03e51d7d92bec

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8

# ---------------------------------------------------------------------------
# Base tooling: git, curl, jq, ripgrep, gcc, gosu, Node.js, Go, Docker CLI,
# glab, and Claude Code itself.
# ---------------------------------------------------------------------------
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        ca-certificates curl gnupg git jq less unzip ripgrep procps locales \
        python3 python-is-python3 gcc libc6-dev; \
    \
    ARCH="$(dpkg --print-architecture)"; \
    \
    # --- gosu (used by the entrypoint to drop from root to the claude user) ---
    curl -fsSL "https://github.com/tianon/gosu/releases/download/${GOSU_VERSION}/gosu-${ARCH}" \
        -o /usr/local/bin/gosu; \
    chmod +x /usr/local/bin/gosu; \
    gosu --version; \
    \
    # --- Go (checksum from https://go.dev/dl/) ---
    case "${ARCH}" in \
        amd64) GO_SHA256="${GO_SHA256_AMD64}" ;; \
        arm64) GO_SHA256="${GO_SHA256_ARM64}" ;; \
        *) echo "no Go checksum for ${ARCH}" >&2; exit 1 ;; \
    esac; \
    curl -fsSL "https://go.dev/dl/go${GO_VERSION}.linux-${ARCH}.tar.gz" -o /tmp/go.tar.gz; \
    echo "${GO_SHA256}  /tmp/go.tar.gz" | sha256sum -c -; \
    tar -xzf /tmp/go.tar.gz -C /usr/local; \
    rm /tmp/go.tar.gz; \
    /usr/local/go/bin/go version; \
    \
    # --- Node.js (Claude Code is an npm package) ---
    curl -fsSL "https://deb.nodesource.com/setup_${NODE_MAJOR}.x" | bash -; \
    apt-get install -y --no-install-recommends nodejs; \
    \
    # --- Docker CLI (talks to the mounted host socket for Testcontainers) ---
    install -m 0755 -d /etc/apt/keyrings; \
    . /etc/os-release; \
    curl -fsSL "https://download.docker.com/linux/${ID}/gpg" -o /etc/apt/keyrings/docker.asc; \
    chmod a+r /etc/apt/keyrings/docker.asc; \
    echo "deb [arch=${ARCH} signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/${ID} ${VERSION_CODENAME} stable" \
        > /etc/apt/sources.list.d/docker.list; \
    apt-get update; \
    apt-get install -y --no-install-recommends docker-ce-cli docker-compose-plugin; \
    \
    # --- glab (GitLab CLI), latest release for this arch ---
    GLAB_VERSION="$(curl -fsSL 'https://gitlab.com/api/v4/projects/gitlab-org%2Fcli/releases/permalink/latest' | jq -r .tag_name | sed 's/^v//')"; \
    curl -fsSL "https://gitlab.com/gitlab-org/cli/-/releases/v${GLAB_VERSION}/downloads/glab_${GLAB_VERSION}_linux_${ARCH}.tar.gz" \
        -o /tmp/glab.tar.gz; \
    tar -xzf /tmp/glab.tar.gz -C /tmp; \
    install /tmp/bin/glab /usr/local/bin/glab; \
    rm -rf /tmp/glab.tar.gz /tmp/bin; \
    glab --version; \
    \
    # --- Claude Code ---
    npm install -g @anthropic-ai/claude-code; \
    \
    apt-get clean; \
    rm -rf /var/lib/apt/lists/*

# ---------------------------------------------------------------------------
# Non-root user. Claude Code refuses to run with --dangerously-skip-permissions
# as root, so everything runs as this user (the entrypoint drops to it).
# ---------------------------------------------------------------------------
RUN useradd --create-home --shell /bin/bash "${USERNAME}" \
    && mkdir -p /home/${USERNAME}/.claude \
                /home/${USERNAME}/.config/glab-cli \
                /home/${USERNAME}/.m2 \
                /home/${USERNAME}/go \
                /workspace \
    && chown -R ${USERNAME}:${USERNAME} /home/${USERNAME} /workspace

# Mounted repositories can be owned by a user other than claude.
# Trust them in the system config, since ~/.gitconfig is mounted read-only.
RUN git config --system --add safe.directory '*'

# git status and git diff do not refresh the index, so they never take
# index.lock. The repo is shared with the host, where other git processes run.
ENV GIT_OPTIONAL_LOCKS=0

# Keep Maven's local repo + config under the claude user's home (mounted volume).
ENV MAVEN_CONFIG=/home/claude/.m2

# Go on the PATH. cgo is on, so `go test -race` works. The module cache and
# the build cache both live under ~/go (mounted volume).
ENV PATH=/usr/local/go/bin:/home/claude/go/bin:${PATH} \
    GOPATH=/home/claude/go \
    GOCACHE=/home/claude/go/cache \
    CGO_ENABLED=1 \
    CC=gcc

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["claude", "--dangerously-skip-permissions"]
