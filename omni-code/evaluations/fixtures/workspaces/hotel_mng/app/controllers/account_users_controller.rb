# frozen_string_literal: true

class AccountUsersController < StaffController
  before_action :set_account_user, only: [:edit, :update, :destroy]
  
  def index
    @account_users = policy_scope(current_tenant.account_users.includes(:user, :roles))
    authorize AccountUser
    
    # Group by role for better display
    @owners = @account_users.joins(:roles).where(roles: { name: 'owner' })
    @admins = @account_users.joins(:roles).where(roles: { name: 'admin' })
    @hotel_managers = @account_users.joins(:roles).where(roles: { name: 'hotel_manager' })
    @front_desk_staff = @account_users.joins(:roles).where(roles: { name: 'front_desk' })
    @housekeeping_staff = @account_users.joins(:roles).where(roles: { name: 'housekeeping' })
    
    # Get pending invitations
    @pending_invitations = User.invitation_not_accepted.joins(:account_users).where(account_users: { account_id: current_tenant.id })
  end
  
  def new
    @account_user = current_tenant.account_users.build
    authorize @account_user
  end
  
  def create
    authorize AccountUser
    
    user_type = params[:user_type]
    role = params[:role]
    
    if role.blank?
      redirect_to new_account_user_path, alert: 'Role is required.' and return
    end
    
    if user_type == 'ai_agent'
      # Create AI Agent
      agent_name = params[:agent_name]&.strip
      agent_description = params[:agent_description]&.strip
      
      if agent_name.blank?
        redirect_to new_account_user_path, alert: 'Agent name is required.' and return
      end
      
      # Generate email for AI agent based on agent name and account subdomain
      agent_email = "#{agent_name.parameterize}@#{current_tenant.subdomain}.ai"
      
      # Check if an AI agent with this email already exists
      existing_user = User.find_by(email: agent_email)
      if existing_user
        redirect_to new_account_user_path, alert: 'An AI agent with this name already exists. Please choose a different name.' and return
      end
      
      # Create the AI agent user (no invitation)
      user = User.new(
        email: agent_email,
        first_name: agent_name,
        last_name: 'Agent',
        is_ai_agent: true,
        password: SecureRandom.hex(32) # Random password that will never be used
      )
      
      # Skip email confirmation and other Devise callbacks for AI agents
      user.skip_confirmation! if user.respond_to?(:skip_confirmation!)
      
      if user.save
        # Create account_user association with API key generation
        @account_user = current_tenant.account_users.create!(user: user)
        @account_user.add_role(role.to_sym)
        
        # Store description if provided (you might want to add a description field to User or AccountUser)
        # For now, we'll just use it in the success message
        
        redirect_to account_users_path, notice: "AI Agent '#{agent_name}' has been created successfully. API key is available in the team members list."
      else
        redirect_to new_account_user_path, alert: "Failed to create AI agent: #{user.errors.full_messages.join(', ')}"
      end
    else
      # Original human user creation logic
      email = params[:email]&.strip&.downcase
      first_name = params[:first_name]&.strip
      last_name = params[:last_name]&.strip
      
      if email.blank?
        redirect_to new_account_user_path, alert: 'Email is required.' and return
      end
      
      # Check if user already exists
      user = User.find_by(email: email)
      
      if user
        # Check if user is already part of this account
        existing_account_user = current_tenant.account_users.find_by(user: user)
        
        if existing_account_user
          redirect_to account_users_path, alert: 'This user is already a member of this account.' and return
        else
          # Add existing user to account
          @account_user = current_tenant.account_users.build(user: user)
          
          if @account_user.save
            @account_user.add_role(role.to_sym)
            redirect_to account_users_path, notice: "#{user.full_name} has been added to the account."
          else
            redirect_to new_account_user_path, alert: 'Failed to add user to account.'
          end
        end
      else
        # Invite new user
        user = User.invite!(
          email: email,
          first_name: first_name || email.split('@').first.capitalize,
          last_name: last_name || ''
        ) do |u|
          u.skip_invitation = false
        end
        
        if user.persisted?
          # Create account_user association
          @account_user = current_tenant.account_users.create!(user: user)
          @account_user.add_role(role.to_sym)
          
          redirect_to account_users_path, notice: "Invitation sent to #{email}. They will be added as #{role.humanize} once they accept."
        else
          redirect_to new_account_user_path, alert: "Failed to send invitation: #{user.errors.full_messages.join(', ')}"
        end
      end
    end
  end
  
  def edit
    authorize @account_user
    @current_role = @account_user.roles.first&.name || 'none'
  end
  
  def update
    authorize @account_user
    
    new_role = params[:role]
    
    if new_role.present? && AccountUser::PRIORITY_1_ROLES.include?(new_role)
      # Remove all existing roles and add the new one
      @account_user.roles.destroy_all
      @account_user.add_role(new_role.to_sym)
      
      redirect_to account_users_path, notice: "#{@account_user.user.full_name}'s role has been updated to #{new_role.humanize}."
    else
      redirect_to edit_account_user_path(@account_user), alert: 'Invalid role selected.'
    end
  end
  
  def destroy
    authorize @account_user
    
    if @account_user.user_id == current_user.id
      redirect_to account_users_path, alert: 'You cannot remove yourself from the account.'
    elsif @account_user.owner? && @account_user.account.account_users.joins(:roles).where(roles: { name: 'owner' }).count == 1
      redirect_to account_users_path, alert: 'Cannot remove the last owner. Please assign another owner first.'
    else
      user_name = @account_user.user.full_name
      @account_user.destroy
      redirect_to account_users_path, notice: "#{user_name} has been removed from the account."
    end
  end
  
  private
  
  def set_account_user
    @account_user = current_tenant.account_users.find(params[:id])
  end
end