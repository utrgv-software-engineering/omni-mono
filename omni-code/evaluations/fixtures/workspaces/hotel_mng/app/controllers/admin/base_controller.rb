class Admin::BaseController < ApplicationController
  layout 'admin'

  before_action :authenticate_user!
  before_action :require_platform_admin!
  around_action :bypass_tenant_scoping

  private

  def require_platform_admin!
    unless current_user&.platform_admin?
      flash[:alert] = "You don't have permission to access this area."
      redirect_to root_path
    end
  end

  def bypass_tenant_scoping
    ActsAsTenant.without_tenant do
      yield
    end
  end
end
