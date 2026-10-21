# syntax=docker/dockerfile:1
# check=error=true

# This Dockerfile is designed for production, not development. Use with Kamal or build'n'run by hand:
# docker build -t hotel .
# docker run -d -p 80:80 -e RAILS_MASTER_KEY=<value from config/master.key> --name hotel hotel

# For a containerized dev environment, see Dev Containers: https://guides.rubyonrails.org/getting_started_with_devcontainer.html

# Make sure RUBY_VERSION matches the Ruby version in .ruby-version
ARG RUBY_VERSION=3.4.4
FROM docker.io/library/ruby:$RUBY_VERSION-slim AS base

# Rails app lives here
WORKDIR /rails

# Install base packages (both SQLite and PostgreSQL for migration period)
RUN apt-get update -qq && \
    apt-get install --no-install-recommends -y curl libjemalloc2 libvips \
    sqlite3 libsqlite3-0 \
    libpq5 postgresql-client && \
    rm -rf /var/lib/apt/lists /var/cache/apt/archives

# Set production environment
ENV RAILS_ENV="production" \
    BUNDLE_DEPLOYMENT="1" \
    BUNDLE_PATH="/usr/local/bundle" \
    BUNDLE_WITHOUT="development"

# Throw-away build stage to reduce size of final image
FROM base AS build

# Install packages needed to build gems (both SQLite and PostgreSQL for migration)
RUN apt-get update -qq && \
    apt-get install --no-install-recommends -y build-essential git \
    libsqlite3-dev libpq-dev libyaml-dev pkg-config && \
    rm -rf /var/lib/apt/lists /var/cache/apt/archives

# Install application gems
COPY Gemfile Gemfile.lock ./
# Copy vendored gems (required for blazer)
COPY vendor/gems ./vendor/gems
RUN bundle install && \
    rm -rf ~/.bundle/ "${BUNDLE_PATH}"/ruby/*/cache "${BUNDLE_PATH}"/ruby/*/bundler/gems/*/.git && \
    bundle exec bootsnap precompile --gemfile

# Copy application code
COPY . .

# Precompile bootsnap code for faster boot times
RUN bundle exec bootsnap precompile app/ lib/

# Precompiling assets for production without requiring secret RAILS_MASTER_KEY
RUN SECRET_KEY_BASE_DUMMY=1 ./bin/rails assets:precompile




# Final stage for app image
FROM base

# Copy built artifacts: gems, application
COPY --from=build "${BUNDLE_PATH}" "${BUNDLE_PATH}"
COPY --from=build /rails /rails

# Copy rocketship configuration (startup script, profile, hooks, motd)
COPY .rocketship/startup.sh ./rocketship-startup.sh
COPY .rocketship/profile.sh /etc/profile.d/rocketship.sh
COPY .rocketship/motd /etc/motd
COPY .rocketship/hooks ./rocketship-hooks

# Start and enable SSH (per Azure App Service docs)
RUN apt-get update \
    && apt-get install -y --no-install-recommends dialog \
    && apt-get install -y --no-install-recommends openssh-server \
    && apt-get install -y --no-install-recommends gosu \
    && echo "root:Docker!" | chpasswd \
    && chmod u+x ./rocketship-startup.sh \
    && chmod +x /etc/profile.d/rocketship.sh \
    && echo 'source /etc/profile.d/rocketship.sh' >> /root/.bashrc \
    && rm -rf /var/lib/apt/lists /var/cache/apt/archives
COPY sshd_config /etc/ssh/

# Create non-root user for running Rails
RUN groupadd --system --gid 1000 rails && \
    useradd rails --uid 1000 --gid 1000 --create-home --shell /bin/bash && \
    chown -R rails:rails /rails

# Entrypoint prepares the database.
ENTRYPOINT ["/rails/bin/docker-entrypoint"]

EXPOSE 80 2222

CMD ["./rocketship-startup.sh"]
