# frozen_string_literal: true

# Concern to enable OpenAPI validation for API controllers
module OpenapiValidation
  extend ActiveSupport::Concern

  included do
    # Only set up OpenAPI validation in the controller itself, not as middleware
    # The validation will be handled by OpenapiFirst::Test in tests
    # and we'll rely on our own validation in production
  end
end