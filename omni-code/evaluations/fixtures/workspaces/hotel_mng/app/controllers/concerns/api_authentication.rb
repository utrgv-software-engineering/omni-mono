module ApiAuthentication
  extend ActiveSupport::Concern

  included do
    before_action :authenticate_api!
    after_action :track_api_usage
  end

  private

  def authenticate_api!
    authenticate_with_api_key || render_unauthorized
  end

  def authenticate_with_api_key
    api_key = extract_api_key
    return false unless api_key

    # Find the AccountUser by API key - this gives us both user and account context
    @current_account_user = AccountUser.find_by(api_key: api_key)
    return false unless @current_account_user

    @current_user = @current_account_user.user
    @current_account = @current_account_user.account

    # Verify this is an AI agent user
    return false unless @current_user.is_ai_agent?

    # Set tenant for multi-tenancy (ActsAsTenant)
    ActsAsTenant.current_tenant = @current_account

    # Don't validate hotel access here - let individual controllers handle it
    # This allows proper 404 responses for non-existent hotels
    true
  end

  def extract_api_key
    # Support both header formats
    request.headers['X-API-Key'] || 
    request.headers['Authorization']&.match(/^Bearer (.+)$/)&.captures&.first
  end

  def track_api_usage
    return unless @current_account_user

    # Update last used timestamp asynchronously to avoid slowing down requests
    @current_account_user.touch(:api_key_last_used_at)
  end

  def render_unauthorized
    render json: {
      error: 'Unauthorized',
      message: 'Invalid or missing API key. Please provide a valid API key in the X-API-Key header.'
    }, status: :unauthorized
  end

  def render_forbidden(message = 'You do not have permission to access this resource')
    render json: {
      error: 'Forbidden',
      message: message
    }, status: :forbidden
  end

  def render_not_found(resource = 'Resource')
    render json: {
      error: 'Not Found',
      message: "#{resource} not found"
    }, status: :not_found
  end

  # Override Pundit's user method for API contexts
  def pundit_user
    @current_account_user
  end

  # Helper to check if user has permission for current hotel
  def authorize_hotel_access!
    unless @current_hotel && @current_account_user.has_any_role?(:owner, :admin, :hotel_manager, :front_desk, :housekeeping)
      render_forbidden('You do not have access to this hotel')
    end
  end
end