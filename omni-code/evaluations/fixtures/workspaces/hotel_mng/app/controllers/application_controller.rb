class ApplicationController < ActionController::Base
  include Pundit::Authorization
  include Pagy::Backend
  
  # Only allow modern browsers supporting webp images, web push, badges, import maps, CSS nesting, and CSS :has.
  allow_browser versions: :modern
  
  set_current_tenant_through_filter
  before_action :configure_permitted_parameters, if: :devise_controller?
  before_action :set_tenant
  before_action :set_current_account_for_user
  before_action :check_force_password_change
  
  rescue_from Pundit::NotAuthorizedError, with: :user_not_authorized
  
  protected
  
  def configure_permitted_parameters
    devise_parameter_sanitizer.permit(:sign_up, keys: [:first_name, :last_name])
    devise_parameter_sanitizer.permit(:account_update, keys: [:first_name, :last_name])
  end
  
  private
  
  def set_tenant
    return unless current_user
    
    # Priority 1: Use session-stored account ID if available
    if session[:current_account_id]
      account = current_user.accounts.find_by(id: session[:current_account_id])
      if account
        set_current_tenant(account)
        return
      else
        # Clear invalid session account ID
        session.delete(:current_account_id)
      end
    end
    
    # Priority 2: Try to match subdomain to user's accounts
    if request.subdomain.present? && request.subdomain != 'www'
      account = current_user.accounts.find_by(subdomain: request.subdomain)
      if account
        session[:current_account_id] = account.id
        set_current_tenant(account)
        return
      end
    end
    
    # Priority 3: Use first available account
    account = current_user.accounts.active.first
    if account
      session[:current_account_id] = account.id
      set_current_tenant(account)
    end
  end
  
  def set_current_account_for_user
    if current_user && current_tenant
      current_user.current_account = current_tenant
    end
  end
  
  def current_tenant
    ActsAsTenant.current_tenant
  end
  helper_method :current_tenant
  
  def require_account!
    redirect_to root_path, alert: "You must select an account first" unless current_tenant
  end
  
  def user_not_authorized
    flash[:alert] = "You are not authorized to perform this action."
    redirect_back(fallback_location: root_path)
  end
  
  def current_account_user
    @current_account_user ||= current_user&.account_users&.find_by(account: current_tenant)
  end
  helper_method :current_account_user
  
  def user_has_multiple_accounts?
    current_user && current_user.accounts.count > 1
  end
  helper_method :user_has_multiple_accounts?
  
  def current_hotel
    return nil unless current_tenant
    
    # Try to get hotel from session first
    if session[:current_hotel_id]
      hotel = current_tenant.hotels.find_by(id: session[:current_hotel_id])
      return hotel if hotel
    end
    
    # Fall back to first hotel in account
    hotel = current_tenant.hotels.first
    session[:current_hotel_id] = hotel.id if hotel
    hotel
  end
  helper_method :current_hotel
  
  def set_current_hotel(hotel_id)
    hotel = current_tenant.hotels.find_by(id: hotel_id)
    if hotel
      session[:current_hotel_id] = hotel.id
      true
    else
      false
    end
  end
  
  def check_force_password_change
    return unless current_user&.force_password_change?
    return if devise_controller?
    return if controller_name == 'password_changes'

    redirect_to edit_password_change_path, alert: "Please change your password before continuing."
  end

  # Blazer authentication method
  def require_blazer_authentication
    authenticate_user!
    set_tenant
    
    # Store account ID for Blazer queries
    Thread.current[:blazer_account_id] = current_tenant&.id
    
    unless current_user && current_account_user && 
           (current_account_user.owner? || current_account_user.admin? || current_account_user.hotel_manager?)
      flash[:alert] = "You must be a manager or admin to access reports."
      redirect_to root_path
    end
  end
end
