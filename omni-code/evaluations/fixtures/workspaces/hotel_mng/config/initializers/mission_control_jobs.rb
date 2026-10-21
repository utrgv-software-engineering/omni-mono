# Disable HTTP basic auth - we use Devise + platform admin check via routing constraint
Rails.application.configure do
  config.mission_control.jobs.http_basic_auth_enabled = false
end