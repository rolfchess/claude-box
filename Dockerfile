# syntax=docker/dockerfile:1
FROM maven:3.9-eclipse-temurin-21

ARG NODE_MAJOR=22
ARG GOSU_VERSION=1.17
ARG USERNAME=claude

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8

# ---------------------------------------------------------------------------
# Base tooling: git, curl, jq, ripgrep, gosu, Node.js, Docker CLI, glab,
# and Claude Code itself.
# ---------------------------------------------------------------------------
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        ca-certificates curl gnupg git jq less unzip ripgrep procps locales \
        python3 python-is-python3; \
    \
    ARCH="$(dpkg --print-architecture)"; \
    \
    # --- gosu (used by the entrypoint to drop from root to the claude user) ---
    curl -fsSL "https://github.com/tianon/gosu/releases/download/${GOSU_VERSION}/gosu-${ARCH}" \
        -o /usr/local/bin/gosu; \
    chmod +x /usr/local/bin/gosu; \
    gosu --version; \
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
                /home/${USERNAME}/.config/glab \
                /home/${USERNAME}/.m2 \
                /workspace \
    && chown -R ${USERNAME}:${USERNAME} /home/${USERNAME} /workspace

# Keep Maven's local repo + config under the claude user's home (mounted volume).
ENV MAVEN_CONFIG=/home/claude/.m2

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["claude", "--dangerously-skip-permissions"]
