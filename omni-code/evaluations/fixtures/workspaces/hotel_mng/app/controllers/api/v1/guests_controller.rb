class Api::V1::GuestsController < Api::V1::BaseController
  before_action :set_guest, only: [:show, :update, :destroy, :update_vip_status]
  
  # GET /api/v1/guests
  def index
    authorize Guest
    
    guests = policy_scope(Guest)
    guests = filter_guests(guests)
    
    @pagy, @guests = pagy(guests.order(:last_name, :first_name))
    
    render json: {
      data: GuestBlueprint.render_as_hash(@guests),
      meta: pagination_meta(@pagy)
    }
  end
  
  # GET /api/v1/guests/:id
  def show
    authorize @guest
    
    render json: {
      data: GuestBlueprint.render_as_hash(@guest)
    }
  end
  
  # POST /api/v1/guests
  def create
    @guest = Guest.new(guest_params)
    authorize @guest
    
    if @guest.save
      render json: {
        data: GuestBlueprint.render_as_hash(@guest)
      }, status: :created
    else
      handle_validation_error(ActiveRecord::RecordInvalid.new(@guest))
    end
  end
  
  # PATCH/PUT /api/v1/guests/:id
  def update
    authorize @guest
    
    if @guest.update(guest_params)
      render json: {
        data: GuestBlueprint.render_as_hash(@guest)
      }
    else
      handle_validation_error(ActiveRecord::RecordInvalid.new(@guest))
    end
  end
  
  # DELETE /api/v1/guests/:id
  def destroy
    authorize @guest
    
    # Check if guest has any reservations
    if @guest.reservations.exists?
      render json: {
        error: 'Conflict',
        message: 'Cannot delete guest with existing reservations'
      }, status: :conflict
    else
      @guest.destroy
      head :no_content
    end
  end
  
  # PATCH /api/v1/guests/:id/vip_status
  def update_vip_status
    authorize @guest, :update?
    
    unless params[:vip_status].present?
      render json: {
        error: 'Bad Request',
        message: 'VIP status parameter is required'
      }, status: :bad_request
      return
    end
    
    if @guest.update(vip_status: params[:vip_status])
      render json: {
        data: GuestBlueprint.render_as_hash(@guest)
      }
    else
      handle_validation_error(ActiveRecord::RecordInvalid.new(@guest))
    end
  end
  
  private
  
  def set_guest
    @guest = Guest.find(params[:id])
  rescue ActiveRecord::RecordNotFound
    render_not_found('Guest')
  end
  
  def filter_guests(scope)
    if params[:search].present?
      search_term = "%#{params[:search]}%"
      scope = scope.where("first_name ILIKE ? OR last_name ILIKE ? OR email ILIKE ?", 
                         search_term, search_term, search_term)
    end
    
    scope = scope.where(vip_status: params[:vip_status]) if params[:vip_status].present?
    scope = scope.where(nationality: params[:nationality]) if params[:nationality].present?
    
    scope
  end
  
  def guest_params
    params.permit(
      :first_name, :last_name, :email, :phone, :date_of_birth,
      :nationality, :passport_number, :id_number, :address, :city,
      :state, :country, :postal_code, :vip_status, :notes
    )
  end
end