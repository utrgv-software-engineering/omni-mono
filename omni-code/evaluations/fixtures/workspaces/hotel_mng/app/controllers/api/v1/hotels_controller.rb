class Api::V1::HotelsController < Api::V1::BaseController
  before_action :set_hotel, only: [:show, :update, :destroy]
  
  # GET /api/v1/hotels
  def index
    authorize Hotel
    hotels = policy_scope(@current_account.hotels)
    
    # Apply search filter if provided
    if params[:search].present?
      hotels = hotels.where("name ILIKE ?", "%#{params[:search]}%")
    end
    
    @pagy, @hotels = pagy(hotels.order(:name))
    
    render json: {
      data: HotelBlueprint.render_as_hash(@hotels),
      meta: pagination_meta(@pagy)
    }
  end
  
  # GET /api/v1/hotels/:id
  def show
    authorize @current_hotel, :show?
    
    render json: {
      data: render_hotel_with_includes(@current_hotel)
    }
  end
  
  # POST /api/v1/hotels
  def create
    @hotel = @current_account.hotels.build(hotel_params)
    authorize @hotel
    
    if @hotel.save
      render json: {
        data: HotelBlueprint.render_as_hash(@hotel)
      }, status: :created
    else
      handle_validation_error(ActiveRecord::RecordInvalid.new(@hotel))
    end
  end
  
  # PATCH/PUT /api/v1/hotels/:id
  def update
    authorize @current_hotel
    
    if @current_hotel.update(hotel_params)
      render json: {
        data: HotelBlueprint.render_as_hash(@current_hotel)
      }
    else
      handle_validation_error(ActiveRecord::RecordInvalid.new(@current_hotel))
    end
  end
  
  # DELETE /api/v1/hotels/:id
  def destroy
    authorize @current_hotel
    
    # Check if hotel has any reservations
    if @current_hotel.reservations.exists?
      render json: {
        error: 'Conflict',
        message: 'Cannot delete hotel with existing reservations'
      }, status: :conflict
    else
      @current_hotel.destroy
      head :no_content
    end
  end
  
  private
  
  def set_hotel
    @current_hotel = @current_account.hotels.find(params[:id])
  rescue ActiveRecord::RecordNotFound
    render_not_found('Hotel')
  end
  
  def hotel_params
    params.permit(
      :name, :subdomain, :address, :city, :state, :country, :postal_code,
      :phone, :email, :website, :description, :check_in_time, :check_out_time,
      :timezone, :star_rating
    )
  end
  
  def render_hotel_with_includes(hotel)
    # Parse the include parameter to determine which associations to include
    # Supports: ?include=amenities,policies,services or ?include=all
    includes = params[:include]&.split(',')&.map(&:strip) || []
    
    # Map include names to blueprint views
    if includes.include?('all') || includes.include?('detailed')
      # Return everything
      HotelBlueprint.render_as_hash(hotel, view: :detailed)
    elsif includes.any?
      # Build the appropriate view based on includes
      view = determine_view_from_includes(includes)
      HotelBlueprint.render_as_hash(hotel, view: view)
    else
      # Default view with basic fields only
      HotelBlueprint.render_as_hash(hotel)
    end
  end
  
  def determine_view_from_includes(includes)
    # This method determines which blueprint view to use based on requested includes
    # Maps specific combinations to their corresponding views
    
    sorted_includes = includes.sort
    
    case sorted_includes
    when ['amenities']
      :with_amenities
    when ['policies']
      :with_policies  
    when ['services']
      :with_services
    when ['amenities', 'services']
      :with_amenities_and_services
    when ['amenities', 'policies']
      :with_amenities_and_policies
    when ['policies', 'services']
      :with_policies_and_services
    when ['amenities', 'policies', 'services']
      :detailed
    else
      # Default to detailed view for unknown combinations
      :detailed
    end
  end
end