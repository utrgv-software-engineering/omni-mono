class Api::V1::HotelPoliciesController < Api::V1::BaseController
  before_action :set_hotel
  before_action :set_hotel_policy, only: [:show, :update, :destroy]

  # GET /api/v1/hotels/:hotel_id/policies
  def index
    authorize @hotel, :show?
    hotel_policies = @hotel.hotel_policies
    
    # Apply policy type filter if provided
    if params[:policy_type].present?
      hotel_policies = hotel_policies.by_type(params[:policy_type])
    end
    
    # Apply active filter if provided
    if params[:active].present?
      hotel_policies = params[:active] == 'true' ? hotel_policies.active : hotel_policies.where(is_active: false)
    else
      hotel_policies = hotel_policies.active
    end
    
    hotel_policies = hotel_policies.ordered
    @pagy, @hotel_policies = pagy(hotel_policies)
    
    render json: {
      data: HotelPolicyBlueprint.render_as_hash(@hotel_policies),
      meta: pagination_meta(@pagy)
    }
  end

  # GET /api/v1/hotels/:hotel_id/policies/:id
  def show
    authorize @hotel, :show?
    
    render json: {
      data: HotelPolicyBlueprint.render_as_hash(@hotel_policy)
    }
  end

  # POST /api/v1/hotels/:hotel_id/policies
  def create
    authorize @hotel, :update?
    @hotel_policy = @hotel.hotel_policies.build(hotel_policy_params)
    
    if @hotel_policy.save
      render json: {
        data: HotelPolicyBlueprint.render_as_hash(@hotel_policy)
      }, status: :created
    else
      handle_validation_error(ActiveRecord::RecordInvalid.new(@hotel_policy))
    end
  end

  # PATCH/PUT /api/v1/hotels/:hotel_id/policies/:id
  def update
    authorize @hotel, :update?
    
    if @hotel_policy.update(hotel_policy_params)
      render json: {
        data: HotelPolicyBlueprint.render_as_hash(@hotel_policy)
      }
    else
      handle_validation_error(ActiveRecord::RecordInvalid.new(@hotel_policy))
    end
  end

  # DELETE /api/v1/hotels/:hotel_id/policies/:id
  def destroy
    authorize @hotel, :update?
    
    @hotel_policy.destroy
    head :no_content
  end

  # POST /api/v1/hotels/:hotel_id/policies/:id/activate
  def activate
    authorize @hotel, :update?
    set_hotel_policy
    return if performed?

    @hotel_policy.activate!
    render json: {
      data: HotelPolicyBlueprint.render_as_hash(@hotel_policy)
    }
  end

  # POST /api/v1/hotels/:hotel_id/policies/:id/deactivate
  def deactivate
    authorize @hotel, :update?
    set_hotel_policy
    return if performed?

    @hotel_policy.deactivate!
    render json: {
      data: HotelPolicyBlueprint.render_as_hash(@hotel_policy)
    }
  end

  private

  def set_hotel
    @hotel = @current_account.hotels.find(params[:hotel_id])
  rescue ActiveRecord::RecordNotFound
    render_not_found('Hotel')
  end

  def set_hotel_policy
    @hotel_policy = @hotel.hotel_policies.find(params[:id])
  rescue ActiveRecord::RecordNotFound
    render_not_found('Hotel Policy')
  end

  def hotel_policy_params
    params.permit(:policy_type, :title, :description, :sort_order, :is_active)
  end
end