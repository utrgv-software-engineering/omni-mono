class Api::V1::CancellationPoliciesController < Api::V1::BaseController
  before_action :set_cancellation_policy, only: [:show, :update, :destroy, :make_default]

  # GET /api/v1/cancellation_policies
  # GET /api/v1/hotels/:hotel_id/cancellation_policies
  def index
    authorize CancellationPolicy
    
    if params[:hotel_id].present?
      @hotel = @current_account.hotels.find(params[:hotel_id])
      authorize @hotel, :show?
      # Get both account-wide and hotel-specific policies
      policies = CancellationPolicy.where(
        account: @current_account
      ).where(
        hotel_id: [nil, @hotel.id]
      )
    else
      policies = policy_scope(@current_account.cancellation_policies)
      
      # Filter by scope if provided
      if params[:scope] == 'account_wide'
        policies = policies.account_wide
      elsif params[:scope] == 'hotel_specific'
        policies = policies.hotel_specific
      end
    end
    
    # Apply type filter if provided
    if params[:cancellation_type].present?
      policies = policies.where(cancellation_type: params[:cancellation_type])
    end
    
    # Apply refundable filter if provided
    if params[:refundable].present?
      policies = params[:refundable] == 'true' ? policies.refundable : policies.where(is_refundable: false)
    end
    
    @pagy, @cancellation_policies = pagy(policies.order(:hotel_id, :name))
    
    render json: {
      data: CancellationPolicyBlueprint.render_as_hash(@cancellation_policies),
      meta: pagination_meta(@pagy)
    }
  end

  # GET /api/v1/cancellation_policies/:id
  def show
    authorize @cancellation_policy
    
    render json: {
      data: CancellationPolicyBlueprint.render_as_hash(@cancellation_policy)
    }
  end

  # POST /api/v1/cancellation_policies
  # POST /api/v1/hotels/:hotel_id/cancellation_policies
  def create
    if params[:hotel_id].present?
      @hotel = @current_account.hotels.find(params[:hotel_id])
      authorize @hotel, :update?
      @cancellation_policy = @current_account.cancellation_policies.build(
        cancellation_policy_params.merge(hotel_id: @hotel.id)
      )
    else
      @cancellation_policy = @current_account.cancellation_policies.build(cancellation_policy_params)
    end
    
    authorize @cancellation_policy
    
    if @cancellation_policy.save
      render json: {
        data: CancellationPolicyBlueprint.render_as_hash(@cancellation_policy)
      }, status: :created
    else
      handle_validation_error(ActiveRecord::RecordInvalid.new(@cancellation_policy))
    end
  end

  # PATCH/PUT /api/v1/cancellation_policies/:id
  def update
    authorize @cancellation_policy
    
    if @cancellation_policy.update(cancellation_policy_params)
      render json: {
        data: CancellationPolicyBlueprint.render_as_hash(@cancellation_policy)
      }
    else
      handle_validation_error(ActiveRecord::RecordInvalid.new(@cancellation_policy))
    end
  end

  # DELETE /api/v1/cancellation_policies/:id
  def destroy
    authorize @cancellation_policy
    
    # Check if policy is in use (when rate_plans are implemented)
    # if @cancellation_policy.rate_plans.exists?
    #   render json: {
    #     error: 'Conflict',
    #     message: 'Cannot delete cancellation policy that is in use'
    #   }, status: :conflict
    # else
      @cancellation_policy.destroy
      head :no_content
    # end
  end

  # POST /api/v1/cancellation_policies/:id/make_default
  def make_default
    authorize @cancellation_policy
    
    @cancellation_policy.make_default!
    render json: {
      data: CancellationPolicyBlueprint.render_as_hash(@cancellation_policy)
    }
  end

  # POST /api/v1/cancellation_policies/calculate_fee
  def calculate_fee
    authorize CancellationPolicy, :index?
    
    policy = @current_account.cancellation_policies.find(params[:policy_id])
    reservation = Reservation.find(params[:reservation_id])
    cancellation_date = params[:cancellation_date] ? Date.parse(params[:cancellation_date]) : Date.current
    
    fee = policy.calculate_cancellation_fee(reservation, cancellation_date)
    no_show_fee = policy.calculate_no_show_fee(reservation)
    
    render json: {
      data: {
        cancellation_fee: fee.to_s,
        no_show_fee: no_show_fee.to_s,
        policy_type: policy.cancellation_type,
        is_refundable: policy.is_refundable
      }
    }
  rescue ActiveRecord::RecordNotFound => e
    render_not_found(e.model || 'Resource')
  end

  private

  def set_cancellation_policy
    @cancellation_policy = @current_account.cancellation_policies.find(params[:id])
  rescue ActiveRecord::RecordNotFound
    render_not_found('Cancellation Policy')
  end

  def cancellation_policy_params
    params.permit(
      :name, :code, :cancellation_type, :description, :is_refundable,
      :free_cancellation_hours, :penalty_percentage, :penalty_nights, 
      :no_show_penalty, :is_default
    )
  end
end