ENV["RAILS_ENV"] ||= "test"

# Code coverage - must be loaded before application code
require 'simplecov'
SimpleCov.start 'rails' do
  add_filter '/test/'
  add_filter '/config/'
  add_filter '/vendor/'

  add_group 'Controllers', 'app/controllers'
  add_group 'Models', 'app/models'
  add_group 'Mailers', 'app/mailers'
  add_group 'Jobs', 'app/jobs'
  add_group 'Policies', 'app/policies'
  add_group 'Blueprints', 'app/blueprints'
  add_group 'Components', 'app/components'
end

# Setup OpenAPI contract testing BEFORE loading the application
require 'openapi_first'
OpenapiFirst::Test.setup do |config|
  config.register('api/openapi.yaml')
  # Ignore unknown requests that aren't part of the API (like non-API routes)
  config.ignore_unknown_requests = true
  # Set to true to see API coverage report after tests
  config.report_coverage = false
end

require_relative "../config/environment"
require "rails/test_help"

# Force routes to load eagerly
# Rails lazy-loads routes, but controller tests need them loaded to work with
# Devise authentication constraints. Accessing .routes triggers the load.
Rails.application.routes.routes.count

# Ensure Devise mappings are also loaded (should be set by routes loading)
if Devise.mappings.empty?
  Rails.application.reload_routes!
end

module ActiveSupport
  class TestCase
    # Run tests in parallel with specified workers
    parallelize(workers: :number_of_processors)

    # Use factories instead of fixtures
    include FactoryBot::Syntax::Methods

    # Add more helper methods to be used by all tests here...
    
    # Helper to set up tenant context for tests
    def set_current_tenant(account)
      ActsAsTenant.current_tenant = account
    end
    
    # Helper to clear tenant context
    def clear_current_tenant
      ActsAsTenant.current_tenant = nil
    end
    
    # Run tests with tenant context
    def with_tenant(account, &block)
      ActsAsTenant.with_tenant(account, &block)
    end
  end
end

# Load API test helper for API tests
require_relative 'support/api_test_helper'

# Load controller test helper for staff/public controller tests
require_relative 'support/controller_test_helper'

class ActionDispatch::IntegrationTest
  include ApiTestHelper
  include OpenapiFirst::Test::Methods

  # Wrap the Rails app with OpenAPI validation for API tests only
  def app
    if self.class.name.start_with?("Api::") || self.class.name.include?("Api::") || self.class.to_s.include?("Api")
      OpenapiFirst::Test.app(Rails.application)
    else
      Rails.application
    end
  end
end
