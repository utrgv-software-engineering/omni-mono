# frozen_string_literal: true

module Users
  class SessionsController < Devise::SessionsController
    # Override after_sign_in_path to set up account context
    def after_sign_in_path_for(resource)
      # Find the user's accounts
      accounts = resource.accounts.active
      
      if accounts.empty?
        # This shouldn't happen with our registration flow, but handle it
        flash[:alert] = "No active account found. Please contact support."
        sign_out(resource)
        new_user_session_path
      elsif accounts.count == 1
        # User has only one account, set it automatically
        session[:current_account_id] = accounts.first.id
        authenticated_root_path
      else
        # User has multiple accounts, let them choose
        # For now, we'll use the first one, but you could redirect to an account selector
        session[:current_account_id] = accounts.first.id
        authenticated_root_path
      end
    end
    
    # Clear account context on sign out
    def destroy
      session.delete(:current_account_id)
      super
    end
  end
end