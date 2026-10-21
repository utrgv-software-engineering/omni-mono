module ApiErrorHandling
  extend ActiveSupport::Concern

  included do
    rescue_from ActiveRecord::RecordNotFound, with: :handle_not_found
    rescue_from ActiveRecord::RecordInvalid, with: :handle_validation_error
    rescue_from ActionController::ParameterMissing, with: :handle_parameter_missing
    rescue_from Pundit::NotAuthorizedError, with: :handle_unauthorized
    rescue_from ActsAsTenant::Errors::NoTenantSet, with: :handle_no_tenant
  end

  private

  def handle_not_found(exception)
    resource = exception.model || 'Resource'
    render json: {
      error: 'Not Found',
      message: "#{resource} not found",
      status: 404
    }, status: :not_found
  end

  def handle_validation_error(exception)
    render json: {
      error: 'Validation Failed',
      message: 'The request could not be processed due to validation errors',
      errors: format_validation_errors(exception.record.errors),
      status: 422
    }, status: :unprocessable_content
  end

  def handle_parameter_missing(exception)
    render json: {
      error: 'Bad Request',
      message: "Missing required parameter: #{exception.param}",
      status: 400
    }, status: :bad_request
  end

  def handle_unauthorized(exception)
    render json: {
      error: 'Forbidden',
      message: 'You are not authorized to perform this action',
      status: 403
    }, status: :forbidden
  end

  def handle_no_tenant(exception)
    render json: {
      error: 'Bad Request',
      message: 'Account context not set. Please ensure you have access to this resource.',
      status: 400
    }, status: :bad_request
  end

  def format_validation_errors(errors)
    errors.map do |error|
      {
        field: error.attribute,
        code: error.type,
        message: error.full_message
      }
    end
  end
end