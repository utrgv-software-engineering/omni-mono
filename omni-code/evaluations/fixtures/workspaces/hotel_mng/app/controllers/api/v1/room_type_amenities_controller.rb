class Api::V1::RoomTypeAmenitiesController < Api::V1::BaseController
  before_action :set_hotel
  before_action :set_room_type
  before_action :set_room_type_amenity, only: [:show, :update, :destroy]

  # GET /api/v1/hotels/:hotel_id/room_types/:room_type_id/amenities
  def index
    authorize @room_type, :show?
    room_type_amenities = @room_type.room_type_amenities.includes(:amenity)
    
    # Apply countable filter if provided
    if params[:countable].present?
      room_type_amenities = params[:countable] == 'true' ? room_type_amenities.countable : room_type_amenities.non_countable
    end
    
    @pagy, @room_type_amenities = pagy(room_type_amenities)
    
    render json: {
      data: RoomTypeAmenityBlueprint.render_as_hash(@room_type_amenities),
      meta: pagination_meta(@pagy)
    }
  end

  # GET /api/v1/hotels/:hotel_id/room_types/:room_type_id/amenities/:id
  def show
    authorize @room_type, :show?
    
    render json: {
      data: RoomTypeAmenityBlueprint.render_as_hash(@room_type_amenity)
    }
  end

  # POST /api/v1/hotels/:hotel_id/room_types/:room_type_id/amenities
  def create
    authorize @room_type, :update?
    @room_type_amenity = @room_type.room_type_amenities.build(room_type_amenity_params)
    
    if @room_type_amenity.save
      render json: {
        data: RoomTypeAmenityBlueprint.render_as_hash(@room_type_amenity)
      }, status: :created
    else
      handle_validation_error(ActiveRecord::RecordInvalid.new(@room_type_amenity))
    end
  end

  # PATCH/PUT /api/v1/hotels/:hotel_id/room_types/:room_type_id/amenities/:id
  def update
    authorize @room_type, :update?
    
    if @room_type_amenity.update(room_type_amenity_params)
      render json: {
        data: RoomTypeAmenityBlueprint.render_as_hash(@room_type_amenity)
      }
    else
      handle_validation_error(ActiveRecord::RecordInvalid.new(@room_type_amenity))
    end
  end

  # DELETE /api/v1/hotels/:hotel_id/room_types/:room_type_id/amenities/:id
  def destroy
    authorize @room_type, :update?
    
    @room_type_amenity.destroy
    head :no_content
  end

  private

  def set_hotel
    @hotel = @current_account.hotels.find(params[:hotel_id])
  rescue ActiveRecord::RecordNotFound
    render_not_found('Hotel')
  end

  def set_room_type
    @room_type = @hotel.room_types.find(params[:room_type_id])
  rescue ActiveRecord::RecordNotFound
    render_not_found('Room Type')
  end

  def set_room_type_amenity
    @room_type_amenity = @room_type.room_type_amenities.find(params[:id])
  rescue ActiveRecord::RecordNotFound
    render_not_found('Room Type Amenity')
  end

  def room_type_amenity_params
    params.permit(:amenity_id, :quantity)
  end
end