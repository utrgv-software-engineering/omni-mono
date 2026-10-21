# frozen_string_literal: true

module Users
  class RegistrationsController < Devise::RegistrationsController
    before_action :configure_sign_up_params, only: [:create]
    before_action :configure_account_update_params, only: [:update]

    # POST /resource
    def create
      build_resource(sign_up_params)

      # Build the account for this user
      account = build_account_for_user(resource)

      # Use a transaction to ensure both User and Account are created together
      ActiveRecord::Base.transaction do
        resource.save!
        account.save!
        
        # Create AccountUser relationship with owner role
        account_user = AccountUser.create!(
          user: resource,
          account: account
        )
        
        # Assign owner role to the user for this account
        account_user.add_role(:owner)
        
        # Set the account in session for immediate use
        session[:current_account_id] = account.id
      end

      if resource.persisted?
        if resource.active_for_authentication?
          set_flash_message! :notice, :signed_up
          sign_up(resource_name, resource)
          respond_with resource, location: after_sign_up_path_for(resource)
        else
          set_flash_message! :notice, :"signed_up_but_#{resource.inactive_message}"
          expire_data_after_sign_in!
          respond_with resource, location: after_inactive_sign_up_path_for(resource)
        end
      else
        clean_up_passwords resource
        set_minimum_password_length
        respond_with resource
      end
    rescue ActiveRecord::RecordInvalid => e
      # If the transaction fails, ensure we have a clean state
      resource.errors.add(:base, "Could not create your account. Please try again.")
      clean_up_passwords resource
      set_minimum_password_length
      respond_with resource
    end

    protected

    # Build account with data from the registration form
    def build_account_for_user(user)
      Account.new(
        name: params[:account_name] || "#{user.first_name}'s Organization",
        subdomain: generate_subdomain(params[:account_name] || user.first_name),
        email: user.email,
        status: 'active',
        timezone: 'UTC',
        currency: 'USD',
        trial_ends_at: 30.days.from_now # 30-day trial period
      )
    end

    # Generate a unique subdomain from the account name
    def generate_subdomain(name)
      # Create base subdomain from name
      base_subdomain = name.downcase.gsub(/[^a-z0-9]+/, '')
      
      # Ensure it's not empty and has reasonable length
      base_subdomain = base_subdomain[0..19] if base_subdomain.length > 20
      base_subdomain = "account" if base_subdomain.blank?
      
      # Make it unique by adding a number if necessary
      subdomain = base_subdomain
      counter = 1
      while Account.exists?(subdomain: subdomain)
        subdomain = "#{base_subdomain}#{counter}"
        counter += 1
      end
      
      subdomain
    end

    # Override to redirect to dashboard after sign up
    def after_sign_up_path_for(resource)
      authenticated_root_path
    end

    # Permit additional parameters for sign up
    def configure_sign_up_params
      devise_parameter_sanitizer.permit(:sign_up, keys: [:first_name, :last_name])
    end

    # Permit additional parameters for account update
    def configure_account_update_params
      devise_parameter_sanitizer.permit(:account_update, keys: [:first_name, :last_name])
    end
  end
end