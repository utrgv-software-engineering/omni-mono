class Api::V1::BaseController < ActionController::API
  include OpenapiValidation
  include Pundit::Authorization
  include ApiAuthentication
  include ApiErrorHandling
  include ApiPagination

  # API controllers don't need CSRF protection (already disabled in ActionController::API)

  private

  # Helper method to require hotel context
  def require_hotel!
    unless @current_hotel
      render_not_found('Hotel')
      return false
    end
    true
  end

  # Helper to load hotel from params
  def set_hotel
    @current_hotel = @current_account.hotels.find(params[:hotel_id])
  rescue ActiveRecord::RecordNotFound
    render_not_found('Hotel')
  end

  # Standard JSON response helpers
  def render_success(data = {}, status: :ok)
    render json: data, status: status
  end

  def render_created(data)
    render json: data, status: :created
  end

  def render_no_content
    head :no_content
  end

  # Override pundit_user to use AccountUser for authorization
  def pundit_user
    @current_account_user
  end
end