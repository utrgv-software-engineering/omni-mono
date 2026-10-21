class Api::V1::HotelAmenitiesController < Api::V1::BaseController
  before_action :set_hotel
  before_action :set_hotel_amenity, only: [:show, :update, :destroy]

  # GET /api/v1/hotels/:hotel_id/amenities
  def index
    authorize @hotel, :show?
    hotel_amenities = @hotel.hotel_amenities.includes(:amenity)
    
    # Apply availability filter if provided
    if params[:available].present?
      hotel_amenities = params[:available] == 'true' ? hotel_amenities.available : hotel_amenities.unscoped.unavailable
    end
    
    # Apply free/paid filter if provided
    if params[:free].present?
      hotel_amenities = params[:free] == 'true' ? hotel_amenities.free : hotel_amenities.paid
    end
    
    @pagy, @hotel_amenities = pagy(hotel_amenities)
    
    render json: {
      data: HotelAmenityBlueprint.render_as_hash(@hotel_amenities),
      meta: pagination_meta(@pagy)
    }
  end

  # GET /api/v1/hotels/:hotel_id/amenities/:id
  def show
    authorize @hotel, :show?
    
    render json: {
      data: HotelAmenityBlueprint.render_as_hash(@hotel_amenity)
    }
  end

  # POST /api/v1/hotels/:hotel_id/amenities
  def create
    authorize @hotel, :update?
    @hotel_amenity = @hotel.hotel_amenities.build(hotel_amenity_params)
    
    if @hotel_amenity.save
      render json: {
        data: HotelAmenityBlueprint.render_as_hash(@hotel_amenity)
      }, status: :created
    else
      handle_validation_error(ActiveRecord::RecordInvalid.new(@hotel_amenity))
    end
  end

  # PATCH/PUT /api/v1/hotels/:hotel_id/amenities/:id
  def update
    authorize @hotel, :update?
    
    if @hotel_amenity.update(hotel_amenity_params)
      render json: {
        data: HotelAmenityBlueprint.render_as_hash(@hotel_amenity)
      }
    else
      handle_validation_error(ActiveRecord::RecordInvalid.new(@hotel_amenity))
    end
  end

  # DELETE /api/v1/hotels/:hotel_id/amenities/:id
  def destroy
    authorize @hotel, :update?
    
    @hotel_amenity.destroy
    head :no_content
  end

  private

  def set_hotel
    @hotel = @current_account.hotels.find(params[:hotel_id])
  rescue ActiveRecord::RecordNotFound
    render_not_found('Hotel')
  end

  def set_hotel_amenity
    @hotel_amenity = @hotel.hotel_amenities.unscoped.find(params[:id])
  rescue ActiveRecord::RecordNotFound
    render_not_found('Hotel Amenity')
  end

  def hotel_amenity_params
    params.permit(:amenity_id, :is_available, :is_free, :additional_cost, :location, :operating_hours, :notes)
  end
end