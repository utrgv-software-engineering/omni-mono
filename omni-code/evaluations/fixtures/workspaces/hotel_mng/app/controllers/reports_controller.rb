class ReportsController < ApplicationController
  before_action :authenticate_user!
  before_action :require_manager_access
  layout 'staff'
  
  def index
    # Main reports dashboard that iframes Blazer
    @blazer_url = "/blazer"
  end
  
  private
  
  def require_manager_access
    unless current_account_user && 
           (current_account_user.owner? || current_account_user.admin? || current_account_user.hotel_manager?)
      flash[:alert] = "You must be a manager or admin to access reports."
      redirect_to root_path
    end
  end
end