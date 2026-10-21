# frozen_string_literal: true

class AccountsController < ApplicationController
  before_action :authenticate_user!
  skip_before_action :set_tenant
  skip_before_action :set_current_account_for_user
  
  # GET /accounts
  # Show list of accounts for the current user to switch between
  def index
    @accounts = current_user.accounts.active
    @current_account_id = session[:current_account_id]
  end
  
  # POST /accounts/:id/switch
  # Switch to a different account
  def switch
    account = current_user.accounts.find(params[:id])
    
    if account
      session[:current_account_id] = account.id
      
      # If the account has a subdomain, redirect there
      if account.subdomain.present? && request.host != account.subdomain
        redirect_to root_url(subdomain: account.subdomain), 
                    notice: "Switched to #{account.name}"
      else
        redirect_to authenticated_root_path, 
                    notice: "Switched to #{account.name}"
      end
    else
      redirect_to accounts_path, alert: "Account not found"
    end
  end
end