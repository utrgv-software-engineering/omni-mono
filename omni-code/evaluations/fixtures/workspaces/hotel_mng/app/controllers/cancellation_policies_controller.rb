# frozen_string_literal: true

class CancellationPoliciesController < StaffController
  before_action :set_cancellation_policy, only: [:show, :edit, :update, :destroy]
  
  def index
    @cancellation_policies = policy_scope(CancellationPolicy)
    
    # Apply filter if present
    if params[:filter] == 'account_wide'
      @cancellation_policies = @cancellation_policies.account_wide
    elsif params[:filter] == 'hotel_specific'
      @cancellation_policies = @cancellation_policies.hotel_specific
    end
    
    authorize CancellationPolicy
  end
  
  def show
    authorize @cancellation_policy
  end
  
  def new
    @cancellation_policy = current_tenant.cancellation_policies.build
    authorize @cancellation_policy
  end
  
  def create
    @cancellation_policy = current_tenant.cancellation_policies.build(cancellation_policy_params)
    authorize @cancellation_policy
    
    if @cancellation_policy.save
      redirect_to @cancellation_policy, notice: 'Cancellation policy was successfully created.'
    else
      render :new, status: :unprocessable_content
    end
  end
  
  def edit
    authorize @cancellation_policy
  end
  
  def update
    authorize @cancellation_policy
    if @cancellation_policy.update(cancellation_policy_params)
      redirect_to @cancellation_policy, notice: 'Cancellation policy was successfully updated.'
    else
      render :edit, status: :unprocessable_content
    end
  end
  
  def destroy
    authorize @cancellation_policy
    @cancellation_policy.destroy
    redirect_to cancellation_policies_path, notice: 'Cancellation policy was successfully destroyed.'
  end
  
  private
  
  def set_cancellation_policy
    @cancellation_policy = CancellationPolicy.find(params[:id])
  end
  
  def cancellation_policy_params
    params.require(:cancellation_policy).permit(
      :name, :code, :cancellation_type, :hotel_id, :free_cancellation_hours, 
      :penalty_percentage, :penalty_nights, :no_show_penalty, :description, 
      :is_default, :is_refundable
    )
  end
end